"""
Indicator engine - Python port of SVMKR_UT_HMA_ORB_Session_Managed_v4.pine

Replicates bar-by-bar:
  - UT Bot ATR trailing stop + pos (GREEN/RED)
  - HMA + chop filter
  - ORB (09:15 IST, configurable duration)
  - Session gates: ORB block, late block, EOD exit
  - Gap-day adaptation (ATR override + ORB extension)
  - Flip signals (rawBuy/rawSell while in opposite position)
  - Day continuation re-entry
"""

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class BarData:
    timestamp: datetime
    open:  float
    high:  float
    low:   float
    close: float
    volume: float


@dataclass
class SignalResult:
    # UT Bot
    pos:       int   = 0    # 1=GREEN, -1=RED, 0=neutral
    raw_buy:   bool  = False
    raw_sell:  bool  = False
    xatr_stop: float = 0.0
    # Filtered signals
    buy_final:       bool = False
    sell_final:      bool = False
    flip_buy_final:  bool = False
    flip_sell_final: bool = False
    cont_long:       bool = False
    cont_short:      bool = False
    eod_exit_fired:  bool = False
    # Position tracker
    live_pos: int = 0
    # Auxiliary
    is_large_gap_day: bool  = False
    orb_high: Optional[float] = None
    orb_low:  Optional[float] = None
    orb_formed: bool = False
    hma_val:    float = 0.0
    adverse_gap: bool = False
    in_orb_period: bool = False
    session_blocked: bool = False


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------

def _wma(values: list, period: int) -> Optional[float]:
    """Weighted Moving Average (Pine Script wma()). Returns None if insufficient data."""
    if len(values) < period:
        return None
    tail   = values[-period:]
    total  = period * (period + 1) / 2
    result = sum(v * (i + 1) for i, v in enumerate(tail))
    return result / total


def _chop_thresholds(mode: str, manual_slope: float, manual_dist: float,
                     manual_cooldown: int, auto_thresh: bool, auto_dist: bool):
    """Return (slope_threshold, dist_mult, cooldown_bars) for given chop mode."""
    if mode == "Off":
        return 0.0, 0.0, manual_cooldown
    presets = {"Light": (0.030, 0.06, 2),
               "Medium": (0.045, 0.10, 3),
               "Strict": (0.060, 0.14, 5)}
    ps, pd, pc = presets.get(mode, (0.045, 0.10, 3))
    slope_thr = ps if auto_thresh else manual_slope
    dist_mult = pd if auto_dist   else manual_dist
    cooldown  = max(manual_cooldown, pc)
    return slope_thr, dist_mult, cooldown


# ---------------------------------------------------------------------------
# Indicator state (mirrors all Pine Script `var` declarations)
# ---------------------------------------------------------------------------

class IndicatorEngine:

    def __init__(self, cfg):
        self.cfg = cfg
        self._compute_static_params()
        self._reset()

    def _compute_static_params(self):
        cfg = self.cfg
        self.slope_threshold, self.dist_mult, self.cooldown_bars = _chop_thresholds(
            cfg.CHOP_MODE, cfg.MANUAL_SLOPE_THRESHOLD, cfg.MANUAL_DISTANCE_MULT,
            cfg.COOLDOWN_BARS_MANUAL, cfg.USE_AUTO_THRESHOLD, cfg.USE_AUTO_DISTANCE)
        self.filters_enabled = cfg.ENABLE_CHOP_FILTER and cfg.CHOP_MODE != "Off"
        n = cfg.HMA_PERIOD
        self.hma_half  = round(n / 2)
        self.hma_sqn   = round(math.sqrt(n))
        self.hma_n     = n

    def _reset(self):
        cfg = self.cfg
        # Price / TR history
        self.closes  : list = []
        self.highs   : list = []
        self.lows    : list = []
        self.prev_close: Optional[float] = None

        # Wilder ATR (UT Bot)
        self._tr_buf      : list = []          # raw TR values for SMA init
        self.atr_val      : Optional[float] = None
        # Wilder ATR (chop norm)
        self._tr_norm_buf : list = []
        self.atr_norm_val : Optional[float] = None

        # Gap-day adaptation (snapshotted at EOD)
        self._gap_prev_close : Optional[float] = None
        self._gap_prev_atr   : Optional[float] = None
        self.is_large_gap_day: bool = False
        self.bars_into_day   : int  = 0

        # UT Bot state
        self.xATRTrailingStop: float = 0.0
        self.pos             : int   = 0
        self.prev_src        : Optional[float] = None
        self.prev_stop       : float = 0.0

        # HMA
        self._diff_buf: deque = deque(maxlen=self.hma_sqn)
        self.hma_val  : float = 0.0
        self.hma_prev : float = 0.0    # = hma from previous bar

        # HMA slope history
        self._hma_hist: deque = deque(maxlen=cfg.SLOPE_LEN + 1)

        # Chop filter
        self.last_signal_bar: Optional[int] = None
        self.bar_count       : int          = 0
        self.stable_bias     : int          = 0

        # ORB
        self.orb_high: Optional[float] = None
        self.orb_low : Optional[float] = None

        # Session
        self.live_pos        : int   = 0
        self.prev_day_signal : int   = 0
        self.prev_day_close  : Optional[float] = None
        self.cont_done_today : bool  = False
        self.adverse_gap_today: bool = False
        self.today_gap_pct   : float = 0.0
        self.eod_done_today  : bool  = False

        # Day tracking
        self.current_date = None

    # -----------------------------------------------------------------------
    # Internal ATR update (Wilder's RMA)
    # -----------------------------------------------------------------------

    def _update_atr(self, high: float, low: float, period: int,
                    buf: list, prev_atr: Optional[float]) -> Optional[float]:
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low,
                     abs(high - self.prev_close),
                     abs(low  - self.prev_close))
        buf.append(tr)
        if len(buf) < period:
            return None
        if len(buf) == period:
            return sum(buf) / period
        return (prev_atr * (period - 1) + tr) / period

    # -----------------------------------------------------------------------
    # Main bar processor
    # -----------------------------------------------------------------------

    def process_bar(self, bar: BarData) -> SignalResult:
        cfg  = self.cfg
        result = SignalResult()

        dt       = bar.timestamp
        bar_date = dt.date()
        bar_h    = dt.hour
        bar_m    = dt.minute
        bar_tmin = bar_h * 60 + bar_m

        # ---- Day boundary ------------------------------------------------
        is_new_day = (self.current_date is not None and bar_date != self.current_date)

        # ---- Gap-day detection (before UT Bot so nLossEffective is ready) -
        if is_new_day or self.current_date is None:
            self.bars_into_day = 1
            if cfg.ENABLE_GAP_ADAPT and self._gap_prev_close is not None:
                gap_pct = abs((bar.open - self._gap_prev_close) / self._gap_prev_close * 100.0)
                self.is_large_gap_day = gap_pct >= cfg.LARGE_GAP_THRESH_PCT
            else:
                self.is_large_gap_day = False
        else:
            self.bars_into_day += 1

        # ---- ATR (UT Bot) ------------------------------------------------
        self.atr_val = self._update_atr(bar.high, bar.low,
                                        cfg.UT_ATR_PERIOD,
                                        self._tr_buf, self.atr_val)
        # ATR norm (chop filter)
        self.atr_norm_val = self._update_atr(bar.high, bar.low,
                                             cfg.ATR_NORM_LEN,
                                             self._tr_norm_buf, self.atr_norm_val)

        # Effective nLoss (Option A: use prev-day ATR on gap days)
        if (cfg.ENABLE_GAP_ADAPT and cfg.ENABLE_GAP_ATR_FIX and
                self.is_large_gap_day and
                self._gap_prev_atr is not None and
                self.bars_into_day <= cfg.GAP_ATR_WARMUP_BARS):
            base_atr = self._gap_prev_atr
        else:
            base_atr = self.atr_val if self.atr_val is not None else (bar.high - bar.low)
        nLoss = cfg.UT_KEY_VALUE * base_atr

        # ---- UT Bot trailing stop ----------------------------------------
        src       = bar.close
        prev_src  = self.prev_src if self.prev_src is not None else src
        prev_stop = self.prev_stop   # = xATRTrailingStop[1]

        if src > prev_stop and prev_src > prev_stop:
            curr_stop = max(prev_stop, src - nLoss)
        elif src < prev_stop and prev_src < prev_stop:
            curr_stop = min(prev_stop, src + nLoss)
        elif src > prev_stop:
            curr_stop = src - nLoss
        else:
            curr_stop = src + nLoss

        self.xATRTrailingStop = curr_stop

        # pos (uses prev_stop for current-bar comparison - matches Pine Script)
        if prev_src < prev_stop and src > prev_stop:
            new_pos = 1
        elif prev_src > prev_stop and src < prev_stop:
            new_pos = -1
        else:
            new_pos = self.pos
        self.pos = new_pos

        # rawBuy / rawSell (crossover uses curr_stop)
        raw_buy  = (prev_src < prev_stop) and (src > curr_stop)
        raw_sell = (prev_src > prev_stop) and (src < curr_stop)

        # ---- HMA ---------------------------------------------------------
        self.closes.append(bar.close)
        closes = self.closes
        n, half_n, sqn = self.hma_n, self.hma_half, self.hma_sqn

        if len(closes) >= n:
            n2ma_c = _wma(closes, half_n)
            nma_c  = _wma(closes, n)
            if n2ma_c is not None and nma_c is not None:
                diff = 2 * n2ma_c - nma_c
                self._diff_buf.append(diff)

                if len(self._diff_buf) >= sqn:
                    prev_hma      = self.hma_val
                    self.hma_val  = _wma(list(self._diff_buf), sqn)
                    self.hma_prev = prev_hma  # hma[1] = last bar's HMA

        self._hma_hist.append(self.hma_val)

        # ---- Chop filter -------------------------------------------------
        atr_n = self.atr_norm_val if self.atr_norm_val else 1e-6

        # HMA slope (hma - hma[slopeLen])
        if len(self._hma_hist) > cfg.SLOPE_LEN:
            hma_slope = self.hma_val - list(self._hma_hist)[0]
        else:
            hma_slope = 0.0

        hma_slope_norm = abs(hma_slope) / max(atr_n, 1e-10)
        hma_slope_bull = hma_slope > 0 and hma_slope_norm >= self.slope_threshold
        hma_slope_bear = hma_slope < 0 and hma_slope_norm >= self.slope_threshold
        hma_trend_bull = self.hma_val > self.hma_prev
        hma_trend_bear = self.hma_val < self.hma_prev

        dist_from_hma = abs(bar.close - self.hma_val)
        dist_ok       = dist_from_hma >= atr_n * self.dist_mult

        self.bar_count += 1
        cooldown_ok = (self.last_signal_bar is None or
                       (self.bar_count - self.last_signal_bar) > self.cooldown_bars)

        fe = self.filters_enabled

        buy_hma_trend_ok  = not fe or not cfg.USE_HMA_TREND_FILTER or hma_trend_bull
        sell_hma_trend_ok = not fe or not cfg.USE_HMA_TREND_FILTER or hma_trend_bear
        buy_slope_ok      = not fe or not cfg.USE_HMA_SLOPE_FILTER or hma_slope_bull
        sell_slope_ok     = not fe or not cfg.USE_HMA_SLOPE_FILTER or hma_slope_bear
        buy_dist_ok       = not fe or not cfg.USE_DISTANCE_FILTER  or dist_ok
        sell_dist_ok      = not fe or not cfg.USE_DISTANCE_FILTER  or dist_ok
        buy_cool_ok       = not fe or not cfg.USE_COOLDOWN_FILTER  or cooldown_ok
        sell_cool_ok      = not fe or not cfg.USE_COOLDOWN_FILTER  or cooldown_ok

        buy  = raw_buy  and buy_hma_trend_ok  and buy_slope_ok  and buy_dist_ok  and buy_cool_ok
        sell = raw_sell and sell_hma_trend_ok and sell_slope_ok and sell_dist_ok and sell_cool_ok

        if buy or sell:
            self.last_signal_bar = self.bar_count

        # Stable bias (for display only)
        if hma_slope_bull and hma_trend_bull:
            self.stable_bias = 1
        elif hma_slope_bear and hma_trend_bear:
            self.stable_bias = -1

        # ---- Session & time gates ----------------------------------------
        # ORB: base duration + gap extension (Option B)
        orb_base = cfg.ORB_DURATION_MINS
        if cfg.ENABLE_GAP_ADAPT and cfg.ENABLE_GAP_ORB_EXTEND and self.is_large_gap_day:
            effective_orb_mins = orb_base + cfg.GAP_EXTRA_ORB_MINS
        else:
            effective_orb_mins = orb_base
        orb_end_min  = 15 + effective_orb_mins         # e.g. 25 for 10-min ORB
        orb_end_tmin = 9 * 60 + orb_end_min            # e.g. 565

        eod_tmin  = cfg.EOD_EXIT_HOUR  * 60 + cfg.EOD_EXIT_MIN
        late_tmin = cfg.LATE_BLOCK_HOUR * 60 + cfg.LATE_BLOCK_MIN
        cont_end_tmin = orb_end_tmin + cfg.CONT_WINDOW_MINS

        in_orb_period = (bar_h == 9) and (15 <= bar_m < orb_end_min)
        in_cont_window = orb_end_tmin <= bar_tmin < cont_end_tmin
        is_eod_time    = bar_tmin >= eod_tmin
        is_late_block  = cfg.ENABLE_LATE_BLOCK and bar_tmin >= late_tmin

        # ---- Day-start: reset flags & compute gap ------------------------
        if is_new_day:
            self.cont_done_today  = False
            self.eod_done_today   = False
            if self.prev_day_close is not None and cfg.ENABLE_GAP_FILTER:
                self.today_gap_pct     = (bar.open - self.prev_day_close) / self.prev_day_close * 100.0
                self.adverse_gap_today = (
                    (self.prev_day_signal == 1  and self.today_gap_pct < -cfg.GAP_FILTER_PCT) or
                    (self.prev_day_signal == -1 and self.today_gap_pct >  cfg.GAP_FILTER_PCT)
                )
            else:
                self.today_gap_pct     = 0.0
                self.adverse_gap_today = False

        # ---- ORB tracking ------------------------------------------------
        if is_new_day or self.current_date is None:
            self.orb_high = bar.high
            self.orb_low  = bar.low
        elif in_orb_period:
            if self.orb_high is not None:
                self.orb_high = max(self.orb_high, bar.high)
                self.orb_low  = min(self.orb_low,  bar.low)
            else:
                self.orb_high = bar.high
                self.orb_low  = bar.low

        orb_formed = (self.orb_high is not None) and (bar_tmin >= orb_end_tmin)

        # ---- EOD exit ----------------------------------------------------
        is_eod_bar   = is_eod_time and not self.eod_done_today
        eod_exit_fired = False
        if is_eod_bar and cfg.ENABLE_EOD_EXIT:
            self.prev_day_signal  = self.live_pos
            self.prev_day_close   = bar.close
            self.live_pos         = 0
            eod_exit_fired        = True
            self.eod_done_today   = True
            # Snapshot for next-day gap detection
            self._gap_prev_close  = bar.close
            self._gap_prev_atr    = self.atr_val if self.atr_val else base_atr

        # ---- Session gate (block entries) --------------------------------
        session_blocked = is_late_block or is_eod_time or in_orb_period

        # ORB gate: only applies when flat; reversals bypass it
        orb_buy_ok  = (self.live_pos != 0 or not cfg.USE_ORB_GATE or
                       not orb_formed or bar.close > self.orb_high)
        orb_sell_ok = (self.live_pos != 0 or not cfg.USE_ORB_GATE or
                       not orb_formed or bar.close < self.orb_low)

        buy_final  = buy  and not session_blocked and orb_buy_ok
        sell_final = sell and not session_blocked and orb_sell_ok

        # ---- Flip signals ------------------------------------------------
        flip_buy_final  = (self.live_pos == -1 and raw_buy  and orb_formed and not session_blocked)
        flip_sell_final = (self.live_pos ==  1 and raw_sell and orb_formed and not session_blocked)

        # ---- Continuation re-entry --------------------------------------
        cont_long = (cfg.ENABLE_DAY_CONT and not self.cont_done_today and
                     in_cont_window and orb_formed and
                     self.prev_day_signal == 1 and not self.adverse_gap_today and
                     self.pos == 1 and hma_trend_bull and
                     (not cfg.USE_ORB_GATE or bar.close > self.orb_high))

        cont_short = (cfg.ENABLE_DAY_CONT and not self.cont_done_today and
                      in_cont_window and orb_formed and
                      self.prev_day_signal == -1 and not self.adverse_gap_today and
                      self.pos == -1 and hma_trend_bear and
                      (not cfg.USE_ORB_GATE or bar.close < self.orb_low))

        if cont_long or cont_short:
            self.cont_done_today = True

        # ---- Update live position ----------------------------------------
        if buy_final or cont_long or flip_buy_final:
            self.live_pos = 1
        if sell_final or cont_short or flip_sell_final:
            self.live_pos = -1

        # ---- Advance bar state -------------------------------------------
        self.prev_src   = src
        self.prev_stop  = curr_stop
        self.current_date = bar_date

        # ---- Fill result -------------------------------------------------
        result.pos             = self.pos
        result.raw_buy         = raw_buy
        result.raw_sell        = raw_sell
        result.xatr_stop       = curr_stop
        result.buy_final       = buy_final
        result.sell_final      = sell_final
        result.flip_buy_final  = flip_buy_final
        result.flip_sell_final = flip_sell_final
        result.cont_long       = cont_long
        result.cont_short      = cont_short
        result.eod_exit_fired  = eod_exit_fired
        result.live_pos        = self.live_pos
        result.is_large_gap_day = self.is_large_gap_day
        result.orb_high        = self.orb_high
        result.orb_low         = self.orb_low
        result.orb_formed      = orb_formed
        result.hma_val         = self.hma_val
        result.adverse_gap     = self.adverse_gap_today
        result.in_orb_period   = in_orb_period
        result.session_blocked = session_blocked
        return result

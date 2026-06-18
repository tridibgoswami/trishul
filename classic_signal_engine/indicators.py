"""
Indicator engine — Python port of SVMKR_UT_HMA_ORB_ChopNoADX.pine

Replicates bar-by-bar, with NO session/day-boundary concept (continuous,
always-in-market system, matching the Pine backtest engine exactly):
  - UT Bot ATR trailing stop + pos (GREEN/RED)
  - HMA + chop filter (trend / slope / distance / cooldown / range)
  - buy/sell filtered signals (cooldown counted in continuous bar_index,
    never reset across day boundaries — exactly like Pine's bar_index)

The ORB in the source script is purely a visual plot and never gates
signals, so it is intentionally NOT implemented here.
"""

import math
from collections import deque
from dataclasses import dataclass
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
    pos:       int   = 0    # 1=GREEN, -1=RED, 0=neutral
    raw_buy:   bool  = False
    raw_sell:  bool  = False
    xatr_stop: float = 0.0
    buy:       bool  = False
    sell:      bool  = False
    hma_val:   float = 0.0


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
        self.hma_half = round(n / 2)
        self.hma_sqn  = round(math.sqrt(n))
        self.hma_n    = n

    def _reset(self):
        cfg = self.cfg
        # Price / TR history
        self.closes: list = []
        self.prev_close: Optional[float] = None

        # Wilder ATR (UT Bot)
        self._tr_buf: list = []
        self.atr_val: Optional[float] = None
        # Wilder ATR (chop norm)
        self._tr_norm_buf: list = []
        self.atr_norm_val: Optional[float] = None

        # UT Bot state
        self.xATRTrailingStop: float = 0.0
        self.pos:        int   = 0
        self.prev_src:   Optional[float] = None
        self.prev_stop:  float = 0.0

        # HMA
        self._diff_buf: deque = deque(maxlen=self.hma_sqn)
        self.hma_val:  float = 0.0
        self.hma_prev: float = 0.0   # = hma from previous bar

        # HMA slope history
        self._hma_hist: deque = deque(maxlen=cfg.SLOPE_LEN + 1)

        # Range breakout (holds the rangeLookback bars *prior* to the current one)
        self._high_hist: deque = deque(maxlen=cfg.RANGE_LOOKBACK)
        self._low_hist:  deque = deque(maxlen=cfg.RANGE_LOOKBACK)

        # Chop filter — continuous bar index, never resets across days
        self.last_signal_bar: Optional[int] = None
        self.bar_count: int = 0

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
        cfg = self.cfg
        result = SignalResult()

        # ---- ATR (UT Bot) --------------------------------------------------
        self.atr_val = self._update_atr(bar.high, bar.low,
                                        cfg.UT_ATR_PERIOD,
                                        self._tr_buf, self.atr_val)
        # ATR norm (chop filter)
        self.atr_norm_val = self._update_atr(bar.high, bar.low,
                                             cfg.ATR_NORM_LEN,
                                             self._tr_norm_buf, self.atr_norm_val)

        base_atr = self.atr_val if self.atr_val is not None else (bar.high - bar.low)
        nLoss = cfg.UT_KEY_VALUE * base_atr

        # ---- UT Bot trailing stop -------------------------------------------
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

        # pos (uses prev_stop for current-bar comparison — matches Pine Script)
        if prev_src < prev_stop and src > prev_stop:
            new_pos = 1
        elif prev_src > prev_stop and src < prev_stop:
            new_pos = -1
        else:
            new_pos = self.pos
        self.pos = new_pos

        # rawBuy / rawSell (crossover uses curr_stop, matching ema1 vs xATRTrailingStop)
        raw_buy  = (prev_src <= prev_stop) and (src > curr_stop)
        raw_sell = (prev_src >= prev_stop) and (src < curr_stop)

        # ---- HMA -------------------------------------------------------------
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
                    prev_hma     = self.hma_val
                    self.hma_val = _wma(list(self._diff_buf), sqn)
                    self.hma_prev = prev_hma  # hma[1] = last bar's HMA

        self._hma_hist.append(self.hma_val)

        # ---- Chop filter -------------------------------------------------------
        atr_n = self.atr_norm_val if self.atr_norm_val else 1e-6

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

        # Strict range breakout filter (uses bars *before* this one)
        range_high_prev = max(self._high_hist) if self._high_hist else None
        range_low_prev  = min(self._low_hist)  if self._low_hist  else None
        range_buy_ok  = range_high_prev is not None and bar.close > range_high_prev
        range_sell_ok = range_low_prev  is not None and bar.close < range_low_prev

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
        buy_range_ok      = not fe or not cfg.USE_RANGE_FILTER     or range_buy_ok
        sell_range_ok     = not fe or not cfg.USE_RANGE_FILTER     or range_sell_ok

        buy  = (raw_buy  and buy_hma_trend_ok  and buy_slope_ok  and buy_dist_ok
                and buy_cool_ok  and buy_range_ok)
        sell = (raw_sell and sell_hma_trend_ok and sell_slope_ok and sell_dist_ok
                and sell_cool_ok and sell_range_ok)

        if buy or sell:
            self.last_signal_bar = self.bar_count

        # Update range history *after* using the previous values (Pine uses [1])
        self._high_hist.append(bar.high)
        self._low_hist.append(bar.low)

        # ---- Advance bar state -------------------------------------------------
        self.prev_close = bar.close
        self.prev_src    = src
        self.prev_stop   = curr_stop

        # ---- Fill result ---------------------------------------------------------
        result.pos       = self.pos
        result.raw_buy   = raw_buy
        result.raw_sell  = raw_sell
        result.xatr_stop = curr_stop
        result.buy       = buy
        result.sell      = sell
        result.hma_val   = self.hma_val
        return result

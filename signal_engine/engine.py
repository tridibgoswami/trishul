"""
Main signal engine — orchestrates warmup, replay, live loop, and EOD summary.
"""

import time
import sys
from datetime import datetime, timedelta
from typing import List

from market_utils import (IST, now_ist, today_ist, prev_trading_day,
                           is_market_open, next_bar_fetch_time, fmt_ist,
                           MARKET_OPEN, MARKET_CLOSE)
from indicators import IndicatorEngine, BarData, SignalResult
from broker_client import BrokerClient
from trade_tracker import TradeTracker


class SignalEngine:

    def __init__(self, cfg):
        self.cfg       = cfg
        self.indicator = IndicatorEngine(cfg)
        self.broker    = BrokerClient(cfg)
        self.tracker   = TradeTracker(cfg.DB_PATH)

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def run(self):
        cfg = self.cfg
        self.broker.login()

        # 1. Warmup with previous N trading days (silent)
        self._warmup()

        # 2. Replay today's bars if we started mid-session
        today_bars = self._fetch_today_bars()
        if today_bars:
            self._replay(today_bars)
        else:
            print(f"[ENGINE] Waiting for market open ({cfg.SYMBOL}) ...")

        # 3. Live loop until market close
        self._live_loop()

        # 4. EOD summary
        self._print_day_summary()

    # -----------------------------------------------------------------------
    # Warmup: feed historical bars silently to initialise indicator state
    # -----------------------------------------------------------------------

    def _warmup(self):
        cfg = self.cfg
        print(f"[WARMUP] Fetching {cfg.WARMUP_DAYS} previous trading days for indicator warmup ...")
        total_bars = 0
        for i in range(cfg.WARMUP_DAYS, 0, -1):
            day = prev_trading_day(n=i)
            try:
                bars = self.broker.fetch_day_candles(day)
            except Exception as e:
                print(f"[WARMUP] Could not fetch {day}: {e}")
                continue
            for bar in bars:
                self.indicator.process_bar(bar)
            total_bars += len(bars)
        print(f"[WARMUP] Complete — processed {total_bars} bars. Indicator state initialised.")

    # -----------------------------------------------------------------------
    # Fetch today's already-completed bars (for mid-day start)
    # -----------------------------------------------------------------------

    def _fetch_today_bars(self) -> List[BarData]:
        try:
            return self.broker.fetch_today_upto_now()
        except Exception as e:
            print(f"[ENGINE] Could not fetch today's bars: {e}")
            return []

    # -----------------------------------------------------------------------
    # Replay today's past bars (mid-day start scenario)
    # -----------------------------------------------------------------------

    def _replay(self, bars: List[BarData]):
        if not bars:
            return
        print(f"[REPLAY] Replaying {len(bars)} bars from today ({bars[0].timestamp.strftime('%H:%M')} "
              f"→ {bars[-1].timestamp.strftime('%H:%M')}) ...")
        for bar in bars:
            result = self.indicator.process_bar(bar)
            self._process_signals(bar, result, mode="REPLAY")
        print(f"[REPLAY] Done. Current live position: {self._pos_str(self.indicator.live_pos)}")

    # -----------------------------------------------------------------------
    # Live loop: runs until market close
    # -----------------------------------------------------------------------

    def _live_loop(self):
        cfg = self.cfg
        print(f"[LIVE] Entering live mode — will run until {cfg.EOD_EXIT_HOUR}:{cfg.EOD_EXIT_MIN:02d} IST")
        seen_bar_times = set()

        while True:
            now = now_ist()
            # Stop past market close
            if now.time() > MARKET_CLOSE:
                break

            # Sleep until next bar boundary + 5s buffer
            fetch_at = next_bar_fetch_time(now)
            sleep_s  = (fetch_at - now).total_seconds()
            if sleep_s > 0:
                time.sleep(sleep_s)

            # Fetch latest bar
            try:
                bar = self.broker.fetch_latest_bar()
            except Exception as e:
                print(f"[LIVE] Fetch error: {e}. Retrying next bar ...")
                continue

            if bar is None:
                continue

            bar_key = bar.timestamp.strftime("%H:%M")
            if bar_key in seen_bar_times:
                continue          # duplicate; already processed
            seen_bar_times.add(bar_key)

            result = self.indicator.process_bar(bar)
            self._process_signals(bar, result, mode="LIVE")

            # EOD exit already handled inside indicator; check for auto-stop
            now2 = now_ist()
            eod_tmin = cfg.EOD_EXIT_HOUR * 60 + cfg.EOD_EXIT_MIN
            if now2.hour * 60 + now2.minute >= eod_tmin + 15:
                print(f"[LIVE] Past EOD cutoff. Stopping live loop.")
                break

    # -----------------------------------------------------------------------
    # Signal processor: print + trade tracker update
    # -----------------------------------------------------------------------

    def _process_signals(self, bar: BarData, r: SignalResult, mode: str):
        cfg   = self.cfg
        ts    = fmt_ist(bar.timestamp)
        price = bar.close
        sym   = cfg.SYMBOL

        line_color = "GREEN" if r.pos == 1 else ("RED" if r.pos == -1 else "NEUTRAL")
        prefix = f"[{mode}] " if mode != "LIVE" else ""

        # Build action string
        actions = []
        exit_reason = None

        # --- Exits first (flip/signal closes existing position) ---
        if r.flip_buy_final and self.tracker.has_open_position():
            old_dir   = self.tracker.open_direction()
            old_entry = self.tracker.open_entry_price()
            pnl       = self.tracker.close_position(ts, price, "FLIP")
            if pnl is not None:
                actions.append(f"Exit {old_dir} @ {price:.2f} (was {old_entry:.2f}, PnL: {pnl:+.2f}pts)")
            exit_reason = "FLIP"

        elif r.flip_sell_final and self.tracker.has_open_position():
            old_dir   = self.tracker.open_direction()
            old_entry = self.tracker.open_entry_price()
            pnl       = self.tracker.close_position(ts, price, "FLIP")
            if pnl is not None:
                actions.append(f"Exit {old_dir} @ {price:.2f} (was {old_entry:.2f}, PnL: {pnl:+.2f}pts)")
            exit_reason = "FLIP"

        elif (r.buy_final or r.cont_long) and self.tracker.has_open_position():
            old_dir   = self.tracker.open_direction()
            old_entry = self.tracker.open_entry_price()
            pnl       = self.tracker.close_position(ts, price, "SIGNAL")
            if pnl is not None:
                actions.append(f"Exit {old_dir} @ {price:.2f} (was {old_entry:.2f}, PnL: {pnl:+.2f}pts)")

        elif (r.sell_final or r.cont_short) and self.tracker.has_open_position():
            old_dir   = self.tracker.open_direction()
            old_entry = self.tracker.open_entry_price()
            pnl       = self.tracker.close_position(ts, price, "SIGNAL")
            if pnl is not None:
                actions.append(f"Exit {old_dir} @ {price:.2f} (was {old_entry:.2f}, PnL: {pnl:+.2f}pts)")

        if r.eod_exit_fired and self.tracker.has_open_position():
            old_dir   = self.tracker.open_direction()
            old_entry = self.tracker.open_entry_price()
            pnl       = self.tracker.close_position(ts, price, "EOD")
            if pnl is not None:
                actions.append(f"EOD EXIT {old_dir} @ {price:.2f} (was {old_entry:.2f}, PnL: {pnl:+.2f}pts)")
            self.tracker.record_signal(bar.timestamp.date(), ts, "EOD_EXIT", price, r.live_pos)

        # --- Entries ---
        if r.buy_final and not r.cont_long:
            self.tracker.open_position(bar.timestamp.date(), ts, "LONG", price)
            actions.append(f"BUY signal activated, Entry @ {price:.2f}")
            self.tracker.record_signal(bar.timestamp.date(), ts, "BUY", price, r.live_pos)

        elif r.sell_final and not r.cont_short:
            self.tracker.open_position(bar.timestamp.date(), ts, "SHORT", price)
            actions.append(f"SELL signal activated, Entry @ {price:.2f}")
            self.tracker.record_signal(bar.timestamp.date(), ts, "SELL", price, r.live_pos)

        if r.flip_buy_final:
            self.tracker.open_position(bar.timestamp.date(), ts, "LONG", price)
            actions.append(f"FLIP B — Enter LONG @ {price:.2f}")
            self.tracker.record_signal(bar.timestamp.date(), ts, "FLIP_B", price, r.live_pos)

        elif r.flip_sell_final:
            self.tracker.open_position(bar.timestamp.date(), ts, "SHORT", price)
            actions.append(f"FLIP S — Enter SHORT @ {price:.2f}")
            self.tracker.record_signal(bar.timestamp.date(), ts, "FLIP_S", price, r.live_pos)

        if r.cont_long:
            self.tracker.open_position(bar.timestamp.date(), ts, "LONG", price)
            actions.append(f"CONT L — Continuation LONG Entry @ {price:.2f}")
            self.tracker.record_signal(bar.timestamp.date(), ts, "CONT_L", price, r.live_pos)

        elif r.cont_short:
            self.tracker.open_position(bar.timestamp.date(), ts, "SHORT", price)
            actions.append(f"CONT S — Continuation SHORT Entry @ {price:.2f}")
            self.tracker.record_signal(bar.timestamp.date(), ts, "CONT_S", price, r.live_pos)

        # --- Open position status ---
        if not actions:
            if r.live_pos == 1:
                pos_str = f"Open position: BUY  (entry {self.tracker.open_entry_price() or '?'})"
            elif r.live_pos == -1:
                pos_str = f"Open position: SELL (entry {self.tracker.open_entry_price() or '?'})"
            else:
                pos_str = "FLAT"
        else:
            pos_str = ""

        # --- Gap / ORB annotation ---
        extras = []
        if r.is_large_gap_day and r.in_orb_period:
            extras.append(f"[GAP DAY - extended ORB forming]")
        elif r.in_orb_period:
            extras.append("[ORB forming]")
        if r.adverse_gap:
            extras.append("[ADVERSE GAP - cont blocked]")

        # --- Compose final line ---
        action_str = " | ".join(actions) if actions else pos_str
        extra_str  = " " + " ".join(extras) if extras else ""

        orb_str = ""
        if r.orb_formed and r.orb_high:
            orb_str = f" ORB={r.orb_low:.0f}-{r.orb_high:.0f}"

        line = (f"{prefix}{ts}  Signal Line {line_color:<7}  {sym} price={price:<10.2f}"
                f"{orb_str}  {action_str}{extra_str}")
        print(line)

    # -----------------------------------------------------------------------
    # EOD summary
    # -----------------------------------------------------------------------

    def _print_day_summary(self):
        s = self.tracker.day_summary()
        print()
        print("=" * 65)
        print(f"  DAY SUMMARY — {today_ist()}")
        print("=" * 65)
        print(f"  Signals fired   : {len(s['signals'])}  ({', '.join(s['signals']) or 'none'})")
        print(f"  Closed trades   : {s['trades']}")
        print(f"  Wins / Losses   : {s['wins']} / {s['losses']}")
        print(f"  Net P&L (pts)   : {s['net_pnl']:+.2f}")
        if self.tracker.has_open_position():
            print(f"  NOTE: 1 position left OPEN (engine stopped before EOD exit)")
        print("=" * 65)

    # -----------------------------------------------------------------------
    # Helper
    # -----------------------------------------------------------------------

    @staticmethod
    def _pos_str(live_pos: int) -> str:
        return {1: "LONG", -1: "SHORT", 0: "FLAT"}.get(live_pos, "FLAT")

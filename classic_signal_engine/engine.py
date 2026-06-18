"""
Classic signal engine — continuous (always-in-market) flip trading model.
Orchestrates warmup, replay, live loop, and EOD summary.
"""

import time
from typing import List

from market_utils import now_ist, today_ist, prev_trading_day, next_bar_fetch_time, fmt_ist, MARKET_CLOSE
from indicators import IndicatorEngine, BarData, SignalResult
from broker_client import BrokerClient
from trade_tracker import TradeTracker


class SignalEngine:

    def __init__(self, cfg):
        self.cfg       = cfg
        self.indicator = IndicatorEngine(cfg)
        self.broker    = BrokerClient(cfg)
        self.tracker   = TradeTracker(cfg.DB_PATH)
        self._day_signals = 0

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def run(self):
        cfg = self.cfg
        self.broker.login()

        # 1. Warmup with previous N trading days (silent — builds indicator
        #    + cooldown state continuously, exactly like Pine's bar_index)
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
            self._process_signal(bar, result, mode="REPLAY")
        pos = self.tracker.open_direction() or "FLAT"
        print(f"[REPLAY] Done. Current open position: {pos}")

    # -----------------------------------------------------------------------
    # Live loop: runs until market close
    # -----------------------------------------------------------------------

    def _live_loop(self):
        cfg = self.cfg
        print(f"[LIVE] Entering live mode — will run until {MARKET_CLOSE.strftime('%H:%M')} IST")
        seen_bar_times = set()

        while True:
            now = now_ist()
            if now.time() > MARKET_CLOSE:
                break

            fetch_at = next_bar_fetch_time(now)
            sleep_s  = (fetch_at - now).total_seconds()
            if sleep_s > 0:
                time.sleep(sleep_s)

            try:
                bar = self.broker.fetch_latest_bar()
            except Exception as e:
                print(f"[LIVE] Fetch error: {e}. Retrying next bar ...")
                continue

            if bar is None:
                continue

            bar_key = bar.timestamp.strftime("%Y-%m-%d %H:%M")
            if bar_key in seen_bar_times:
                continue
            seen_bar_times.add(bar_key)

            result = self.indicator.process_bar(bar)
            self._process_signal(bar, result, mode="LIVE")

    # -----------------------------------------------------------------------
    # Signal processor: print + trade tracker update (continuous flip model)
    # -----------------------------------------------------------------------

    def _process_signal(self, bar: BarData, r: SignalResult, mode: str):
        cfg   = self.cfg
        ts    = fmt_ist(bar.timestamp)
        price = bar.close
        sym   = cfg.SYMBOL
        prefix = f"[{mode}] " if mode != "LIVE" else ""

        line_color = "GREEN" if r.pos == 1 else ("RED" if r.pos == -1 else "NEUTRAL")
        action = ""

        if r.buy or r.sell:
            self._day_signals += 1

        if r.buy and not self.tracker.has_open_position():
            self.tracker.open_position(ts, "BUY", price)
            action = f"BUY signal — Enter LONG @ {price:.2f}"

        elif r.buy and self.tracker.open_direction() == "SELL":
            pnl = self.tracker.flip(ts, "BUY", price)
            action = f"BUY signal — Exit SHORT @ {price:.2f} (PnL {pnl:+.2f}pts), Enter LONG @ {price:.2f}"

        elif r.sell and not self.tracker.has_open_position():
            self.tracker.open_position(ts, "SELL", price)
            action = f"SELL signal — Enter SHORT @ {price:.2f}"

        elif r.sell and self.tracker.open_direction() == "BUY":
            pnl = self.tracker.flip(ts, "SELL", price)
            action = f"SELL signal — Exit LONG @ {price:.2f} (PnL {pnl:+.2f}pts), Enter SHORT @ {price:.2f}"

        if not action:
            pos_dir = self.tracker.open_direction()
            action = f"Open position: {pos_dir} @ {self.tracker.open_entry_price():.2f}" if pos_dir else "FLAT"

        line = f"{prefix}{ts}  Signal Line {line_color:<7}  {sym} price={price:<10.2f}  {action}"
        print(line)

    # -----------------------------------------------------------------------
    # EOD summary
    # -----------------------------------------------------------------------

    def _print_day_summary(self):
        print()
        print("=" * 65)
        print(f"  DAY SUMMARY — {today_ist()}")
        print("=" * 65)
        print(f"  Signals fired today : {self._day_signals}")
        pos_dir = self.tracker.open_direction()
        if pos_dir:
            print(f"  Open position        : {pos_dir} @ {self.tracker.open_entry_price():.2f} (carries to next session)")
        else:
            print(f"  Open position        : FLAT")
        print("=" * 65)

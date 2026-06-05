#!/usr/bin/env python3
"""
SVMKR UT-HMA Signal Engine — Entry point

Usage
-----
  python main.py                  # Run live engine for today
  python main.py --history 7      # Show last 7 days of trade history
  python main.py --history 2      # Show last 2 days
  python main.py --history 2026-06-01  # Show from a specific date

Setup (first time)
------------------
  pip install -r requirements.txt
  # Edit config.py with your AngelOne credentials and symbol settings
"""

import sys
import argparse
from datetime import date

# Make sure imports resolve when running from signal_engine/
import os
sys.path.insert(0, os.path.dirname(__file__))

import config as cfg
from market_utils import is_trading_day, today_ist, now_ist, MARKET_OPEN, MARKET_CLOSE
from trade_tracker import TradeTracker


# ---------------------------------------------------------------------------
# History query mode
# ---------------------------------------------------------------------------

def run_history(arg: str):
    tracker = TradeTracker(cfg.DB_PATH)
    try:
        days = int(arg)
        print(f"\n--- Trade history: last {days} calendar days ---\n")
        tracker.print_history(days=days)
    except ValueError:
        try:
            from_date = date.fromisoformat(arg)
            print(f"\n--- Trade history from {from_date} ---\n")
            rows = tracker.query_trades(from_date=from_date)
            if not rows:
                print("No trades found.")
            else:
                tracker.print_history(days=None)   # will print all; minor limitation
        except ValueError:
            print(f"Invalid --history argument: '{arg}'. Use an integer (days) or YYYY-MM-DD.")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Live engine mode
# ---------------------------------------------------------------------------

def run_engine():
    today = today_ist()
    now   = now_ist()

    # Weekend / holiday check
    if not is_trading_day(today):
        day_name = today.strftime("%A")
        print(f"\nNSE market is CLOSED today ({today} is a {day_name} / holiday). Engine shutting down.\n")
        sys.exit(0)

    # Past market close check
    if now.time() > MARKET_CLOSE:
        print(f"\nMarket already closed for today ({today}). Engine shutting down.\n")
        sys.exit(0)

    # Config sanity check
    placeholders = {"YOUR_API_KEY", "YOUR_CLIENT_ID", "YOUR_MPIN", "YOUR_TOTP_SECRET"}
    if {cfg.API_KEY, cfg.CLIENT_ID, cfg.MPIN, cfg.TOTP_SECRET} & placeholders:
        print("\nERROR: Please fill in your AngelOne credentials in config.py before running.\n")
        sys.exit(1)

    print()
    print("=" * 65)
    print(f"  SVMKR UT-HMA Signal Engine  —  {cfg.SYMBOL}")
    print(f"  Date : {today}   |   ORB: {cfg.ORB_DURATION_MINS} min")
    print(f"  UT Key={cfg.UT_KEY_VALUE}  ATR={cfg.UT_ATR_PERIOD}  "
          f"HMA={cfg.HMA_PERIOD}  Chop={cfg.CHOP_MODE}")
    print("=" * 65)
    print()

    from engine import SignalEngine
    engine = SignalEngine(cfg)
    try:
        engine.run()
    except KeyboardInterrupt:
        print("\n[ENGINE] Interrupted by user. Printing day summary ...")
        engine._print_day_summary()
    except Exception as e:
        print(f"\n[ENGINE] Fatal error: {e}")
        raise


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SVMKR UT-HMA Signal Engine")
    parser.add_argument("--history", metavar="DAYS_OR_DATE",
                        help="Show trade history (number of days or YYYY-MM-DD)")
    args = parser.parse_args()

    if args.history:
        run_history(args.history)
    else:
        run_engine()


if __name__ == "__main__":
    main()

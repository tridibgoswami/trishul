#!/usr/bin/env python3
"""
SVMKR UT-HMA-ORB ChopNoADX — Classic Signal Engine (continuous flip model)

Entry point
------------
  python main.py                                # Run live engine for today
  python main.py --from 2026-01-01 --to 2026-01-05   # Trade history for a date range

Setup (first time)
-------------------
  pip install -r requirements.txt
  # Edit config.py with your AngelOne credentials and symbol settings
"""

import sys
import argparse
from datetime import date

import os
sys.path.insert(0, os.path.dirname(__file__))

import config as cfg
from market_utils import is_trading_day, today_ist, now_ist, MARKET_CLOSE
from trade_tracker import TradeTracker


# ---------------------------------------------------------------------------
# History query mode
# ---------------------------------------------------------------------------

def run_history(from_str: str, to_str: str):
    tracker = TradeTracker(cfg.DB_PATH)
    from_date = date.fromisoformat(from_str) if from_str else None
    to_date   = date.fromisoformat(to_str)   if to_str   else None
    tracker.print_history(from_date=from_date, to_date=to_date)


# ---------------------------------------------------------------------------
# Live engine mode
# ---------------------------------------------------------------------------

def run_engine():
    today = today_ist()
    now   = now_ist()

    if not is_trading_day(today):
        day_name = today.strftime("%A")
        print(f"\nNSE market is CLOSED today ({today} is a {day_name} / holiday). Engine shutting down.\n")
        sys.exit(0)

    if now.time() > MARKET_CLOSE:
        print(f"\nMarket already closed for today ({today}). Engine shutting down.\n")
        sys.exit(0)

    placeholders = {"YOUR_API_KEY", "YOUR_CLIENT_ID", "YOUR_MPIN", "YOUR_TOTP_SECRET"}
    if {cfg.API_KEY, cfg.CLIENT_ID, cfg.MPIN, cfg.TOTP_SECRET} & placeholders:
        print("\nERROR: Please fill in your AngelOne credentials in config.py before running.\n")
        sys.exit(1)

    print()
    print("=" * 65)
    print(f"  SVMKR UT-HMA-ORB ChopNoADX — Classic Signal Engine — {cfg.SYMBOL}")
    print(f"  Date : {today}")
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
    parser = argparse.ArgumentParser(description="SVMKR UT-HMA-ORB ChopNoADX Classic Signal Engine")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                        help="Start date for trade history query")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD",
                        help="End date for trade history query")
    args = parser.parse_args()

    if args.from_date or args.to_date:
        run_history(args.from_date, args.to_date)
    else:
        run_engine()


if __name__ == "__main__":
    main()

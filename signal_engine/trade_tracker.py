"""
SQLite-backed trade history.

Schema
------
trades  : one row per closed trade
signals : one row per signal event (buy/sell/flip/cont/eod)
"""

import sqlite3
import json
from datetime import date, timedelta
from typing import Optional


DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    entry_time  TEXT NOT NULL,
    exit_time   TEXT,
    direction   TEXT NOT NULL,   -- LONG | SHORT
    entry_price REAL NOT NULL,
    exit_price  REAL,
    pnl_pts     REAL,
    exit_reason TEXT             -- FLIP | SIGNAL | EOD | CONT
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    signal_type TEXT NOT NULL,   -- BUY | SELL | FLIP_B | FLIP_S | CONT_L | CONT_S | EOD_EXIT
    price       REAL NOT NULL,
    live_pos    INTEGER,         -- 1=LONG, -1=SHORT, 0=FLAT
    notes       TEXT
);
"""


class TradeTracker:

    def __init__(self, db_path: str):
        self.db_path      = db_path
        self._open_trade_id: Optional[int] = None
        self._open_direction: Optional[str] = None
        self._open_entry_price: Optional[float] = None
        self._open_entry_time: Optional[str] = None
        self._day_signals  = []      # list of signal_type strings this session
        self._day_pnl      = 0.0
        self._day_trades   = 0
        self._day_wins     = 0
        self._day_losses   = 0
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(DDL)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    # -----------------------------------------------------------------------
    # Signal recording (every event)
    # -----------------------------------------------------------------------

    def record_signal(self, trade_date: date, signal_time: str,
                      signal_type: str, price: float,
                      live_pos: int, notes: str = ""):
        self._day_signals.append(signal_type)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO signals (trade_date,signal_time,signal_type,price,live_pos,notes) "
                "VALUES (?,?,?,?,?,?)",
                (str(trade_date), signal_time, signal_type, price, live_pos, notes)
            )

    # -----------------------------------------------------------------------
    # Position management
    # -----------------------------------------------------------------------

    def open_position(self, trade_date: date, entry_time: str,
                      direction: str, entry_price: float):
        """Call when a new trade is entered."""
        if self._open_trade_id is not None:
            return   # already open; close first
        self._open_direction   = direction
        self._open_entry_price = entry_price
        self._open_entry_time  = entry_time
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO trades (trade_date,entry_time,direction,entry_price) VALUES (?,?,?,?)",
                (str(trade_date), entry_time, direction, entry_price)
            )
            self._open_trade_id = cur.lastrowid

    def close_position(self, exit_time: str, exit_price: float, exit_reason: str):
        """Call when a trade is closed."""
        if self._open_trade_id is None:
            return
        if self._open_direction == "LONG":
            pnl = exit_price - self._open_entry_price
        else:
            pnl = self._open_entry_price - exit_price

        with self._conn() as conn:
            conn.execute(
                "UPDATE trades SET exit_time=?,exit_price=?,pnl_pts=?,exit_reason=? WHERE id=?",
                (exit_time, exit_price, pnl, exit_reason, self._open_trade_id)
            )

        self._day_pnl    += pnl
        self._day_trades += 1
        if pnl > 0:
            self._day_wins += 1
        elif pnl < 0:
            self._day_losses += 1

        self._open_trade_id    = None
        self._open_direction   = None
        self._open_entry_price = None
        self._open_entry_time  = None
        return pnl

    def has_open_position(self) -> bool:
        return self._open_trade_id is not None

    def open_direction(self) -> Optional[str]:
        return self._open_direction

    def open_entry_price(self) -> Optional[float]:
        return self._open_entry_price

    # -----------------------------------------------------------------------
    # Day summary
    # -----------------------------------------------------------------------

    def day_summary(self) -> dict:
        return {
            "signals" : self._day_signals,
            "trades"  : self._day_trades,
            "wins"    : self._day_wins,
            "losses"  : self._day_losses,
            "flat"    : self._day_trades - self._day_wins - self._day_losses,
            "net_pnl" : round(self._day_pnl, 2),
        }

    # -----------------------------------------------------------------------
    # History queries
    # -----------------------------------------------------------------------

    def query_trades(self, days: int = None, from_date: date = None) -> list:
        """Return trades for the last `days` calendar days, or from a specific date."""
        with self._conn() as conn:
            if from_date:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE trade_date >= ? ORDER BY id",
                    (str(from_date),)
                ).fetchall()
            elif days:
                cutoff = date.today() - timedelta(days=days)
                rows = conn.execute(
                    "SELECT * FROM trades WHERE trade_date >= ? ORDER BY id",
                    (str(cutoff),)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return rows

    def query_signals(self, days: int = None, from_date: date = None) -> list:
        with self._conn() as conn:
            if from_date:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE trade_date >= ? ORDER BY id",
                    (str(from_date),)
                ).fetchall()
            elif days:
                cutoff = date.today() - timedelta(days=days)
                rows = conn.execute(
                    "SELECT * FROM signals WHERE trade_date >= ? ORDER BY id",
                    (str(cutoff),)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM signals ORDER BY id").fetchall()
        return rows

    def print_history(self, days: int = 7):
        """Pretty-print trade history."""
        trades = self.query_trades(days=days)
        if not trades:
            print(f"No trades found in the last {days} days.")
            return
        header = f"{'Date':<12} {'Dir':<6} {'Entry':>8} {'Exit':>8} {'PnL':>8} {'Entry Time':<8} {'Exit Time':<8} {'Reason'}"
        print(header)
        print("-" * len(header))
        total_pnl = 0.0
        for t in trades:
            # id, trade_date, entry_time, exit_time, direction, entry_price,
            # exit_price, pnl_pts, exit_reason
            _, tdate, etime, xtime, direction, eprice, xprice, pnl, reason = t
            pnl = pnl or 0.0
            total_pnl += pnl
            pnl_str = f"{pnl:+.2f}"
            xtime   = xtime or "OPEN"
            xprice  = f"{xprice:.2f}" if xprice else "-"
            reason  = reason or "-"
            print(f"{tdate:<12} {direction:<6} {eprice:>8.2f} {xprice:>8} {pnl_str:>8} "
                  f"{etime:<8} {xtime:<8} {reason}")
        print("-" * len(header))
        print(f"Total net P&L: {total_pnl:+.2f} pts  |  Trades: {len(trades)}")

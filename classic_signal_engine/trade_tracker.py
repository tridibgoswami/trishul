"""
SQLite-backed trade history for the continuous (always-in-market) flip engine.

Every closed trade is one row. The currently open position (if any) has
exit_time/exit_price/pnl_pts = NULL. entry_time is unique so warmup/replay
can be re-run safely without duplicating rows (INSERT OR IGNORE).
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional


DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_time  TEXT NOT NULL UNIQUE,
    direction   TEXT NOT NULL,   -- BUY | SELL
    entry_price REAL NOT NULL,
    exit_time   TEXT,
    exit_price  REAL,
    pnl_pts     REAL
);
"""


class TradeTracker:

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._open_id:         Optional[int]   = None
        self._open_direction:  Optional[str]   = None
        self._open_entry_price: Optional[float] = None
        self._open_entry_time: Optional[str]   = None
        self._init_db()
        self._load_open_position()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(DDL)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _load_open_position(self):
        """Resume in-memory open-position state after a restart."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, entry_time, direction, entry_price FROM trades "
                "WHERE exit_time IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            self._open_id, self._open_entry_time, self._open_direction, self._open_entry_price = row

    # -----------------------------------------------------------------------
    # Position management (always-in-market flip model)
    # -----------------------------------------------------------------------

    def open_position(self, entry_time: str, direction: str, entry_price: float):
        """Open a new position. No-op if one is already open (caller should
        close the existing one first — see flip())."""
        if self._open_id is not None:
            return
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO trades (entry_time,direction,entry_price) VALUES (?,?,?)",
                (entry_time, direction, entry_price)
            )
            if cur.rowcount == 0:
                # Already recorded (replay) — just resync in-memory state.
                row = conn.execute(
                    "SELECT id FROM trades WHERE entry_time=?", (entry_time,)
                ).fetchone()
                self._open_id = row[0] if row else None
            else:
                self._open_id = cur.lastrowid
        self._open_direction   = direction
        self._open_entry_price = entry_price
        self._open_entry_time  = entry_time

    def close_position(self, exit_time: str, exit_price: float) -> Optional[float]:
        if self._open_id is None:
            return None
        if self._open_direction == "BUY":
            pnl = exit_price - self._open_entry_price
        else:
            pnl = self._open_entry_price - exit_price

        with self._conn() as conn:
            conn.execute(
                "UPDATE trades SET exit_time=?,exit_price=?,pnl_pts=? WHERE id=?",
                (exit_time, exit_price, pnl, self._open_id)
            )

        self._open_id           = None
        self._open_direction    = None
        self._open_entry_price  = None
        self._open_entry_time   = None
        return pnl

    def flip(self, ts: str, new_direction: str, price: float) -> Optional[float]:
        """Close the current position (if any) and open a new one in the
        opposite direction at the same price/time — mirrors the Pine
        backtest engine's flip behaviour."""
        pnl = None
        if self.has_open_position():
            pnl = self.close_position(ts, price)
        self.open_position(ts, new_direction, price)
        return pnl

    def has_open_position(self) -> bool:
        return self._open_id is not None

    def open_direction(self) -> Optional[str]:
        return self._open_direction

    def open_entry_price(self) -> Optional[float]:
        return self._open_entry_price

    # -----------------------------------------------------------------------
    # History queries
    # -----------------------------------------------------------------------

    def query_trades(self, from_date: date = None, to_date: date = None,
                     days: int = None) -> list:
        with self._conn() as conn:
            if from_date or to_date:
                from_s = f"{from_date} 00:00:00" if from_date else "0000-01-01"
                to_s   = f"{to_date} 23:59:59"   if to_date   else "9999-12-31"
                rows = conn.execute(
                    "SELECT * FROM trades WHERE entry_time >= ? AND entry_time <= ? ORDER BY id",
                    (from_s, to_s)
                ).fetchall()
            elif days:
                cutoff = datetime.now() - timedelta(days=days)
                rows = conn.execute(
                    "SELECT * FROM trades WHERE entry_time >= ? ORDER BY id",
                    (cutoff.strftime("%Y-%m-%d 00:00:00"),)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return rows

    def print_history(self, from_date: date = None, to_date: date = None,
                      days: int = None):
        trades = self.query_trades(from_date=from_date, to_date=to_date, days=days)
        print("datetime, trade signal, entry, exit, PnL")
        if not trades:
            print("No trades found.")
        for t in trades:
            _, etime, direction, eprice, xtime, xprice, pnl = t
            if xtime is None:
                continue   # open position printed separately below
            print(f"{etime}, {direction}, {eprice:.2f}, {xprice:.2f}, {pnl:+.2f} points")
        print()
        if self.has_open_position():
            print(f"Open position: {self._open_direction} @ {self._open_entry_price:.2f}")

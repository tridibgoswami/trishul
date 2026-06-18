"""AngelOne SmartAPI wrapper — login, historical and live candle fetch."""

import time
import pyotp
from datetime import datetime
from typing import List, Optional

try:
    from SmartApi import SmartConnect
except ImportError:
    raise SystemExit("smartapi-python not installed. Run: pip install smartapi-python")

from market_utils import IST
from indicators import BarData


class BrokerClient:

    def __init__(self, cfg):
        self.cfg = cfg
        self.obj: Optional[SmartConnect] = None
        self._jwt_token: Optional[str] = None

    # -----------------------------------------------------------------------
    # Login
    # -----------------------------------------------------------------------

    def login(self) -> None:
        cfg = self.cfg
        print(f"[AUTH] Logging in to AngelOne as {cfg.CLIENT_ID} ...")
        self.obj = SmartConnect(api_key=cfg.API_KEY)
        totp_code = pyotp.TOTP(cfg.TOTP_SECRET).now()
        resp = self.obj.generateSession(cfg.CLIENT_ID, cfg.MPIN, totp_code)
        if not resp or not resp.get("status"):
            raise SystemExit(f"[AUTH] Login failed: {resp}")
        self._jwt_token = resp["data"]["jwtToken"]
        print(f"[AUTH] Login successful.")

    # -----------------------------------------------------------------------
    # Fetch candles with retry
    # -----------------------------------------------------------------------

    def _fetch_candles_raw(self, from_dt: datetime, to_dt: datetime) -> list:
        cfg = self.cfg
        historicParam = {
            "exchange"   : cfg.EXCHANGE,
            "symboltoken": cfg.SYMBOL_TOKEN,
            "interval"   : cfg.INTERVAL,
            "fromdate"   : from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate"     : to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        resp = self.obj.getCandleData(historicParam)
        if not resp or not resp.get("status"):
            raise IOError(f"getCandleData failed: {resp}")
        return resp.get("data") or []

    def fetch_candles(self, from_dt: datetime, to_dt: datetime,
                      retries: int = 4) -> List[BarData]:
        delay = 2
        for attempt in range(retries):
            try:
                raw = self._fetch_candles_raw(from_dt, to_dt)
                return self._parse_candles(raw)
            except Exception as e:
                if attempt == retries - 1:
                    raise
                print(f"[BROKER] Fetch error ({e}), retry in {delay}s ...")
                time.sleep(delay)
                delay *= 2
        return []

    @staticmethod
    def _parse_candles(raw: list) -> List[BarData]:
        bars = []
        for row in raw:
            ts_str = row[0]
            try:
                dt = datetime.fromisoformat(ts_str)
            except ValueError:
                dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                dt = IST.localize(dt)
            if dt.tzinfo is None:
                dt = IST.localize(dt)
            bars.append(BarData(
                timestamp=dt,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            ))
        return bars

    # -----------------------------------------------------------------------
    # Convenience fetchers
    # -----------------------------------------------------------------------

    def fetch_day_candles(self, trading_date) -> List[BarData]:
        from_dt = IST.localize(datetime(trading_date.year, trading_date.month, trading_date.day, 9, 15))
        to_dt   = IST.localize(datetime(trading_date.year, trading_date.month, trading_date.day, 15, 30))
        return self.fetch_candles(from_dt, to_dt)

    def fetch_today_upto_now(self) -> List[BarData]:
        now = datetime.now(IST)
        today = now.date()
        from_dt = IST.localize(datetime(today.year, today.month, today.day, 9, 15))
        m       = (now.minute // 5) * 5
        to_dt   = now.replace(minute=m, second=0, microsecond=0)
        if to_dt <= from_dt:
            return []
        return self.fetch_candles(from_dt, to_dt)

    def fetch_latest_bar(self) -> Optional[BarData]:
        bars = self.fetch_today_upto_now()
        return bars[-1] if bars else None

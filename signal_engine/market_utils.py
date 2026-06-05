"""NSE market calendar and IST time utilities."""

from datetime import date, datetime, timedelta, time as dtime
import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE trading holidays 2025-2026 (update annually from NSE circular)
NSE_HOLIDAYS = {
    # 2025
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 3, 31), date(2025, 4, 10), date(2025, 4, 14),
    date(2025, 4, 18), date(2025, 5,  1), date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 23),
    date(2025, 10, 24), date(2025, 11, 5), date(2025, 12, 25),
    # 2026
    date(2026, 1, 26), date(2026, 3, 20), date(2026, 4,  2),
    date(2026, 4,  3), date(2026, 4, 14), date(2026, 5,  1),
    date(2026, 8, 15), date(2026, 10, 2), date(2026, 10, 23),
    date(2026, 11, 14), date(2026, 12, 25),
}

MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def is_trading_day(d: date = None) -> bool:
    if d is None:
        d = today_ist()
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def is_market_open() -> bool:
    now = now_ist()
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def prev_trading_day(from_date: date = None, n: int = 1) -> date:
    """Return the nth previous trading day before from_date."""
    d = from_date or today_ist()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if is_trading_day(d):
            count += 1
    return d


def bar_time_min(dt: datetime) -> int:
    """Minutes since midnight for a bar datetime."""
    return dt.hour * 60 + dt.minute


def next_bar_fetch_time(now: datetime) -> datetime:
    """
    Returns the next datetime at which we should fetch a completed 5-min bar.
    Bars close at :20, :25, :30, ... so we fetch 5s after each mark.
    """
    m = now.minute
    s = now.second
    # Round up to next multiple-of-5 minute boundary
    next_m = ((m // 5) + 1) * 5
    extra_h = next_m // 60
    next_m  = next_m % 60
    fetch = now.replace(hour=now.hour + extra_h, minute=next_m, second=5, microsecond=0)
    return fetch


def fmt_ist(dt: datetime) -> str:
    """Format: 05-06-2026 9:25"""
    return dt.strftime("%d-%m-%Y %-H:%M")

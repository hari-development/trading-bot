"""
Market Hours Utility — NSE/BSE session awareness.

Used by the engine's run() loop to:
  - Skip expensive data fetches when the market is closed.
  - Sleep precisely until the next trading session opens (auto-resumes
    the next morning or after weekends — no manual restart required).

NSE trading hours: 09:15 – 15:30 IST, Monday – Friday.
Indian public holidays are not included here (add them to HOLIDAY_DATES
as needed). On a holiday the engine will simply find no data and skip
gracefully — no crash, no trade.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Add NSE public holidays here as needed (YYYY-MM-DD strings).
# The engine gracefully handles missing data on holidays even without this list,
# but listing them here enables a cleaner log message.
HOLIDAY_DATES: set[date] = {
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 3, 14),   # Holi
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti / Ram Navami
    date(2025, 4, 18),   # Good Friday
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 24),  # Dussehra
    date(2025, 11, 5),   # Diwali Laxmi Pujan (Muhurat trading day — partial)
    date(2025, 12, 25),  # Christmas

    # 2026 — update as NSE publishes the official holiday calendar
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi (tentative)
    date(2026, 4, 3),    # Good Friday (tentative)
}


def now_ist() -> datetime:
    """Return current datetime in IST."""
    return datetime.now(tz=IST)


def is_trading_day(d: date | None = None) -> bool:
    """Return True if *d* is a weekday and not a known NSE holiday."""
    if d is None:
        d = now_ist().date()
    if d.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    return d not in HOLIDAY_DATES


def is_market_open(dt: datetime | None = None) -> bool:
    """Return True if *dt* (IST) falls inside the NSE trading session."""
    if dt is None:
        dt = now_ist()
    d = dt.date()
    t = dt.time()
    return is_trading_day(d) and MARKET_OPEN <= t <= MARKET_CLOSE


def _next_trading_day(after: date) -> date:
    """Return the next calendar date that is a trading day, starting after *after*."""
    candidate = after + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def seconds_until_market_open(dt: datetime | None = None) -> float:
    """
    Return the number of seconds from *dt* until the next NSE market open.
    Returns 0 if the market is currently open.
    """
    if dt is None:
        dt = now_ist()

    if is_market_open(dt):
        return 0.0

    today = dt.date()
    t = dt.time()

    # If today is a trading day and we're before open — wait until today's open
    if is_trading_day(today) and t < MARKET_OPEN:
        open_dt = datetime.combine(today, MARKET_OPEN, tzinfo=IST)
        return max(0.0, (open_dt - dt).total_seconds())

    # Otherwise wait until the next trading day's open
    next_day = _next_trading_day(today)
    open_dt = datetime.combine(next_day, MARKET_OPEN, tzinfo=IST)
    return max(0.0, (open_dt - dt).total_seconds())


def next_open_description(dt: datetime | None = None) -> str:
    """Human-readable description of when the next session opens."""
    if dt is None:
        dt = now_ist()
    secs = seconds_until_market_open(dt)
    if secs == 0:
        return "market is OPEN now"
    hours, rem = divmod(int(secs), 3600)
    mins = rem // 60
    today = dt.date()
    t = dt.time()
    if is_trading_day(today) and t < MARKET_OPEN:
        day_label = "today"
    else:
        next_day = _next_trading_day(today)
        day_label = next_day.strftime("%A %d-%b")
    return f"opens at 09:15 IST {day_label} (in {hours}h {mins}m)"

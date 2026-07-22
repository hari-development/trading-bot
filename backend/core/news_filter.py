"""
News / Macro-Event Blackout Filter.

Trading around high-impact scheduled events is equivalent to gambling on a
binary outcome. This module maintains a calendar of known blackout windows and
exposes a single function the quality-filter calls before allowing any entry.

Blackout windows are defined as (date, start_time_IST, end_time_IST) tuples.
The list should be updated each quarter as RBI / FOMC calendars are released.

Adding a new event is one line — no code changes elsewhere required.
"""
from datetime import date, time, datetime
from typing import List, NamedTuple

from utils.logger import get_logger

logger = get_logger("news_filter")


class BlackoutWindow(NamedTuple):
    event_date: date
    start_ist: time   # IST (UTC+5:30)
    end_ist: time     # IST — trading resumes after this
    label: str


# ---------------------------------------------------------------------------
# Blackout Calendar — update each quarter
# Format: (YYYY, MM, DD), start IST, end IST, label
# ---------------------------------------------------------------------------
_BLACKOUT_CALENDAR: List[BlackoutWindow] = [
    # ── RBI MPC Decisions (typically 10:00 IST announcement) ──
    BlackoutWindow(date(2026, 4, 9),  time(9, 0),  time(11, 30), "RBI MPC Apr 2026"),
    BlackoutWindow(date(2026, 6, 6),  time(9, 0),  time(11, 30), "RBI MPC Jun 2026"),
    BlackoutWindow(date(2026, 8, 6),  time(9, 0),  time(11, 30), "RBI MPC Aug 2026"),
    BlackoutWindow(date(2026, 10, 7), time(9, 0),  time(11, 30), "RBI MPC Oct 2026"),
    BlackoutWindow(date(2026, 12, 5), time(9, 0),  time(11, 30), "RBI MPC Dec 2026"),
    # ── US Federal Reserve FOMC (overnight announcement, Indian market impact at open) ──
    BlackoutWindow(date(2026, 1, 29), time(9, 0),  time(11, 0),  "FOMC Jan 2026"),
    BlackoutWindow(date(2026, 3, 19), time(9, 0),  time(11, 0),  "FOMC Mar 2026"),
    BlackoutWindow(date(2026, 5, 7),  time(9, 0),  time(11, 0),  "FOMC May 2026"),
    BlackoutWindow(date(2026, 6, 18), time(9, 0),  time(11, 0),  "FOMC Jun 2026"),
    BlackoutWindow(date(2026, 7, 30), time(9, 0),  time(11, 0),  "FOMC Jul 2026"),
    BlackoutWindow(date(2026, 9, 17), time(9, 0),  time(11, 0),  "FOMC Sep 2026"),
    BlackoutWindow(date(2026, 11, 5), time(9, 0),  time(11, 0),  "FOMC Nov 2026"),
    BlackoutWindow(date(2026, 12, 16),time(9, 0),  time(11, 0),  "FOMC Dec 2026"),
    # ── India Union Budget ──
    BlackoutWindow(date(2027, 2, 1),  time(9, 0),  time(15, 30), "Union Budget 2027"),
    # ── Nifty / BankNifty Monthly Expiry (last Thursday — avoid last 30 min) ──
    # These are handled separately in quality_filter via avoid_last_minutes logic,
    # but we flag the full expiry day as reduced-confidence if needed.
]


def is_news_blackout(dt: datetime | None = None) -> tuple[bool, str]:
    """
    Returns (True, event_label) if the given datetime falls inside a
    scheduled high-impact event blackout window, (False, '') otherwise.

    Parameters
    ----------
    dt : datetime | None
        Moment to check. Defaults to datetime.now() (local time, IST assumed).
    """
    if dt is None:
        dt = datetime.now()

    check_date = dt.date()
    check_time = dt.time()

    for window in _BLACKOUT_CALENDAR:
        if window.event_date != check_date:
            continue
        if window.start_ist <= check_time <= window.end_ist:
            logger.warning(
                f"News blackout active: '{window.label}' "
                f"({window.start_ist}–{window.end_ist} IST). No new entries."
            )
            return True, window.label

    return False, ""


def get_todays_events(dt: datetime | None = None) -> List[str]:
    """Return list of event labels scheduled for today (for dashboard display)."""
    if dt is None:
        dt = datetime.now()
    return [w.label for w in _BLACKOUT_CALENDAR if w.event_date == dt.date()]

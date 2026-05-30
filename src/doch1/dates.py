"""Pure date helpers for DOCH1 week selection (no I/O, no rich/textual)."""

from __future__ import annotations

from datetime import date, timedelta


def default_week_anchor(today: date | None = None) -> date:
    """Anchor date whose Sun-Sat week is the 'default' to show/fill.

    Sun-Fri -> the current Sun-Sat week (anchor == today).
    Saturday (weekday()==5) -> the FOLLOWING week: return today+1 (Sunday),
    because the current IDF week ends Saturday evening.

    The caller passes the result to cli._week_days(), which back-computes the
    containing Sunday via (anchor.weekday()+1)%7, so returning any day inside the
    target week is sufficient.
    """
    t = today or date.today()
    if t.weekday() == 5:  # Saturday
        return t + timedelta(days=1)
    return t

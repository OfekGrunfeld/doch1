"""Tests for the default-week-anchor Saturday rule (pure logic, TDD)."""

from __future__ import annotations

from datetime import date, timedelta

from doch1.cli import _week_days
from doch1.dates import default_week_anchor


def _week_of(anchor: date) -> tuple[date, date]:
    days = _week_days(anchor)
    return days[0], days[-1]


# 2026-05-30 is a Saturday; 2026-05-31 a Sunday; 2026-05-29 a Friday.


def test_saturday_returns_following_week():
    sat = date(2026, 5, 30)
    assert sat.weekday() == 5
    anchor = default_week_anchor(sat)
    assert anchor == date(2026, 5, 31)  # the following Sunday
    start, end = _week_of(anchor)
    assert start == date(2026, 5, 31)
    assert end == date(2026, 6, 6)


def test_friday_returns_current_week_unchanged():
    fri = date(2026, 5, 29)
    assert fri.weekday() == 4
    anchor = default_week_anchor(fri)
    assert anchor == fri
    start, end = _week_of(anchor)
    assert start == date(2026, 5, 24)  # Sunday
    assert end == date(2026, 5, 30)  # Saturday


def test_sunday_returns_own_week():
    sun = date(2026, 5, 31)
    assert sun.weekday() == 6
    anchor = default_week_anchor(sun)
    assert anchor == sun
    start, end = _week_of(anchor)
    assert start == date(2026, 5, 31)
    assert end == date(2026, 6, 6)


def test_midweek_days_unchanged_same_containing_week():
    # Mon..Thu of the 2026-05-24 .. 2026-05-30 week.
    for d in (date(2026, 5, 25), date(2026, 5, 26), date(2026, 5, 27), date(2026, 5, 28)):
        anchor = default_week_anchor(d)
        assert anchor == d
        start, end = _week_of(anchor)
        assert start == date(2026, 5, 24)
        assert end == date(2026, 5, 30)


def test_next_week_is_anchor_plus_seven():
    # For a Saturday base, next week follows the already-shifted target week.
    sat = date(2026, 5, 30)
    nxt = default_week_anchor(sat) + timedelta(days=7)
    start, end = _week_of(nxt)
    assert start == date(2026, 6, 7)
    assert end == date(2026, 6, 13)

    # For a non-Saturday base.
    wed = date(2026, 5, 27)
    nxt2 = default_week_anchor(wed) + timedelta(days=7)
    start2, end2 = _week_of(nxt2)
    assert start2 == date(2026, 5, 31)
    assert end2 == date(2026, 6, 6)


def test_default_uses_today_when_none():
    # Smoke: no arg returns a date in some valid Sun-Sat week.
    anchor = default_week_anchor()
    start, end = _week_of(anchor)
    assert (end - start).days == 6
    assert start.weekday() == 6  # Sunday

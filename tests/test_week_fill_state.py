"""Unit tests for the pure week_fill_state indicator helper.

Uses plain dict rows shaped like fetch_week_status() output — no network,
no Textual. week_fill_state is the single source of truth for the week-fill
icon shown on the This week / Next week screens.
"""

from __future__ import annotations

from doch1.tui.data import week_fill_state


def _row(action, effective=""):
    return {
        "date": "2026-05-30",
        "action": action,
        "effective": effective,
        "in_base": False,
        "conflict": False,
        "note": "",
    }


def test_fully_scheduled_week_is_filled():
    # past days skipped, today reported, all future days already scheduled.
    rows = [
        _row("skip-past"),
        _row("today", "At base"),
        _row("skip-scheduled"),
        _row("skip-scheduled"),
        _row("skip-scheduled"),
        _row("skip-scheduled"),
        _row("skip-scheduled"),
    ]
    assert week_fill_state(rows) == "filled"


def test_one_future_day_is_partial():
    rows = [
        _row("skip-past"),
        _row("today", "At base"),
        _row("skip-scheduled"),
        _row("future"),  # unscheduled future day still needs a report
        _row("skip-scheduled"),
    ]
    assert week_fill_state(rows) == "partial"


def test_today_pending_is_partial():
    # today reported as covered elsewhere, but today itself blank => still needs.
    rows = [
        _row("skip-past"),
        _row("today", ""),  # today, not yet reported
        _row("skip-scheduled"),  # at least one covered day
    ]
    assert week_fill_state(rows) == "partial"


def test_all_skip_past_or_empty_week_is_empty():
    rows = [
        _row("skip-past"),
        _row("skip-past"),
        _row("skip-window"),
    ]
    assert week_fill_state(rows) == "empty"


def test_completely_empty_rows_is_empty():
    assert week_fill_state([]) == "empty"

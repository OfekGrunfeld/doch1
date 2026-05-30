"""Pure-merge tests for the calendar month overlay (no network / Textual).

Verifies merge_month_cells joins past history days with future scheduled days,
tags each date with the right kind, excludes out-of-month dates, and lets
history win over scheduled on a shared date.
"""

from __future__ import annotations

from datetime import date

from doch1.api import HistoryDay
from doch1.tui.data import CellInfo, grid_window, merge_grid_cells, merge_month_cells


def _hist(
    d, *, reported="נמצא/ת ביחידה", approved="נמצא/ת ביחידה", in_base=True, conflict=False, note=""
):
    return HistoryDay(
        date=d,
        reported=reported,
        determined="",
        approved=approved,
        in_base=in_base,
        conflict=conflict,
        note=note,
    )


def _sched(d, *, main="נמצא/ת ביחידה", secondary="נוכח/ת", code="0101"):
    return {
        "date": d.isoformat() + "T00:00:00",
        "reportedStatusCode": code,
        "reportedMainName": main,
        "secondaryStatusReported": secondary,
    }


def test_history_and_scheduled_get_right_kind():
    history = [_hist(date(2026, 6, 3)), _hist(date(2026, 6, 4))]
    scheduled = [_sched(date(2026, 6, 10)), _sched(date(2026, 6, 11))]
    cells = merge_month_cells(history, scheduled, 6, 2026)

    assert cells[date(2026, 6, 3)].kind == "history"
    assert cells[date(2026, 6, 4)].kind == "history"
    assert cells[date(2026, 6, 10)].kind == "scheduled"
    assert cells[date(2026, 6, 11)].kind == "scheduled"
    assert set(cells) == {date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 10), date(2026, 6, 11)}


def test_non_reported_days_are_absent_none():
    # A day with neither history nor a schedule must not appear in the map;
    # the screen treats absence as kind "none" (renders blank in-month).
    cells = merge_month_cells([_hist(date(2026, 6, 3))], [], 6, 2026)
    assert date(2026, 6, 5) not in cells
    assert cells.get(date(2026, 6, 5)) is None


def test_dates_outside_month_excluded():
    history = [_hist(date(2026, 5, 31)), _hist(date(2026, 6, 1))]
    scheduled = [_sched(date(2026, 7, 1)), _sched(date(2026, 6, 30))]
    cells = merge_month_cells(history, scheduled, 6, 2026)

    assert set(cells) == {date(2026, 6, 1), date(2026, 6, 30)}
    assert cells[date(2026, 6, 1)].kind == "history"
    assert cells[date(2026, 6, 30)].kind == "scheduled"
    assert date(2026, 5, 31) not in cells
    assert date(2026, 7, 1) not in cells


def test_history_wins_over_scheduled_on_same_date():
    d = date(2026, 6, 15)
    cells = merge_month_cells([_hist(d, note="reported")], [_sched(d)], 6, 2026)
    assert cells[d].kind == "history"
    assert cells[d].hd.note == "reported"


def test_scheduled_cell_carries_reported_status():
    cells = merge_month_cells([], [_sched(date(2026, 6, 20))], 6, 2026)
    ci = cells[date(2026, 6, 20)]
    assert isinstance(ci, CellInfo)
    assert ci.kind == "scheduled"
    # reportedMainName / secondary joined into the effective status.
    assert "נמצא/ת ביחידה" in ci.hd.effective


def test_empty_inputs_give_empty_map():
    assert merge_month_cells([], [], 6, 2026) == {}
    assert merge_month_cells(None, None, 6, 2026) == {}


# ---------- grid-window merge (spill cells populated) ----------


def test_grid_window_is_fixed_6_weeks():
    s, e = grid_window(5, 2026)
    assert s.weekday() == 6  # Sunday (Python: Mon=0..Sun=6)
    assert (e - s).days == 41
    assert s <= date(2026, 5, 1) and date(2026, 5, 31) <= e


def test_merge_grid_includes_prev_tail_and_next_head():
    # May 2026 grid pulls Apr tail + Jun head.
    s, e = grid_window(5, 2026)
    history = [_hist(date(2026, 4, 30)), _hist(date(2026, 5, 4))]
    scheduled = [_sched(date(2026, 6, 2))]
    cells = merge_grid_cells(history, scheduled, s, e)
    assert cells[date(2026, 4, 30)].kind == "history"  # prev-month tail
    assert cells[date(2026, 6, 2)].kind == "scheduled"  # next-month head
    assert cells[date(2026, 5, 4)].kind == "history"


def test_merge_grid_excludes_outside_window():
    s, e = grid_window(5, 2026)
    cells = merge_grid_cells([_hist(date(2026, 3, 1))], [], s, e)
    assert cells == {}


def test_merge_grid_history_wins():
    s, e = grid_window(6, 2026)
    d = date(2026, 6, 15)
    cells = merge_grid_cells([_hist(d, note="r")], [_sched(d)], s, e)
    assert cells[d].kind == "history" and cells[d].hd.note == "r"

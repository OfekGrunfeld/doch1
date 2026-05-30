"""Renderer output tests — capture a rich Console to a string and assert."""

from __future__ import annotations

import io
from datetime import date

from rich.console import Console

from doch1 import render
from doch1.api import HistoryDay


def _cap(force_terminal=True, width=120):
    return Console(file=io.StringIO(), force_terminal=force_terminal, width=width, highlight=False)


def _hist(d, reported="", determined="", approved="", in_base=True, conflict=False, note=""):
    return HistoryDay(
        date=d,
        reported=reported,
        determined=determined,
        approved=approved,
        in_base=in_base,
        conflict=conflict,
        note=note,
    )


def _identity(s: str) -> str:
    return s


# ---------- history ----------


def test_history_empty_state():
    c = _cap()
    render.render_history(c, [], 5, 2026, _identity)
    out = c.file.getvalue()
    assert "No reports" in out
    assert "05/2026" in out


def test_history_empty_conflicts_only():
    c = _cap()
    render.render_history(c, [], 5, 2026, _identity, conflicts_only=True)
    out = c.file.getvalue()
    assert "No conflicts" in out


def test_history_table_basic():
    days = [_hist(date(2026, 5, 4), approved="At base", in_base=True, note="hello")]
    c = _cap()
    render.render_history(c, days, 5, 2026, _identity)
    out = c.file.getvalue()
    assert "History 05/2026" in out
    assert "1 day" in out
    assert "2026-05-04" in out
    assert "At base" in out
    assert render.GLYPH_OK in out  # in-base marker
    assert "hello" in out


def test_history_conflict_row_shows_arrow_and_flag():
    days = [
        _hist(
            date(2026, 5, 5), reported="At base", approved="Off base", in_base=False, conflict=True
        )
    ]
    c = _cap()
    render.render_history(c, days, 5, 2026, _identity)
    out = c.file.getvalue()
    assert render.GLYPH_CONFLICT in out  # warning glyph
    assert "->" in out or "→" in out  # reported -> approved
    assert "conflict" in out.lower()


def test_history_plain_no_ansi():
    days = [_hist(date(2026, 5, 4), approved="At base", note="x")]
    c = _cap(force_terminal=False)
    render.render_history(c, days, 5, 2026, _identity)
    out = c.file.getvalue()
    assert "\x1b[" not in out


# ---------- week ----------


def test_week_results_table():
    results = [
        {"date": "2026-05-31", "action": "today", "ok": True},
        {"date": "2026-06-01", "action": "future", "ok": False},
        {"date": "2026-06-02", "action": "skip-scheduled", "ok": None},
    ]
    c = _cap()
    render.render_week(c, results)
    out = c.file.getvalue()
    assert render.GLYPH_OK in out
    assert render.GLYPH_FAIL in out
    assert "31.05" in out


# ---------- calendar cell ----------


def test_calendar_cell_today_and_status():
    hd = _hist(date(2026, 5, 31), approved="At base", in_base=True)
    cell = render.calendar_cell(date(2026, 5, 31), hd, in_month=True, is_today=True)
    plain = cell.plain
    assert "[31]" in plain  # today marker
    assert render.GLYPH_OK in plain  # green at-base glyph


def test_calendar_cell_spill_and_conflict():
    spill = render.calendar_cell(date(2026, 4, 30), None, in_month=False, is_today=False)
    assert "30" in spill.plain
    assert render.GLYPH_OK not in spill.plain  # no status on spill cells
    hd = _hist(date(2026, 5, 5), reported="At base", approved="Off base", conflict=True)
    conf = render.calendar_cell(date(2026, 5, 5), hd, in_month=True, is_today=False)
    assert render.GLYPH_CONFLICT in conf.plain


def test_calendar_cell_spill_with_data_renders_dim_glyph():
    hd = _hist(date(2026, 6, 1), approved="At base", in_base=True)
    cell = render.calendar_cell(date(2026, 6, 1), hd, in_month=False, is_today=False)
    plain = cell.plain
    assert " 1 " in plain
    assert render.GLYPH_OK in plain  # spill day now SHOWS its status glyph


def test_calendar_legend_has_glyphs():
    leg = render.calendar_legend().plain
    assert render.GLYPH_OK in leg and render.GLYPH_CONFLICT in leg


# ---------- status ----------


def test_status_ok():
    c = _cap()
    render.render_status(c, True, transport="browser-session")
    out = c.file.getvalue()
    assert render.GLYPH_OK in out
    assert "browser-session" in out


def test_status_fail():
    c = _cap()
    render.render_status(c, False, error="boom")
    out = c.file.getvalue()
    assert render.GLYPH_FAIL in out
    assert "boom" in out

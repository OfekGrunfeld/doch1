"""Textual Pilot tests with a fake DataService (no Playwright / no network)."""

from __future__ import annotations

from datetime import date

from textual.widgets import ContentSwitcher, DataTable, Static

from doch1.api import HistoryDay
from doch1.tui.app import Doch1App


class FakeService:
    """In-memory stand-in for DataService. Records mutations."""

    def __init__(self):
        self.report_today_calls = 0
        self.fill_calls = []
        self.login_calls = 0
        self.login_manual_calls = []
        self._anchor = date(2026, 5, 31)  # Sunday — deterministic week
        self.history = {
            # Late-April day so May's prev-tail spill is populated and a
            # roll-over up/left has a real target. (Keep May data <= May 5 so
            # June's grid window (May 31..Jul 11) stays empty.)
            (4, 2026): [
                HistoryDay(
                    date(2026, 4, 30), "נמצא/ת ביחידה", "", "נמצא/ת ביחידה", True, False, "apr"
                ),
            ],
            (5, 2026): [
                HistoryDay(
                    date(2026, 5, 4), "נמצא/ת ביחידה", "", "נמצא/ת ביחידה", True, False, "ok"
                ),
                HistoryDay(
                    date(2026, 5, 5), "נמצא/ת ביחידה", "", 'חו"ל', False, True, "conflict day"
                ),
            ],
            (6, 2026): [],
        }

    def week_anchor(self):
        return self._anchor

    def fetch_today_status(self):
        return {
            "effective": "נמצא/ת ביחידה",
            "in_base": True,
            "conflict": False,
            "note": "",
            "found": True,
        }

    def do_report_today(self):
        self.report_today_calls += 1
        return True

    def fetch_week_status(self, days):
        return [
            {
                "date": d.isoformat(),
                "action": "future" if d > date(2026, 5, 31) else "skip-past",
                "effective": "",
                "in_base": False,
                "conflict": False,
                "note": "",
            }
            for d in days
        ]

    def fetch_week_plan_sync(self, days):
        return [
            {"date": d.isoformat(), "action": "future" if d > date(2026, 5, 31) else "skip-past"}
            for d in days
        ]

    def do_fill_week(self, days):
        self.fill_calls.append(list(days))
        results = [
            {
                "date": d.isoformat(),
                "action": "future" if d > date(2026, 5, 31) else "skip-past",
                "ok": True if d > date(2026, 5, 31) else None,
            }
            for d in days
        ]
        return results, []

    def fetch_history(self, m, y):
        return list(self.history.get((m, y), []))

    def fetch_calendar_month(self, m, y):
        from doch1.tui.data import grid_window, merge_grid_cells

        s, e = grid_window(m, y)
        hist = []
        for days in self.history.values():
            hist += days
        return merge_grid_cells(hist, [], s, e)

    def probe_auth(self):
        return True, "browser-session", None

    def login(self, *, manual: bool = False):
        self.login_calls += 1
        self.login_manual_calls.append(manual)


def _app():
    return Doch1App(FakeService())


async def test_launches_on_today_readonly():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#main", ContentSwitcher).current == "screen-today"
        assert app.service.report_today_calls == 0  # no mutation on launch
        assert app.service.fill_calls == []


async def test_number_key_switches_to_next_week():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#main", ContentSwitcher).current == "screen-nextweek"


async def test_today_report_calls_service():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.3)
        assert app.service.report_today_calls == 1


async def test_calendar_month_nav_and_conflicts_filter():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("4")  # Calendar
        await pilot.pause(0.3)
        cal = app.query_one("#screen-history")
        assert (cal.m, cal.y) == (5, 2026)
        table = app.query_one("#cal-table", DataTable)
        # 7-col Sun..Sat grid, one row per ISO week of May 2026 (5 weeks).
        assert len(table.columns) == 7
        assert table.row_count >= 4
        # conflicts-only still keeps the rectangle (does not drop rows).
        await pilot.press("c")
        await pilot.pause(0.3)
        assert cal.conflicts_only is True
        assert table.row_count >= 4
        await pilot.press("c")  # back to all
        await pilot.pause(0.2)
        # next month -> empty state (June 2026 has no reports in the fake)
        await pilot.press("greater_than_sign")
        await pilot.pause(0.3)
        assert (cal.m, cal.y) == (6, 2026)
        empty = app.query_one("#history-empty", Static)
        assert "hidden" not in empty.classes


async def test_calendar_arrow_nav_and_day_detail():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("4")  # Calendar
        await pilot.pause(0.3)
        from textual.coordinate import Coordinate

        table = app.query_one("#cal-table", DataTable)
        table.focus()
        # Park on a known interior cell so left/up moves are unambiguous.
        table.cursor_coordinate = Coordinate(1, 3)
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert table.cursor_coordinate.column == 4
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_coordinate.row == 2
        # Enter opens the day-detail overlay, keyboard-only.
        await pilot.press("enter")
        await pilot.pause(0.2)
        from doch1.tui.modals import DayDetailModal

        assert isinstance(app.screen, DayDetailModal)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, DayDetailModal)


async def test_escape_focuses_nav_and_enter_switches_screen():
    app = _app()
    async with app.run_test() as pilot:
        from textual.widgets import ListView

        await pilot.press("escape")
        await pilot.pause()
        nav = app.query_one("#nav", ListView)
        assert nav.has_focus
        nav.index = 1  # highlight "This week" — must NOT switch on highlight
        await pilot.pause()
        assert app.query_one("#main", ContentSwitcher).current == "screen-today"
        await pilot.press("enter")  # commit on activation
        await pilot.pause(0.2)
        assert app.query_one("#main", ContentSwitcher).current == "screen-thisweek"


async def test_week_fill_confirm_writes(monkeypatch):
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("2")  # This week
        await pilot.pause(0.3)
        await pilot.press("f")  # opens ConfirmModal
        await pilot.pause(0.2)
        await pilot.press("y")  # confirm
        await pilot.pause(0.4)
        assert len(app.service.fill_calls) == 1


async def test_week_fill_cancel_writes_nothing():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause(0.3)
        await pilot.press("f")
        await pilot.pause(0.2)
        await pilot.press("n")  # cancel
        await pilot.pause(0.3)
        assert app.service.fill_calls == []


async def test_help_opens_and_quit():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause(0.2)
        from doch1.tui.modals import HelpModal

        assert isinstance(app.screen, HelpModal)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, HelpModal)


# ---------- Login (auto 'l' / manual 'm') from inside the TUI ----------
#
# These drive the REAL DataService so cli.py-identical arg wiring is exercised,
# with session.login monkeypatched (no Playwright/browser). app.suspend() and
# the TTY gate are stubbed because Pilot has no controlling terminal.

import contextlib

from doch1.api import Doch1Error
from doch1.tui.app import DataService, Doch1App as _RealApp


@contextlib.contextmanager
def _noop_suspend():
    yield


def _login_app(cfg):
    return Doch1App(DataService(cfg))


def _patch_login_env(monkeypatch, calls, *, raises=None):
    """Stub session.login (recording kwargs), suspend(), and the TTY gate."""

    def fake_login(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return "/tmp/session.json"

    monkeypatch.setattr("doch1.session.login", fake_login)
    monkeypatch.setattr(_RealApp, "suspend", lambda self: _noop_suspend())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)


_CFG = {
    "DOCH1_USER": "u@example.com",
    "DOCH1_PASS": "s3cret",
    "DOCH1_TOTP_SEED": "SEED",
}


async def _goto_status(pilot, app):
    await pilot.press("5")  # Status screen
    await pilot.pause(0.3)
    assert app.query_one("#main", ContentSwitcher).current == "screen-status"


async def test_auto_login_l_passes_password(monkeypatch):
    calls = []
    _patch_login_env(monkeypatch, calls)
    app = _login_app(_CFG)
    async with app.run_test() as pilot:
        await _goto_status(pilot, app)
        await pilot.press("l")
        await pilot.pause(0.3)
    assert len(calls) == 1
    # AUTO: real password threaded through, assisted derived (None) -> headless.
    assert calls[0]["password"] == "s3cret"
    assert calls[0]["assisted"] is None
    assert calls[0]["username"] == "u@example.com"


async def test_manual_login_m_with_password_for_dual_autofill(monkeypatch):
    calls = []
    _patch_login_env(monkeypatch, calls)
    app = _login_app(_CFG)
    async with app.run_test() as pilot:
        await _goto_status(pilot, app)
        await pilot.press("m")
        await pilot.pause(0.3)
    assert len(calls) == 1
    # MANUAL forces assisted=True; password still threaded for dual autofill.
    assert calls[0]["assisted"] is True
    assert calls[0]["password"] == "s3cret"
    assert calls[0]["username"] == "u@example.com"


async def test_manual_login_no_pass_config_sends_none(monkeypatch):
    calls = []
    _patch_login_env(monkeypatch, calls)
    app = _login_app({"DOCH1_USER": "u@example.com"})  # no DOCH1_PASS
    async with app.run_test() as pilot:
        await _goto_status(pilot, app)
        await pilot.press("m")
        await pilot.pause(0.3)
    assert len(calls) == 1
    assert calls[0]["password"] is None
    assert calls[0]["assisted"] is True


async def test_login_doch1error_is_surfaced_not_raised(monkeypatch):
    calls = []
    _patch_login_env(monkeypatch, calls, raises=Doch1Error("re-run with --manual"))
    app = _login_app(_CFG)
    notes = []
    async with app.run_test() as pilot:
        await _goto_status(pilot, app)
        monkeypatch.setattr(
            app, "notify", lambda *a, **k: notes.append((a, k)), raising=False
        )
        # Must NOT raise out of the binding handler / crash the app.
        await pilot.press("l")
        await pilot.pause(0.3)
        # App is still alive and re-probed.
        assert app.query_one("#main", ContentSwitcher).current == "screen-status"
    assert len(calls) == 1
    assert any("re-run with --manual" in str(a) for a, _ in notes)


async def test_login_blocked_without_tty(monkeypatch):
    calls = []
    _patch_login_env(monkeypatch, calls)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    app = _login_app(_CFG)
    async with app.run_test() as pilot:
        await _goto_status(pilot, app)
        await pilot.press("l")
        await pilot.pause(0.3)
    # TTY gate refuses before suspend -> session.login never called.
    assert calls == []

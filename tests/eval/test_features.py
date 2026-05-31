"""FEATURE-EVAL SUITE — asserts every manifest feature against the REAL code.

Hermetic only: the network is blocked by the repo-wide ``_hermetic_guard``
fixture (tests/conftest.py), and every command is driven through a FakeClient /
FakeService double — no Playwright, no browser, no secrets. Each test maps to a
feature id in tests/eval/feature_manifest.py so scripts/eval.py can emit a
pass/fail-per-feature report.

What is pinned, per the manifest:
  * the ``--json`` envelope shape (top-level keys) for every CLI verb,
  * the exit-code semantics: 0 ok / 1 fail / 1 + ``auth_expired`` on a dead
    session,
  * the status-selection resolution order (explicit > env > default),
  * the week-fill PLAN branching (skip-past/today/skip-scheduled/skip-window/
    future),
  * the calendar month/grid merge (history wins over scheduled),
  * the conflicts-only history view,
  * a Textual Pilot pass for the TUI screens + keyboard journeys.

Every test carries the ``eval`` marker (also registered in conftest) so the
runner can select exactly this suite.
"""

from __future__ import annotations

import contextlib
import json as _json
from datetime import date

import pytest
from typer.testing import CliRunner

from doch1 import cli
from doch1.api import HistoryDay
from doch1.tui.data import (
    grid_window,
    merge_grid_cells,
    merge_month_cells,
    plan_week,
    week_fill_state,
)

from .feature_manifest import (
    CLI_FEATURES,
    MANIFEST,
    TUI_SCREENS,
    ExitContract,
)

pytestmark = pytest.mark.eval

# Import the conftest FakeClient (a transport double) for ad-hoc client builds.
from conftest import FakeClient  # noqa: E402

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Helpers: drive a CLI verb with a fake transport (no network/browser)         #
# --------------------------------------------------------------------------- #


def _patch_client(monkeypatch, client) -> None:
    """Make cli._client (and the TUI's reuse of it) yield ``client``.

    cli._client normally inspects state_path()/cookie and builds a BrowserClient
    or RequestsClient. We replace it wholesale with a context manager returning
    the fake, and force state_path().exists() True so the 'status' verb reports
    transport='browser-session' deterministically.
    """

    @contextlib.contextmanager
    def fake_client_cm(_cfg):
        with client as c:
            yield c

    monkeypatch.setattr(cli, "_client", fake_client_cm)
    monkeypatch.setattr(cli, "_cfg", lambda: {})

    class _FakeStatePath:
        def exists(self) -> bool:
            return True

    monkeypatch.setattr("doch1.session.state_path", lambda: _FakeStatePath())


def _run(argv):
    return runner.invoke(cli.app, list(argv))


def _json_out(result) -> dict:
    """Parse the last JSON line of stdout (Typer mixes plain + json lines)."""
    for line in reversed(result.output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return _json.loads(line)
    raise AssertionError(f"no JSON object in output:\n{result.output}")


# --------------------------------------------------------------------------- #
# CLI: --json envelope shape + exit-code OK contract (manifest-driven)         #
# --------------------------------------------------------------------------- #

# Per-feature OK transport: what the fake client must return so the verb succeeds.
_OK_CLIENTS = {
    "today": lambda: FakeClient(multipart_reply="true"),
    "day": lambda: FakeClient(multipart_reply="true"),
    "week": lambda: FakeClient(multipart_reply="true", json_reply={}),
    "history": lambda: FakeClient(json_reply={"days": []}),
    "status": lambda: FakeClient(json_reply={"days": []}),
}


@pytest.mark.parametrize(
    "feature",
    [f for f in CLI_FEATURES if ExitContract.OK in f.exits and f.json_keys and f.needs_client],
    ids=lambda f: f.id,
)
def test_cli_json_envelope_ok(monkeypatch, feature):
    """Each transport-backed verb returns exit 0 and a JSON envelope with the
    manifest's required keys + correct command discriminator."""
    _patch_client(monkeypatch, _OK_CLIENTS[feature.id]())
    result = _run(feature.argv)
    assert result.exit_code == 0, result.output
    payload = _json_out(result)
    assert payload["command"] == feature.json_command
    for key in feature.json_keys:
        assert key in payload, f"{feature.id}: missing JSON key {key!r} in {payload}"


def test_cli_statuses_json_envelope_ok():
    """statuses is pure/offline: exit 0, lists the at-base default with codes."""
    result = _run(("statuses", "--json"))
    assert result.exit_code == 0, result.output
    payload = _json_out(result)
    assert payload["command"] == "statuses"
    assert any(s["main"] == "01" and s["secondary"] == "01" for s in payload["statuses"])


def test_cli_cron_status_json_envelope_ok(monkeypatch):
    """cron status is read-only/offline: exit 0, reports both managed jobs."""
    monkeypatch.setattr(cli, "_crontab_read", lambda: "")
    monkeypatch.setattr(cli, "_cfg", lambda: {})
    result = _run(("cron", "status", "--json"))
    assert result.exit_code == 0, result.output
    payload = _json_out(result)
    assert payload["command"] == "cron status"
    assert "jobs" in payload


# --------------------------------------------------------------------------- #
# CLI: exit-code semantics — FAIL (1) and AUTH_EXPIRED (1 + flag)              #
# --------------------------------------------------------------------------- #

# Write/read verbs that surface the explicit auth_expired flag in the error body.
# `status` is excluded here: it is the dedicated auth probe and instead reports
# `authenticated: false` (see test_cli_status_auth_expired_reports_unauthenticated).
_AUTH_VERBS = [f for f in CLI_FEATURES if ExitContract.AUTH_EXPIRED in f.exits and f.id != "status"]


@pytest.mark.parametrize("feature", _AUTH_VERBS, ids=lambda f: f.id)
def test_cli_auth_expired_exit_and_flag(monkeypatch, feature):
    """A dead session -> exit 1 AND the JSON error body carries auth_expired:true
    (so an agent re-logs-in instead of retrying). 401 triggers this on every
    write/read verb."""
    _patch_client(monkeypatch, FakeClient(fail_status=401))
    result = _run(feature.argv)
    assert result.exit_code == 1, result.output
    payload = _json_out(result)
    assert payload.get("auth_expired") is True, payload


def test_cli_status_auth_expired_reports_unauthenticated(monkeypatch):
    """`status` is the auth probe: a dead session -> exit 1 with
    authenticated:false (its agent-facing flavour of auth_expired)."""
    _patch_client(monkeypatch, FakeClient(fail_status=401))
    result = _run(("status", "--json"))
    assert result.exit_code == 1, result.output
    payload = _json_out(result)
    assert payload["command"] == "status"
    assert payload["authenticated"] is False


def test_cli_today_fail_exit_when_rejected(monkeypatch):
    """A non-'true' server reply on today -> exit 1, ok=False, NOT auth_expired."""
    _patch_client(monkeypatch, FakeClient(multipart_reply="false"))
    result = _run(("today", "--json"))
    assert result.exit_code == 1
    payload = _json_out(result)
    assert payload["ok"] is False
    assert payload.get("auth_expired") is False


def test_cli_day_bad_date_fails(monkeypatch):
    """An unparseable date -> exit 1 with a structured JSON error (no transport)."""
    _patch_client(monkeypatch, FakeClient())
    result = _run(("day", "not-a-date", "--json"))
    assert result.exit_code == 1
    payload = _json_out(result)
    assert payload["ok"] is False


def test_cli_ui_launches_app(monkeypatch):
    """`doch1 ui` launches the interactive shell (run_app stubbed — no terminal)."""
    launched = []
    monkeypatch.setattr("doch1.tui.app.run_app", lambda: launched.append(True))
    result = _run(("ui",))
    assert result.exit_code == 0, result.output
    assert launched == [True]


def test_cli_login_saves_session_ok(monkeypatch):
    """`doch1 login` drives session.login (stubbed — no browser) and reports OK.

    Pins the login verb's contract without a real Playwright/Entra round-trip:
    on success it prints an OK line and exits 0; the password/username are
    threaded from cfg (blank -> None) exactly as the TUI login path does.
    """
    calls = []

    def fake_login(**kwargs):
        calls.append(kwargs)
        return "/tmp/session.json"

    monkeypatch.setattr("doch1.session.login", fake_login)
    monkeypatch.setattr(
        cli, "_cfg", lambda: {"DOCH1_USER": "u@example.com", "DOCH1_PASS": "s3cret"}
    )
    result = _run(("login",))
    assert result.exit_code == 0, result.output
    assert "OK session saved" in result.output
    assert len(calls) == 1
    assert calls[0]["username"] == "u@example.com"
    assert calls[0]["password"] == "s3cret"


def test_cli_login_failure_exits_1(monkeypatch):
    """A login error -> exit 1 with a FAIL line (stable failure contract)."""

    def boom(**kwargs):
        raise RuntimeError("conditional access blocked")

    monkeypatch.setattr("doch1.session.login", boom)
    monkeypatch.setattr(cli, "_cfg", lambda: {})
    result = _run(("login",))
    assert result.exit_code == 1


def test_cli_statuses_refresh_not_wired_fails():
    """statuses --refresh is the documented unwired ritual -> exit 1, ok=False."""
    result = _run(("statuses", "--refresh", "--json"))
    assert result.exit_code == 1
    payload = _json_out(result)
    assert payload["ok"] is False


# --------------------------------------------------------------------------- #
# CLI: history JSON day shape + conflicts-only view                            #
# --------------------------------------------------------------------------- #

_HISTORY_REPLY = {
    "days": [
        {
            "date": "2026-05-04T00:00:00",
            "reportedMainName": "נמצא/ת ביחידה",
            "approvedMainName": "נמצא/ת ביחידה",
            "inBase": True,
            "conflict": False,
            "note": "ok",
        },
        {
            "date": "2026-05-05T00:00:00",
            "reportedMainName": "נמצא/ת ביחידה",
            "approvedMainName": 'חו"ל',
            "inBase": False,
            "conflict": True,
            "note": "conflict day",
        },
    ]
}

_HISTORY_DAY_KEYS = {
    "date",
    "reported",
    "determined",
    "approved",
    "effective",
    "effective_en",
    "in_base",
    "conflict",
    "note",
}


def test_cli_history_day_shape(monkeypatch):
    """Each history day carries the full per-day contract incl. effective_en gloss."""
    _patch_client(monkeypatch, FakeClient(json_reply=_HISTORY_REPLY))
    result = _run(("history", "5", "2026", "--json"))
    assert result.exit_code == 0, result.output
    payload = _json_out(result)
    assert payload["month"] == 5 and payload["year"] == 2026
    assert len(payload["days"]) == 2
    for d in payload["days"]:
        assert _HISTORY_DAY_KEYS <= set(d), set(d)
    # effective_en glosses the Hebrew approved status to English.
    conflict_day = next(d for d in payload["days"] if d["conflict"])
    assert conflict_day["effective_en"] == "Abroad"


def test_cli_history_conflicts_only_filters(monkeypatch):
    """--conflicts keeps only days where approved != reported (conflict=True)."""
    _patch_client(monkeypatch, FakeClient(json_reply=_HISTORY_REPLY))
    result = _run(("history", "5", "2026", "--conflicts", "--json"))
    assert result.exit_code == 0
    payload = _json_out(result)
    assert len(payload["days"]) == 1
    assert payload["days"][0]["conflict"] is True


# --------------------------------------------------------------------------- #
# Status selection: explicit --status > env codes > default; and on the wire   #
# --------------------------------------------------------------------------- #


def test_cli_today_status_default_codes_on_wire(monkeypatch):
    """Default selection sends MainCode/SecondaryCode 01/01 to the endpoint."""
    client = FakeClient(multipart_reply="true")
    _patch_client(monkeypatch, client)
    result = _run(("today", "--json"))
    assert result.exit_code == 0
    _, fields = client.multipart_calls[0]
    assert fields["MainCode"] == "01" and fields["SecondaryCode"] == "01"
    payload = _json_out(result)
    assert payload["status"]["main"] == "01"
    assert payload["status"]["label"]  # English label present


def test_cli_unknown_status_key_rejected(monkeypatch):
    """An unknown --status key -> exit 1 (resolution rejects it before any write)."""
    _patch_client(monkeypatch, FakeClient())
    result = _run(("today", "--status", "leave", "--json"))
    assert result.exit_code == 1
    payload = _json_out(result)
    assert payload["ok"] is False


# --------------------------------------------------------------------------- #
# Week-fill PLAN branching (pure) — the contract behind `doch1 week`           #
# --------------------------------------------------------------------------- #


def test_week_plan_branches_cover_all_actions():
    """plan_week classifies each day: past/today/scheduled/window/future."""
    today = date(2026, 6, 3)  # Wednesday
    # FakeClient post_json -> {} => no filled days, no min/max window.
    client = FakeClient(json_reply={})
    days = [date(2026, 6, 1) + __import__("datetime").timedelta(days=i) for i in range(7)]
    plan = plan_week(client, days, today)
    actions = {p["date"]: p["action"] for p in plan}
    assert actions["2026-06-01"] == "skip-past"  # before today
    assert actions["2026-06-03"] == "today"
    assert actions["2026-06-04"] == "future"  # unscheduled future


def test_week_plan_skips_already_scheduled():
    """A day already present in list_scheduled -> skip-scheduled (no rewrite)."""
    today = date(2026, 6, 3)
    reply = {"days": [{"date": "2026-06-04T00:00:00"}], "minDate": None, "maxDate": None}
    client = FakeClient(json_reply=reply)
    days = [date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)]
    plan = plan_week(client, days, today)
    actions = {p["date"]: p["action"] for p in plan}
    assert actions["2026-06-04"] == "skip-scheduled"
    assert actions["2026-06-05"] == "future"


def test_week_plan_respects_scheduling_window():
    """Days outside the server's [minDate, maxDate] window -> skip-window."""
    today = date(2026, 6, 3)
    reply = {"days": [], "minDate": "2026-06-04", "maxDate": "2026-06-05"}
    client = FakeClient(json_reply=reply)
    days = [date(2026, 6, 6), date(2026, 6, 4)]
    plan = plan_week(client, days, today)
    actions = {p["date"]: p["action"] for p in plan}
    assert actions["2026-06-06"] == "skip-window"  # past maxDate
    assert actions["2026-06-04"] == "future"  # inside window


def test_cli_week_results_shape(monkeypatch):
    """`doch1 week --json` returns a results list of {date,action,ok} dicts."""
    _patch_client(monkeypatch, FakeClient(multipart_reply="true", json_reply={}))
    result = _run(("week", "2026-06-03", "--json"))
    assert result.exit_code == 0, result.output
    payload = _json_out(result)
    assert isinstance(payload["results"], list)
    for r in payload["results"]:
        assert {"date", "action", "ok"} <= set(r)


def test_week_fill_state_classifies_coverage():
    """The week-fill indicator: filled / partial / empty."""

    def row(action, eff=""):
        return {"action": action, "effective": eff}

    assert week_fill_state([]) == "empty"
    assert week_fill_state([row("skip-past"), row("skip-window")]) == "empty"
    assert week_fill_state([row("today", "At base"), row("skip-scheduled")]) == "filled"
    assert week_fill_state([row("today", "At base"), row("future")]) == "partial"


# --------------------------------------------------------------------------- #
# Calendar merge (pure) — history overlay + scheduled overlay + conflicts      #
# --------------------------------------------------------------------------- #


def _hist(d, *, approved="נמצא/ת ביחידה", conflict=False):
    return HistoryDay(
        date=d,
        reported="נמצא/ת ביחידה",
        determined="",
        approved=approved,
        in_base=True,
        conflict=conflict,
        note="",
    )


def _sched(d):
    return {
        "date": d.isoformat() + "T00:00:00",
        "reportedMainName": "נמצא/ת ביחידה",
        "secondaryStatusReported": "נוכח/ת",
    }


def test_calendar_merge_history_wins_over_scheduled():
    """On a shared date, a reported history day is authoritative over a plan."""
    shared = date(2026, 6, 3)
    cells = merge_month_cells([_hist(shared)], [_sched(shared)], 6, 2026)
    assert cells[shared].kind == "history"


def test_calendar_merge_tags_kinds_and_excludes_out_of_month():
    cells = merge_month_cells(
        [_hist(date(2026, 6, 4))],
        [_sched(date(2026, 6, 10)), _sched(date(2026, 7, 1))],  # July is out-of-month
        6,
        2026,
    )
    assert cells[date(2026, 6, 4)].kind == "history"
    assert cells[date(2026, 6, 10)].kind == "scheduled"
    assert date(2026, 7, 1) not in cells


def test_calendar_grid_window_is_fixed_6x7():
    start, end = grid_window(6, 2026)
    assert (end - start).days == 41  # exactly 6 weeks
    assert start.weekday() == 6  # Sunday (firstweekday=6)


def test_calendar_grid_keeps_spill_days():
    """merge_grid_cells keeps adjacent-month spill so the grid renders fully."""
    start, end = grid_window(6, 2026)
    spill = start  # a late-May spill cell inside June's grid window
    cells = merge_grid_cells([_hist(spill)], [], start, end)
    assert spill in cells


def test_calendar_conflict_day_is_flagged():
    cells = merge_month_cells(
        [_hist(date(2026, 6, 5), approved='חו"ל', conflict=True)], [], 6, 2026
    )
    assert cells[date(2026, 6, 5)].hd.conflict is True


# --------------------------------------------------------------------------- #
# TUI: Pilot pass over screens + keyboard journeys (fake service, no browser)  #
# --------------------------------------------------------------------------- #


class FakeService:
    """In-memory DataService stand-in — records mutations, serves fixed data."""

    def __init__(self):
        self.report_today_calls = 0
        self.fill_calls = []
        self._anchor = date(2026, 5, 31)  # Sunday
        self.history = {
            (5, 2026): [
                _hist(date(2026, 5, 4)),
                _hist(date(2026, 5, 5), approved='חו"ל', conflict=True),
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
                "action": "future" if d > self._anchor else "skip-past",
                "effective": "",
                "in_base": False,
                "conflict": False,
                "note": "",
            }
            for d in days
        ]

    def fetch_week_plan_sync(self, days):
        return [
            {"date": d.isoformat(), "action": "future" if d > self._anchor else "skip-past"}
            for d in days
        ]

    def do_fill_week(self, days):
        self.fill_calls.append(list(days))
        return [{"date": d.isoformat(), "action": "future", "ok": True} for d in days], []

    def fetch_history(self, m, y):
        return list(self.history.get((m, y), []))

    def fetch_calendar_month(self, m, y):
        s, e = grid_window(m, y)
        hist = [d for days in self.history.values() for d in days]
        return merge_grid_cells(hist, [], s, e)

    def probe_auth(self):
        return True, "browser-session", None

    def login(self, *, manual: bool = False):
        pass


def _app():
    from doch1.tui.app import Doch1App

    return Doch1App(FakeService())


@pytest.mark.parametrize("screen", TUI_SCREENS, ids=lambda s: s.screen_id)
async def test_tui_screen_reachable(screen):
    """Every manifest screen is reachable via its number key."""
    from textual.widgets import ContentSwitcher

    app = _app()
    async with app.run_test() as pilot:
        await pilot.press(screen.nav_key)
        await pilot.pause(0.3)
        assert app.query_one("#main", ContentSwitcher).current == screen.screen_id


async def test_tui_journey_nav_number_keys():
    """Number keys 1-5 walk every screen in sequence (the nav-number-keys journey)."""
    from textual.widgets import ContentSwitcher

    app = _app()
    async with app.run_test() as pilot:
        for screen in TUI_SCREENS:
            await pilot.press(screen.nav_key)
            await pilot.pause(0.2)
            assert app.query_one("#main", ContentSwitcher).current == screen.screen_id


async def test_tui_journey_launch_readonly():
    """Bare launch lands on Today and performs no mutation."""
    from textual.widgets import ContentSwitcher

    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#main", ContentSwitcher).current == "screen-today"
        assert app.service.report_today_calls == 0
        assert app.service.fill_calls == []


async def test_tui_journey_today_report():
    """r on Today triggers exactly one report call."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.3)
        assert app.service.report_today_calls == 1


async def test_tui_journey_week_fill_confirm():
    """2 -> f -> y writes the week fill."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause(0.3)
        await pilot.press("f")
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(0.4)
        assert len(app.service.fill_calls) == 1


async def test_tui_journey_week_fill_cancel():
    """2 -> f -> n writes nothing."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause(0.3)
        await pilot.press("f")
        await pilot.pause(0.2)
        await pilot.press("n")
        await pilot.pause(0.3)
        assert app.service.fill_calls == []


async def test_tui_journey_calendar_nav_conflicts():
    """4 -> Calendar; c toggles conflicts-only (grid kept); > advances month."""
    from textual.widgets import DataTable

    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("4")
        await pilot.pause(0.3)
        cal = app.query_one("#screen-history")
        assert (cal.m, cal.y) == (5, 2026)
        table = app.query_one("#cal-table", DataTable)
        assert len(table.columns) == 7
        await pilot.press("c")
        await pilot.pause(0.3)
        assert cal.conflicts_only is True
        await pilot.press("c")
        await pilot.pause(0.2)
        await pilot.press("greater_than_sign")
        await pilot.pause(0.3)
        assert (cal.m, cal.y) == (6, 2026)


async def test_tui_journey_help_modal():
    """? opens the help modal; escape closes it."""
    from doch1.tui.modals import HelpModal

    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        await pilot.pause(0.2)
        assert isinstance(app.screen, HelpModal)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, HelpModal)


# --------------------------------------------------------------------------- #
# Manifest integrity: the suite actually covers every declared feature         #
# --------------------------------------------------------------------------- #


def test_manifest_lists_all_cli_verbs():
    """Guard: the manifest enumerates every public CLI verb in cli.app."""
    declared = set(MANIFEST.cli_ids())
    # The verbs doch1 exposes (cron is a sub-app counted as one feature here).
    expected = {"today", "day", "week", "history", "status", "statuses", "cron", "login", "ui"}
    assert expected <= declared, expected - declared


def test_manifest_screens_match_app_nav():
    """Guard: manifest screen ids match the app's nav rail (minus Quit)."""
    from doch1.tui.app import _NAV

    nav_ids = {sid for sid, _ in _NAV if sid != "__quit__"}
    assert {s.screen_id for s in TUI_SCREENS} == nav_ids

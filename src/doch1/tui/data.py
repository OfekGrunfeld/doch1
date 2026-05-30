"""Thin data layer bridging the Textual UI to the existing api/client.

Import-time-cheap: depends only on the existing api/cli modules and stdlib —
NO textual import here, so cli.py can reuse fill_week_plan() without pulling in
the interactive stack. The synchronous functions below are meant to be called
from Textual worker threads (each opens/closes its own BrowserClient, because
Playwright's sync API must run on the thread that created it).
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import api


def grid_window(m: int, y: int) -> tuple[date, date]:
    """(Sunday on/before the 1st, Saturday) of a fixed 6-week grid.

    Always spans exactly 42 days so the grid is a stable 6x7 regardless of
    month, including 4/5-week months — the source of the "fixed 6x7" layout.
    """
    weeks = _calendar.Calendar(firstweekday=6).monthdatescalendar(y, m)
    grid_start = weeks[0][0]
    grid_end = grid_start + timedelta(days=41)  # fixed 6 weeks * 7 - 1
    return grid_start, grid_end


# ---------- calendar month merge (pure, testable) ----------


@dataclass
class CellInfo:
    """One calendar day's render payload.

    kind is one of:
      "history"   — a past/present reported day (from member_history)
      "scheduled" — a future day already scheduled (from list_scheduled)
      "none"      — in-month but neither reported nor scheduled
    hd is the api.HistoryDay for history/scheduled days (synthesized for
    scheduled), else None.
    """

    kind: str
    hd: object | None = None


def _scheduled_to_history_day(day: dict) -> api.HistoryDay | None:
    """Adapt a list_scheduled() day dict into a HistoryDay (reported fields).

    Scheduled days carry reportedMainName / secondaryStatusReported and a
    reportedStatusCode; they have no approved/determined status yet. Returns
    None when the date is missing/unparseable.
    """
    ds = day.get("date")
    if not ds:
        return None
    try:
        d = datetime.fromisoformat(ds).date()
    except ValueError:
        return None
    reported = api._join(day.get("reportedMainName"), day.get("secondaryStatusReported"))
    return api.HistoryDay(
        date=d,
        reported=reported,
        determined="",
        approved="",
        in_base=bool(day.get("inBase")),
        conflict=False,
        note=(day.get("note") or "").strip(),
    )


def merge_month_cells(history_days, scheduled_days, m: int, y: int) -> dict[date, CellInfo]:
    """Merge history + scheduled days into a per-date cell map for month m/y.

    Pure (no I/O). history_days is a list of api.HistoryDay; scheduled_days is
    the raw list of dicts from api.list_scheduled(...)["days"]. Only dates that
    fall inside month m/year y are included. History wins over scheduled when
    both name the same date (a reported day is authoritative over a plan).
    """
    cells: dict[date, CellInfo] = {}
    for sd in scheduled_days or []:
        hd = _scheduled_to_history_day(sd)
        if hd is None or hd.date.month != m or hd.date.year != y:
            continue
        cells[hd.date] = CellInfo(kind="scheduled", hd=hd)
    for hd in history_days or []:
        if hd.date.month != m or hd.date.year != y:
            continue
        cells[hd.date] = CellInfo(kind="history", hd=hd)
    return cells


def merge_grid_cells(
    history_days, scheduled_days, grid_start: date, grid_end: date
) -> dict[date, CellInfo]:
    """Merge history + scheduled into a per-date map for the inclusive window
    [grid_start, grid_end]. History wins over scheduled on a shared date.

    Unlike merge_month_cells (month-only), this keeps prev-month tail and
    next-month head dates so adjacent-month spill cells render with real status.
    """
    cells: dict[date, CellInfo] = {}
    for sd in scheduled_days or []:
        hd = _scheduled_to_history_day(sd)
        if hd is None or not (grid_start <= hd.date <= grid_end):
            continue
        cells[hd.date] = CellInfo(kind="scheduled", hd=hd)
    for hd in history_days or []:
        if not (grid_start <= hd.date <= grid_end):
            continue
        cells[hd.date] = CellInfo(kind="history", hd=hd)
    return cells


# ---------- week-fill indicator (pure, single source of truth) ----------


def week_fill_state(rows) -> str:
    """Classify a week's report-coverage from fetch_week_status() rows.

    Pure (no I/O); decides only from `rows` (each a dict with keys date, action,
    effective, in_base, conflict, note). Returns one of "filled"/"partial"/"empty".

    A day still NEEDS a report if it is an unscheduled future day (action ==
    "future"), or it is today (action == "today") with a blank effective status.
    A day is COVERED if it is reported/scheduled: action == "skip-scheduled", or
    a "today" with a non-empty effective.
    """
    needs = False
    covered = False
    for r in rows:
        action = r.get("action")
        effective = (r.get("effective") or "").strip()
        if action == "future" or (action == "today" and not effective):
            needs = True
        if action == "skip-scheduled" or (action == "today" and effective):
            covered = True
    if not covered:
        return "empty"
    return "partial" if needs else "filled"


# ---------- shared week-fill plan (single source of truth) ----------


def fill_week_plan(client, days, today_d, status=None):
    """Compute + execute the week-fill for `days`, returning the results list.

    Mirrors the exact branching cli.week used to inline; extracted so BOTH the
    non-interactive `doch1 week` command and the TUI call identical logic (no
    drift). Returns (results, failures) where results is the list of
    {"date","action","ok"} dicts and failures is the list of "DD.MM" strings.

    `status` (a statuses.Status) is threaded uniformly into both write actions;
    defaults to the at-base status when None.
    """
    status = status or api.DEFAULT_STATUS
    months = {(d.month, d.year) for d in days}
    filled, min_d, max_d = api.scheduled_window(client, months)
    results, failures = [], []
    for d in days:
        if d < today_d:
            action, ok = "skip-past", None
        elif d == today_d:
            ok, action = api.report_today(client, status), "today"
        elif d in filled:
            action, ok = "skip-scheduled", None
        elif (min_d and d < min_d) or (max_d and d > max_d):
            action, ok = "skip-window", None
        else:
            ok, action = api.report_future(client, d, status=status), "future"
        if ok is False:
            failures.append(d.strftime("%d.%m"))
        results.append({"date": d.isoformat(), "action": action, "ok": ok})
    return results, failures


def plan_week(client, days, today_d):
    """Read-only preview of what fill_week_plan WOULD do — no writes.

    Returns a list of {"date","action"} where action is one of
    skip-past / today / skip-scheduled / skip-window / future. Used to render
    the "Planned action" column and to know which days a Fill would write.
    """
    months = {(d.month, d.year) for d in days}
    filled, min_d, max_d = api.scheduled_window(client, months)
    out = []
    for d in days:
        if d < today_d:
            action = "skip-past"
        elif d == today_d:
            action = "today"
        elif d in filled:
            action = "skip-scheduled"
        elif (min_d and d < min_d) or (max_d and d > max_d):
            action = "skip-window"
        else:
            action = "future"
        out.append({"date": d.isoformat(), "action": action})
    return out


# ---------- worker-thread entry points (open/close a client each call) ----------


def _client(cfg):
    from ..cli import _client as cli_client

    return cli_client(cfg)


def fetch_history(cfg, m: int, y: int):
    """list[api.HistoryDay] for month m / year y."""
    with _client(cfg) as client:
        return api.member_history(client, m, y)


def fetch_calendar_month(cfg, m: int, y: int) -> dict[date, CellInfo]:
    """Merged per-date cell map for the FULL visible 6-week grid of month m/y.

    Combines api.member_history (past/present reported days) with
    api.list_scheduled (future scheduled days) across the m-1/m/m+1 months the
    grid window touches, so adjacent-month spill cells render with real status.
    """
    grid_start, grid_end = grid_window(m, y)
    months = {(grid_start.month, grid_start.year), (m, y), (grid_end.month, grid_end.year)}
    history: list = []
    scheduled: list = []
    with _client(cfg) as client:
        for fm, fy in months:
            history += api.member_history(client, fm, fy)
            try:
                scheduled += api.list_scheduled(client, fm, fy).get("days", [])
            except api.Doch1Error as exc:
                if exc.auth_expired:
                    raise
    return merge_grid_cells(history, scheduled, grid_start, grid_end)


def fetch_week_plan(cfg, days):
    """Read-only plan for the given Sun..Sat days (list of {date,action})."""
    with _client(cfg) as client:
        return plan_week(client, days, date.today())


def fetch_week_status(cfg, days):
    """Per-day status for a week, joining scheduled-window plan with history.

    Returns list of dicts: {date, action, effective, in_base, conflict, note}.
    history only covers past/present days; future days have empty status.
    """
    with _client(cfg) as client:
        plan = plan_week(client, days, date.today())
        months = {(d.month, d.year) for d in days}
        hist: dict = {}
        for m, y in months:
            for hd in api.member_history(client, m, y):
                hist[hd.date] = hd
    rows = []
    for p in plan:
        d = date.fromisoformat(p["date"])
        row_hd = hist.get(d)
        rows.append(
            {
                "date": p["date"],
                "action": p["action"],
                "effective": row_hd.effective if row_hd else "",
                "in_base": bool(row_hd.in_base) if row_hd else False,
                "conflict": bool(row_hd.conflict) if row_hd else False,
                "note": row_hd.note if row_hd else "",
            }
        )
    return rows


def fetch_today_status(cfg):
    """Status for date.today(): {effective,in_base,conflict,note} or empty."""
    t = date.today()
    with _client(cfg) as client:
        days = api.member_history(client, t.month, t.year)
    for hd in days:
        if hd.date == t:
            return {
                "effective": hd.effective,
                "in_base": hd.in_base,
                "conflict": hd.conflict,
                "note": hd.note,
                "found": True,
            }
    return {"effective": "", "in_base": False, "conflict": False, "note": "", "found": False}


def do_report_today(cfg) -> bool:
    with _client(cfg) as client:
        return api.report_today(client)


def do_fill_week(cfg, days):
    """Execute the week fill. Returns (results, failures)."""
    with _client(cfg) as client:
        return fill_week_plan(client, days, date.today())


def probe_auth(cfg):
    """(ok: bool, transport: str | None, error: str | None)."""
    from ..session import state_path

    try:
        with _client(cfg) as client:
            api.list_scheduled(client, date.today().month, date.today().year)
    except api.Doch1Error as e:
        return False, None, str(e)
    src = "browser-session" if state_path().exists() else "cookie"
    return True, src, None

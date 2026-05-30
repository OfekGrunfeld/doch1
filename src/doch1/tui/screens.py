"""Screen widgets swapped inside the app's ContentSwitcher.

Each screen is a Vertical container holding a view + an action bar. Data is
fetched on Textual worker threads (thread=True) because the underlying
BrowserClient drives Playwright's sync API. Screens never touch the network on
the event loop; they post results back via call_from_thread / worker messages.

The DataService indirection lets tests inject a fake (no Playwright).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, LoadingIndicator, Static

from .. import render
from ..cli import _t, _week_days
from . import data
from .modals import ConfirmModal, DayDetailModal

# Day-of-week labels (Sun..Sat) reused across week screens.
_DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _status_cell(effective: str) -> Text:
    """Translated status as a colored rich Text (renders natively in DataTable)."""
    return render.status_badge(_t(effective)) if effective else Text("-", style="dim")


def _base_cell(in_base: bool) -> Text:
    return render.base_marker(in_base)


def _conflict_cell(conflict: bool) -> Text:
    return render.conflict_flag(conflict)


# ---------- Today ----------


class TodayScreen(Vertical):
    BINDINGS = [
        ("enter", "refresh", "Refresh"),
    ]

    def __init__(self, service) -> None:
        super().__init__(id="screen-today")
        self.service = service

    def compose(self) -> ComposeResult:
        yield Static("Today", classes="screen-title")
        yield Static("loading…", id="today-card", classes="card")
        yield Static("[b]r[/b] report at base    [b]enter[/b] refresh", classes="actionbar")

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#today-card", Static).update("loading…")
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        try:
            st = self.service.fetch_today_status()
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self._show_error, str(e))
            return
        self.app.call_from_thread(self._show, st)

    def _show(self, st: dict) -> None:
        t = date.today()
        if not st.get("found"):
            body = Text.assemble(
                Text(t.strftime("%A %d.%m.%Y"), style="bold"),
                "\n\n",
                Text("No report yet for today.", style="yellow"),
                "\n",
                Text("Press r to report 'at base'.", style="dim"),
            )
        else:
            body = Text.assemble(
                Text(t.strftime("%A %d.%m.%Y"), style="bold"),
                "\n\n",
                Text("Status: "),
                _status_cell(st["effective"]),
                "\n",
                Text("In base: "),
                _base_cell(st["in_base"]),
                "\n",
                Text("Conflict: "),
                _conflict_cell(st["conflict"]) if st["conflict"] else Text("none", style="dim"),
                "\n",
                Text("Note: "),
                Text(st["note"] or "—", style="dim"),
            )
        self.query_one("#today-card", Static).update(body)

    def _show_error(self, msg: str) -> None:
        self.query_one("#today-card", Static).update(Text(f"✗ {msg}", style="red"))

    def action_report(self) -> None:
        self.query_one("#today-card", Static).update("reporting…")
        self.run_worker(self._report, thread=True, exclusive=True)

    def _report(self) -> None:
        try:
            ok = self.service.do_report_today()
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self.app.notify, f"Report failed: {e}", severity="error")
            self.app.call_from_thread(self.action_refresh)
            return
        msg = "Reported: at base ✓" if ok else "Report rejected ✗"
        sev = "information" if ok else "error"
        self.app.call_from_thread(self.app.notify, msg, severity=sev)
        self.app.call_from_thread(self.action_refresh)


# ---------- Week (This / Next) ----------


class WeekScreen(Vertical):
    BINDINGS = [
        ("enter", "refresh", "Refresh"),
    ]

    def __init__(self, service, *, offset_weeks: int, screen_id: str, label: str) -> None:
        super().__init__(id=screen_id)
        self.service = service
        self.offset_weeks = offset_weeks
        self.label = label

    def _days(self) -> list[date]:
        anchor = self.service.week_anchor() + timedelta(days=7 * self.offset_weeks)
        return _week_days(anchor)

    def compose(self) -> ComposeResult:
        yield Static(self.label, classes="screen-title", id=f"{self.id}-title")
        t = DataTable(id=f"{self.id}-table", zebra_stripes=True, cursor_type="row")
        t.add_columns("Day", "Date", "Status", "Base", "Conflict", "Planned action")
        yield t
        yield LoadingIndicator(id=f"{self.id}-loading", classes="hidden")
        yield Static("[b]f[/b] fill week    [b]enter[/b] refresh", classes="actionbar")

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one(f"#{self.id}-loading", LoadingIndicator).remove_class("hidden")
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        days = self._days()
        try:
            rows = self.service.fetch_week_status(days)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self.app.notify, f"Load failed: {e}", severity="error")
            self.app.call_from_thread(self._stop_loading)
            return
        self.app.call_from_thread(self._populate, rows)

    def _stop_loading(self) -> None:
        self.query_one(f"#{self.id}-loading", LoadingIndicator).add_class("hidden")

    def _populate(self, rows: list[dict]) -> None:
        table = self.query_one(f"#{self.id}-table", DataTable)
        table.clear()
        for r in rows:
            d = date.fromisoformat(r["date"])
            table.add_row(
                Text(_DOW[(d.weekday() + 1) % 7], style="dim"),
                d.strftime("%d.%m"),
                _status_cell(r["effective"]),
                _base_cell(r["in_base"]),
                _conflict_cell(r["conflict"]),
                render.humanize_action(r["action"]),
                key=r["date"],
            )
        self._set_title(rows)
        self._stop_loading()

    def _set_title(self, rows: list[dict]) -> None:
        """Update the screen header with a week-fill icon + label."""
        state = data.week_fill_state(rows)
        title = self.query_one(f"#{self.id}-title", Static)
        title.update(
            Text.assemble(Text(self.label + "  ", style="bold"), render.week_fill_badge(state))
        )

    def action_fill(self) -> None:
        days = self._days()
        try:
            plan = self.service.fetch_week_plan_sync(days)
        except Exception as e:  # noqa: BLE001
            self.app.notify(f"Plan failed: {e}", severity="error")
            return
        writable = [p for p in plan if p["action"] in ("today", "future")]
        if not writable:
            self.app.notify("Nothing to fill — week already up to date.", severity="warning")
            return
        lines = [
            f"  {date.fromisoformat(p['date']).strftime('%a %d.%m')}  "
            f"→ {render.humanize_action(p['action'])}"
            for p in writable
        ]
        self.app.push_screen(
            ConfirmModal(f"Fill {self.label.lower()} — {len(writable)} day(s)?", lines),
            self._on_confirm,
        )

    def _on_confirm(self, ok: bool | None) -> None:
        if not ok:
            self.app.notify("Cancelled — nothing written.")
            return
        self.query_one(f"#{self.id}-loading", LoadingIndicator).remove_class("hidden")
        self.run_worker(self._fill, thread=True, exclusive=True)

    def _fill(self) -> None:
        days = self._days()
        try:
            results, failures = self.service.do_fill_week(days)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self.app.notify, f"Fill failed: {e}", severity="error")
            self.app.call_from_thread(self._stop_loading)
            return
        self.app.call_from_thread(self._after_fill, results, failures)

    def _after_fill(self, results: list[dict], failures: list[str]) -> None:
        table = self.query_one(f"#{self.id}-table", DataTable)
        for r in results:
            try:
                table.update_cell(r["date"], "Planned action", render.result_badge(r["ok"]))
            except Exception:  # noqa: BLE001 — row key may be gone after refresh
                pass
        if failures:
            self.app.notify("Failures: " + ", ".join(failures), severity="error")
        else:
            self.app.notify("Week filled ✓")
        self.action_refresh()


# ---------- Calendar (primary History/overview) ----------


class CalendarNav(Static):
    """Centered '‹  May 2026  ›   23 days · 4 scheduled · 1 conflict'.

    Bold-accent arrows are click targets that fire prev/next month on the
    active screen. The whole widget re-renders when the screen calls set_state.
    """

    def set_state(self, month_label: str, tail: str) -> None:
        body = Text.assemble(
            Text(" ‹ ", style="bold yellow"),
            Text(month_label, style="bold"),
            Text(" › ", style="bold yellow"),
        )
        if tail:
            body.append("   ")
            body.append(Text(tail, style="dim"))
        self.update(body)

    def on_click(self, event) -> None:
        # Left edge -> prev, right edge -> next (arrows live at the edges).
        w = self.size.width or 1
        if event.x <= 3:
            self.app.action_screen_action("action_prev_month")
        elif event.x >= w - 4:
            self.app.action_screen_action("action_next_month")


class CalendarScreen(Vertical):
    """Month grid: a 7-col (Sun..Sat) DataTable, one row per ISO calendar week.

    cursor_type="cell" gives native arrow-key day/week navigation. Each cell is
    a 2-line rich Text via render.calendar_cell. Enter on a cell opens a
    DayDetailModal. Month nav via <,>,PageUp,PageDown; t jumps to today; c
    dims non-conflict cells. Keeps id 'screen-history' so the 4 binding/nav index
    are unchanged. Data comes from service.fetch_calendar_month(m, y) on a
    worker, which overlays BOTH reported history days and future scheduled days
    (the latter rendered in a distinct dimmer "scheduled" style).
    """

    BINDINGS = [
        Binding("less_than_sign,comma", "prev_month", "Prev month", show=True),
        Binding("greater_than_sign,full_stop", "next_month", "Next month", show=True),
        Binding("pageup", "prev_month", "Prev month", show=False),
        Binding("pagedown", "next_month", "Next month", show=False),
        Binding("enter", "open_day", "Detail", show=True),
        Binding("t", "today", "Today", show=True),
        Binding("c", "toggle_conflicts", "Conflicts", show=True),
    ]

    def __init__(self, service) -> None:
        super().__init__(id="screen-history")
        self.service = service
        t = date.today()
        self.m, self.y = t.month, t.year
        self.conflicts_only = False
        # date <-> (row, col) maps for cursor translation, rebuilt each populate.
        self._cell_dates: dict[tuple[int, int], date] = {}
        # date -> data.CellInfo (kind in history/scheduled), rebuilt each load.
        self._cells: dict[date, object] = {}
        # date to reseat the cursor on after a roll-over flip (consumed once).
        self._pending_cursor_date: date | None = None
        # True while we programmatically rebuild/reseat the grid: ignore the
        # transient CellHighlighted events DataTable emits during clear/add_row
        # and during _restore_cursor (which would otherwise loop on spill cells).
        self._suppress_rollover: bool = False

    def compose(self) -> ComposeResult:
        yield Static("Calendar", classes="screen-overline", id="cal-overline")
        yield CalendarNav(id="cal-nav")
        yield Static(
            "[b]<[/b],[b],[/b] prev   [b]>[/b],[b].[/b] next   "
            "[b]PgUp/PgDn[/b] same   [b]t[/b] today   "
            "[dim]or arrow past the edge[/dim]",
            id="cal-nav-hints",
            classes="actionbar",
        )
        t = DataTable(id="cal-table", cursor_type="cell", zebra_stripes=False)
        t.add_columns("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        yield t
        yield Static(render.calendar_legend(), id="cal-legend", classes="actionbar")
        yield Static("", id="history-empty", classes="hidden card")
        yield LoadingIndicator(id="history-loading", classes="hidden")
        yield Static(
            "[b]←→↑↓[/b] day   [b]enter[/b] detail   [b]<[/b]/[b]>[/b] month   "
            "[b]t[/b] today   [b]c[/b] conflicts-only",
            classes="actionbar",
        )

    def on_mount(self) -> None:
        self.action_refresh()
        if not getattr(self.app, "_cal_tip_shown", False):
            self.app._cal_tip_shown = True
            self.app.notify("Tip: press < / > or arrow past the edge to change month", timeout=4)

    def _update_nav(self, n_total: int = 0, n_sched: int = 0, n_conf: int = 0) -> None:
        label = date(self.y, self.m, 1).strftime("%B %Y")
        if self.conflicts_only:
            label += "  (conflicts only)"
        parts = [f"{n_total} days"]
        if n_sched:
            parts.append(f"{n_sched} scheduled")
        if n_conf:
            parts.append(f"{n_conf} conflict{'s' if n_conf != 1 else ''}")
        self.query_one("#cal-nav", CalendarNav).set_state(label, " · ".join(parts))

    def action_refresh(self) -> None:
        self._update_nav()
        self.query_one("#history-loading", LoadingIndicator).remove_class("hidden")
        self.run_worker(self._load, thread=True, exclusive=True)

    def _load(self) -> None:
        try:
            cells = self.service.fetch_calendar_month(self.m, self.y)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self.app.notify, f"Load failed: {e}", severity="error")
            self.app.call_from_thread(
                lambda: self.query_one("#history-loading", LoadingIndicator).add_class("hidden")
            )
            return
        self.app.call_from_thread(self._populate, cells)

    def _populate(self, cells) -> None:
        self.query_one("#history-loading", LoadingIndicator).add_class("hidden")
        self._suppress_rollover = True
        self._cells = dict(cells)
        # Counts reflect the FOCAL month only (spill days are not "this month").
        focal = [ci for d, ci in self._cells.items() if d.month == self.m and d.year == self.y]
        n_total = len(focal)
        n_sched = sum(1 for ci in focal if ci.kind == "scheduled")
        n_conf = sum(1 for ci in focal if ci.hd is not None and ci.hd.conflict)
        table = self.query_one("#cal-table", DataTable)
        empty = self.query_one("#history-empty", Static)
        legend = self.query_one("#cal-legend", Static)
        table.clear()
        self._cell_dates = {}

        if not self._cells:
            table.add_class("hidden")
            legend.add_class("hidden")
            empty.remove_class("hidden")
            empty.update(
                Text(
                    f"No conflicts in {self.m:02d}/{self.y}. ✓"
                    if self.conflicts_only
                    else f"No reports for {self.m:02d}/{self.y}.",
                    style="dim",
                )
            )
            self._update_nav()
            self.call_after_refresh(self._end_suppress_rollover)
            return

        empty.add_class("hidden")
        table.remove_class("hidden")
        legend.remove_class("hidden")
        today = date.today()
        grid_start, _ = data.grid_window(self.m, self.y)
        for r in range(6):
            cells_row = []
            for c in range(7):
                day = grid_start + timedelta(days=r * 7 + c)
                in_month = day.month == self.m and day.year == self.y
                ci = self._cells.get(day)
                hd = ci.hd if ci else None
                scheduled = bool(ci and ci.kind == "scheduled")
                dim = bool(self.conflicts_only and in_month and not (hd and hd.conflict))
                cells_row.append(
                    render.calendar_cell(
                        day,
                        hd,
                        in_month=in_month,
                        is_today=(day == today),
                        translate=_t,
                        dim=dim,
                        scheduled=scheduled,
                    )
                )
                self._cell_dates[(r, c)] = day  # ALL cells, incl. spill
            table.add_row(*cells_row, height=2)

        self._update_nav(n_total, n_sched, n_conf)
        self._restore_cursor()
        self.call_after_refresh(self._end_suppress_rollover)

    def _end_suppress_rollover(self) -> None:
        self._suppress_rollover = False

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Arrow onto a spill cell -> flip to that month, reseat on the date.

        Covers all four arrows, Home/End and mouse uniformly. No reentrancy:
        _restore_cursor reseats onto an in-month date, so the month check below
        is false and no flip loop occurs.
        """
        if self._suppress_rollover:
            return
        d = self._cell_dates.get((event.coordinate.row, event.coordinate.column))
        if d is None:
            return
        if d.month != self.m or d.year != self.y:
            self.m, self.y = d.month, d.year
            self._pending_cursor_date = d  # consumed by _restore_cursor
            self.action_refresh()

    def _restore_cursor(self) -> None:
        """Seat the cursor on the pending/roll-over date, else today, else the
        first in-month day. Always lands on an in-month cell."""
        table = self.query_one("#cal-table", DataTable)
        want = self._pending_cursor_date or date.today()
        self._pending_cursor_date = None
        target = None
        for coord, d in self._cell_dates.items():
            if d == want and d.month == self.m and d.year == self.y:
                target = coord
                break
        if target is None:  # want not in this month -> first in-month
            in_month = {
                rc: d
                for rc, d in self._cell_dates.items()
                if d.month == self.m and d.year == self.y
            }
            if in_month:
                target = min(in_month, key=lambda rc: in_month[rc])
        if target is not None:
            table.cursor_coordinate = Coordinate(*target)

    def _cursor_date(self) -> date | None:
        table = self.query_one("#cal-table", DataTable)
        coord = table.cursor_coordinate
        return self._cell_dates.get((coord.row, coord.column))

    def action_open_day(self) -> None:
        d = self._cursor_date()
        if d is None:
            return  # spill cell — nothing to open
        ci = self._cells.get(d)
        self.app.push_screen(
            DayDetailModal(d, ci.hd if ci else None, translate=_t), self._on_detail_close
        )

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        self.action_open_day()

    def _on_detail_close(self, result) -> None:
        if result == "report":
            self.app.notify("Reporting is available from Today / week fill.", severity="warning")

    def action_next_month(self) -> None:
        self.m += 1
        if self.m > 12:
            self.m, self.y = 1, self.y + 1
        self.action_refresh()

    def action_prev_month(self) -> None:
        self.m -= 1
        if self.m < 1:
            self.m, self.y = 12, self.y - 1
        self.action_refresh()

    def action_today(self) -> None:
        t = date.today()
        self.m, self.y = t.month, t.year
        self.action_refresh()

    def action_toggle_conflicts(self) -> None:
        self.conflicts_only = not self.conflicts_only
        self.action_refresh()


# Backwards-compatible alias (id is still 'screen-history').
HistoryScreen = CalendarScreen


# ---------- Status / Login ----------


class StatusScreen(Vertical):
    BINDINGS = [
        ("enter", "refresh", "Re-probe"),
        Binding("l", "login", "Auto login"),
        Binding("m", "login_manual", "Manual login"),
    ]

    def __init__(self, service) -> None:
        super().__init__(id="screen-status")
        self.service = service

    def compose(self) -> ComposeResult:
        yield Static("Status", classes="screen-title")
        yield Static("probing…", id="status-card", classes="card")
        yield Static(
            "[b]l[/b] auto login   [b]m[/b] manual login   [b]enter[/b] re-probe",
            classes="actionbar",
        )

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one("#status-card", Static).update("probing…")
        self.run_worker(self._probe, thread=True, exclusive=True)

    def _probe(self) -> None:
        ok, transport, error = self.service.probe_auth()
        self.app.call_from_thread(self._show, ok, transport, error)

    def _show(self, ok: bool, transport, error) -> None:
        if ok:
            body = Text.assemble(
                Text("✓ ", style="green"),
                Text("authenticated", style="bold green"),
                Text(f" via {transport}", style="dim"),
            )
        else:
            body = Text.assemble(
                Text("✗ ", style="red"),
                Text("not authenticated", style="bold red"),
                Text(f": {error}" if error else "", style="dim"),
                "\n\n",
                Text("Press l to log in.", style="dim"),
            )
        self.query_one("#status-card", Static).update(body)
        self.app.set_transport(transport if ok else None)

    def action_login(self) -> None:  # bound to 'l' -> AUTO
        self._do_login(manual=False)

    def action_login_manual(self) -> None:  # bound to 'm' -> MANUAL/assisted
        self._do_login(manual=True)

    def _do_login(self, *, manual: bool) -> None:
        from ..api import Doch1Error

        label = "manual/assisted" if manual else "auto"
        # suspend() drops the alt-screen; with no real terminal the browser/SMS
        # prompt are unusable, so refuse rather than freeze a dead terminal.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            self.app.notify("Login needs a real terminal (no TTY).", severity="error")
            return
        self.app.notify(f"Launching {label} login… (browser + terminal)", timeout=3)
        self.query_one("#status-card", Static).update(f"opening {label} login…")
        try:
            with self.app.suspend():  # drops alt-screen, restores on exit
                print(
                    f"\n=== doch1 {label} login — finish here / in the browser ===\n",
                    flush=True,
                )
                self.service.login(manual=manual)  # BLOCKING, main thread, real tty
        except Doch1Error as e:
            self.app.notify(f"Login failed: {e}", severity="error", timeout=8)
        except Exception as e:  # noqa: BLE001 — never crash the TUI
            self.app.notify(f"Login error: {e}", severity="error", timeout=8)
        else:
            self.app.notify("Login finished — re-probing session…", timeout=3)
        finally:
            self.action_refresh()  # always re-run the auth probe -> _show

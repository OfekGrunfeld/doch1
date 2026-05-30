"""Shared rich formatters — single source of truth for non-interactive look.

Import-time-cheap: depends on rich only (no textual). Used by cli.py human
branches; the TUI may later reuse the color map / glyph constants / badge
helpers (which return rich Text, renderable natively in Textual DataTable cells).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def get_console() -> Console:
    """One console per CLI invocation.

    highlight=False keeps numbers/paths unstyled. rich auto-detects the tty:
    when stdout is not a terminal it disables color + box drawing, so cron logs
    stay clean. NO_COLOR is honored automatically.
    """
    return Console(highlight=False)


# ---------- color map (define once) ----------

STATUS_COLORS = {
    "At base": "green",
    "Present": "green",
    "On duty off-base": "cyan",
    "Annual leave": "blue",
    "Sick leave": "blue",
    "Sick leave (medical)": "blue",
    "Off base": "yellow",
    "Abroad": "yellow",
}

GLYPH_OK = "✓"  # ✓
GLYPH_FAIL = "✗"  # ✗
GLYPH_SKIP = "–"  # –
GLYPH_CONFLICT = "⚠"  # ⚠
GLYPH_NOBASE = "·"  # ·
GLYPH_ARROW = "→"  # →
GLYPH_DOT = "●"  # ● leave / off-base / duty marker
GLYPH_SCHEDULED = "○"  # ○ future scheduled (planned, not yet reported)


def status_color(effective_en: str) -> str:
    """Color for an English (already-translated) effective status."""
    # The translated string may join fields with " / "; key off the first part.
    head = effective_en.split(" / ")[0].strip()
    return STATUS_COLORS.get(head, "default")


def status_badge(effective_en: str) -> Text:
    return Text(effective_en or "-", style=status_color(effective_en))


def base_marker(in_base: bool) -> Text:
    if in_base:
        return Text(GLYPH_OK, style="green")
    return Text(GLYPH_NOBASE, style="dim")


def conflict_flag(conflict: bool) -> Text:
    if conflict:
        return Text(GLYPH_CONFLICT, style="bold yellow")
    return Text("")


def result_badge(ok: bool | None) -> Text:
    if ok is True:
        return Text(f"OK {GLYPH_OK}", style="green")
    if ok is False:
        return Text(f"FAIL {GLYPH_FAIL}", style="bold red")
    return Text(f"SKIP {GLYPH_SKIP}", style="dim")


def status_glyph(effective_en: str) -> str:
    """Compact one-char glyph for an English effective status (calendar line 2)."""
    color = status_color(effective_en)
    if not effective_en:
        return GLYPH_SKIP
    if color == "green":
        return GLYPH_OK
    return GLYPH_DOT  # leave/off-base/duty all render as a colored dot


def calendar_cell(
    day: date,
    hd,
    *,
    in_month: bool,
    is_today: bool,
    translate: Callable[[str], str] = lambda s: s,
    dim: bool = False,
    scheduled: bool = False,
) -> Text:
    """A 2-line rich Text for one DataTable calendar cell.

    Line 1: day number ([NN] reverse-bold for today, dim for spill days).
    Line 2: in_base marker + status glyph (colored via status_color), with a
    trailing conflict glyph. Reuses the exact render.py palette so the grid is
    byte-for-byte consistent with History/CLI. `hd` is a HistoryDay or None.

    When `scheduled` is True the day is a FUTURE planned report (not yet
    reported): it renders dimmer and uses GLYPH_SCHEDULED (a hollow ○ instead of
    the solid ●/✓) so the user can tell "already reported (past)" from
    "scheduled (future)".
    """
    num = f"{day.day:>2}"
    if is_today:
        line1 = Text(f"[{num}]", style="bold reverse")
    elif not in_month:
        line1 = Text(f" {num} ", style="dim")
    else:
        line1 = Text(f" {num} ", style="dim" if (dim or scheduled) else "")

    if hd is None:
        return Text.assemble(line1, "\n", Text("   ", style="dim"))

    eff = translate(hd.effective) if hd.effective else ""
    gstyle = status_color(eff)
    force_dim = dim or not in_month  # spill cells always read as muted
    if scheduled:
        glyph = GLYPH_SCHEDULED
        # dim the planned status so it reads as "tentative" vs a reported day.
        gstyle = "dim" if (force_dim or gstyle == "default") else f"dim {gstyle}"
    else:
        glyph = status_glyph(eff)
        if force_dim:
            gstyle = "dim"
    base = base_marker(bool(hd.in_base))
    line2 = Text.assemble(base, Text(glyph, style=gstyle))
    if hd.conflict:
        line2 = Text.assemble(
            line2, Text(GLYPH_CONFLICT, style="dim" if force_dim else "bold yellow")
        )
    return Text.assemble(line1, "\n", line2)


def calendar_legend() -> Text:
    """One-line glyph→meaning legend; reuses the same glyph constants."""
    return Text.assemble(
        Text(f"{GLYPH_OK} ", style="green"),
        Text("at base   ", style="dim"),
        Text(f"{GLYPH_DOT} ", style="blue"),
        Text("leave   ", style="dim"),
        Text(f"{GLYPH_DOT} ", style="yellow"),
        Text("off-base   ", style="dim"),
        Text(f"{GLYPH_DOT} ", style="cyan"),
        Text("duty   ", style="dim"),
        Text(f"{GLYPH_CONFLICT} ", style="bold yellow"),
        Text("conflict   ", style="dim"),
        Text(f"{GLYPH_SCHEDULED} ", style="dim"),
        Text("scheduled   ", style="dim"),
        Text("▒ ", style="dim"),
        Text("other month   ", style="dim"),
        Text(f"{GLYPH_SKIP} ", style="dim"),
        Text("no report", style="dim"),
    )


def week_fill_badge(state: str) -> Text:
    """Icon + label for a week-fill state ("filled"/"partial"/"empty").

    Reuses the shared glyph/color palette (no new colors invented): filled => a
    green GLYPH_OK, partial => a dim-yellow half-circle, empty => a dim
    GLYPH_SKIP. Returned as rich Text so it renders in both CLI and Textual.
    """
    if state == "filled":
        return Text.assemble(Text(GLYPH_OK, style="green"), Text(" filled", style="green"))
    if state == "partial":
        return Text.assemble(Text("◐", style="yellow"), Text(" partial", style="yellow"))
    return Text.assemble(Text(GLYPH_SKIP, style="dim"), Text(" empty", style="dim"))


def humanize_action(action: str) -> str:
    return {
        "today": "reported",
        "future": "will report",
        "skip-scheduled": "already filled",
        "skip-past": "past (skipped)",
        "skip-window": "outside window",
    }.get(action, action)


# ---------- renderers ----------


def render_history(
    console: Console,
    days,
    m: int,
    y: int,
    translate: Callable[[str], str],
    conflicts_only: bool = False,
) -> None:
    if not days:
        if conflicts_only:
            body = Text(f"No conflicts in {m:02d}/{y}. {GLYPH_OK}", justify="center", style="dim")
        else:
            body = Text(f"No reports for {m:02d}/{y}.", justify="center", style="dim")
        console.print(Panel(body, box=box.ROUNDED, border_style="dim"))
        return

    n_conf = sum(1 for d in days if d.conflict)
    title = f"History {m:02d}/{y} — {len(days)} day(s)"
    table = Table(
        box=box.SIMPLE_HEAVY, title=title, title_style="bold", expand=False, pad_edge=False
    )
    table.add_column("Date", justify="right", no_wrap=True)
    table.add_column("Day", style="dim", no_wrap=True)
    table.add_column("Status", no_wrap=False)
    table.add_column("Base", justify="center", no_wrap=True)
    table.add_column("Flag", justify="center", no_wrap=True)
    table.add_column("Note", style="dim", no_wrap=True, overflow="ellipsis", max_width=40)

    for d in days:
        if d.conflict and d.reported and d.approved and d.reported != d.approved:
            rep = translate(d.reported)
            app = translate(d.approved)
            status = Text.assemble(
                Text(rep, style=status_color(rep)),
                Text(f" {GLYPH_ARROW} ", style="dim"),
                Text(app, style=status_color(app)),
            )
        else:
            status = status_badge(translate(d.effective))
        table.add_row(
            d.date.strftime("%Y-%m-%d"),
            d.date.strftime("%a"),
            status,
            base_marker(d.in_base),
            conflict_flag(d.conflict),
            d.note,
        )

    if n_conf:
        table.caption = (
            f"{len(days)} day(s) reported — {n_conf} conflict(s)  (doch1 history --conflicts)"
        )
        table.caption_style = "yellow"
    console.print(table)


def render_week(console: Console, results, status=None) -> None:
    from datetime import date as _date

    table = Table(box=box.SIMPLE_HEAVY, expand=False, pad_edge=False)
    table.add_column("Result", no_wrap=True)
    table.add_column("Day", style="dim", no_wrap=True)
    table.add_column("Date", justify="right", no_wrap=True)
    table.add_column("Action", no_wrap=True)
    for r in results:
        d = _date.fromisoformat(r["date"])
        table.add_row(
            result_badge(r["ok"]),
            d.strftime("%a"),
            d.strftime("%d.%m"),
            humanize_action(r["action"]),
        )
    if status is not None:
        table.caption = f"status: {status.en} ({status.codes})"
        table.caption_style = status_color(status.en)
    console.print(table)


def render_status(
    console: Console,
    authenticated: bool,
    transport: str | None = None,
    error: str | None = None,
) -> None:
    if authenticated:
        console.print(
            Text.assemble(
                Text(f"{GLYPH_OK} ", style="green"),
                Text("authenticated", style="bold green"),
                Text(f" via {transport}", style="dim"),
            )
        )
    else:
        console.print(
            Text.assemble(
                Text(f"{GLYPH_FAIL} ", style="red"),
                Text("not authenticated", style="bold red"),
                Text(f": {error}" if error else "", style="dim"),
            )
        )


def render_today_result(console: Console, ok: bool, error: str | None = None, status=None) -> None:
    if ok:
        label = f"{status.en} ({status.codes})" if status is not None else "accepted at base"
        console.print(
            Text.assemble(
                Text(f"{GLYPH_OK} ", style="green"),
                Text("today: ", style="green"),
                Text(label, style=status_color(status.en) if status is not None else "green"),
            )
        )
    else:
        console.print(
            Text.assemble(
                Text(f"{GLYPH_FAIL} ", style="red"),
                Text(f"today: {error}" if error else "today: rejected", style="red"),
            )
        )

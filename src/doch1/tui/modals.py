"""Modal overlays: destructive-action confirmation + help cheat sheet."""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .. import render


class ConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses with True (confirm) or False (cancel)."""

    BINDINGS = [
        Binding("y,enter", "confirm", "Confirm", show=False),
        Binding("n,escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self._title = title
        self._lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-title")
            yield Static("\n".join(self._lines) or "(nothing to do)", id="confirm-body")
            yield Static("[b]y[/b]/Enter confirm   [b]n[/b]/Esc cancel", id="confirm-hint")
            yield Button("Confirm", variant="success", id="confirm-ok")
            yield Button("Cancel", variant="error", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-ok")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class DayDetailModal(ModalScreen):
    """Read-only detail for a single calendar day. Dismiss with Esc/Enter/q.

    Built from a HistoryDay (or None when the day has no report). `r` requests a
    report by dismissing with the literal string "report" so the calendar screen
    can act on it; any other close dismisses with None.
    """

    BINDINGS = [
        Binding("escape,enter,q", "close", "Close", show=False),
        Binding("r", "report", "Report", show=False),
    ]

    def __init__(self, day: date, hd, translate=lambda s: s) -> None:
        super().__init__()
        self._day = day
        self._hd = hd
        self._t = translate

    def _body(self) -> Text:
        d = self._day
        hd = self._hd
        head = Text(d.strftime("%A %d.%m.%Y"), style="bold")
        if hd is None:
            return Text.assemble(
                head,
                "\n\n",
                Text("No report for this day.", style="yellow"),
                "\n",
                Text("Press r to report 'at base' (today/future only).", style="dim"),
            )
        eff = self._t(hd.effective) if hd.effective else ""
        if hd.conflict and hd.reported and hd.approved and hd.reported != hd.approved:
            rep, app_ = self._t(hd.reported), self._t(hd.approved)
            status = Text.assemble(
                Text(rep, style=render.status_color(rep)),
                Text(f" {render.GLYPH_ARROW} ", style="dim"),
                Text(app_, style=render.status_color(app_)),
            )
        else:
            status = render.status_badge(eff)
        return Text.assemble(
            head,
            "\n\n",
            Text("Status:   "),
            status,
            "\n",
            Text("In base:  "),
            render.base_marker(bool(hd.in_base)),
            "\n",
            Text("Conflict: "),
            (render.conflict_flag(True) if hd.conflict else Text("none", style="dim")),
            "\n",
            Text("Note:     "),
            Text(hd.note or "—", style="dim"),
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("Day detail", id="help-title")
            yield Static(self._body(), id="help-body")
            yield Static("[b]r[/b] report   [b]Esc[/b]/Enter close", id="help-hint")

    def action_close(self) -> None:
        self.dismiss(None)

    def action_report(self) -> None:
        self.dismiss("report")


class HelpModal(ModalScreen):
    """Key-binding cheat sheet. Dismiss with ? or Esc."""

    BINDINGS = [
        Binding("question_mark,escape,q", "dismiss_help", "Close", show=False),
    ]

    HELP = """\
[b]Navigation[/b]
  1   Today          2   This week
  3   Next week      4   Calendar
  5   Status         6   Quit
  esc menu (nav rail)  tab cycle focus
  ?   this help     q  quit

[b]Nav rail (esc)[/b]
  up/down browse     enter  open screen

[b]Today[/b]
  r   report at base       enter  refresh

[b]This / Next week[/b]
  f   fill week (confirm)

[b]Calendar[/b]
  ←→↑↓   move day; arrow past the edge flips month
  <  >   (or , .)  prev / next month     PgUp/PgDn same
  click ‹ › in the header to change month
  t  today      c  conflicts-only      enter  day detail

[b]Status[/b]
  l   auto login    (headless email+password+SMS)
  m   manual login  (headed browser, you finish MFA)
  enter  re-probe session
"""

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("Keybindings", id="help-title")
            yield Static(self.HELP, id="help-body")
            yield Static("press [b]?[/b] or [b]Esc[/b] to close", id="help-hint")

    def action_dismiss_help(self) -> None:
        self.dismiss()

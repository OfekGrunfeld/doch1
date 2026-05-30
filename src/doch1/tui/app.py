"""Doch1App — the interactive Textual shell.

Layout: Header, a Horizontal split of a left nav rail (ListView) + a
ContentSwitcher main pane, and a Footer. Number keys 1-6 and the rail switch
screens. All network work happens on worker threads inside the screens, via a
DataService that wraps tui/data.py functions bound to the loaded config. Tests
inject a fake DataService (no Playwright).
"""

from __future__ import annotations

from datetime import date

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Header, Label, ListItem, ListView

from ..dates import default_week_anchor
from . import data as _data
from .modals import HelpModal
from .screens import CalendarScreen, StatusScreen, TodayScreen, WeekScreen

# Nav rail: (screen id, label). Index+1 is the number-key shortcut.
_NAV = [
    ("screen-today", "Today"),
    ("screen-thisweek", "This week"),
    ("screen-nextweek", "Next week"),
    ("screen-history", "Calendar"),
    ("screen-status", "Status"),
    ("__quit__", "Quit"),
]


class DataService:
    """Default service: real api calls via tui/data.py bound to cfg.

    Every fetch_* / do_* opens and closes its own BrowserClient on the calling
    (worker) thread. week_anchor() applies the Saturday rule. Override in tests.
    """

    def __init__(self, cfg: dict[str, str]) -> None:
        self.cfg = cfg

    def week_anchor(self) -> date:
        return default_week_anchor()

    def fetch_today_status(self):
        return _data.fetch_today_status(self.cfg)

    def do_report_today(self) -> bool:
        return _data.do_report_today(self.cfg)

    def fetch_week_status(self, days):
        return _data.fetch_week_status(self.cfg, days)

    def fetch_week_plan_sync(self, days):
        return _data.fetch_week_plan(self.cfg, days)

    def do_fill_week(self, days):
        return _data.do_fill_week(self.cfg, days)

    def fetch_history(self, m, y):
        return _data.fetch_history(self.cfg, m, y)

    def fetch_calendar_month(self, m, y):
        return _data.fetch_calendar_month(self.cfg, m, y)

    def probe_auth(self):
        return _data.probe_auth(self.cfg)

    def login(self, *, manual: bool = False) -> None:
        from ..session import login as do_login

        cfg = self.cfg
        pass_cfg = (cfg.get("DOCH1_PASS") or "").strip()
        password = pass_cfg or None  # blank/whitespace -> None (cli.py login)
        assisted = True if manual else None  # manual forces headed/assisted
        do_login(
            timeout_s=300,
            totp_seed=cfg.get("DOCH1_TOTP_SEED"),
            username=cfg.get("DOCH1_USER"),
            password=password,
            assisted=assisted,
        )


class Doch1App(App):
    CSS_PATH = "app.tcss"
    TITLE = "doch1"
    SUB_TITLE = "presence reporter"

    BINDINGS = [
        Binding("1", "nav('screen-today')", "Today"),
        Binding("2", "nav('screen-thisweek')", "This week"),
        Binding("3", "nav('screen-nextweek')", "Next week"),
        Binding("4", "nav('screen-history')", "Calendar"),
        Binding("5", "nav('screen-status')", "Status"),
        Binding("6", "quit", "Quit"),
        Binding("escape", "focus_nav", "Menu"),
        Binding("tab", "cycle_focus", "Focus", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q,ctrl+c", "quit", "Quit"),
        Binding("t", "screen_action('action_today')", "Today", show=False),
        Binding("pageup", "screen_action('action_prev_month')", "Prev", show=False),
        Binding("pagedown", "screen_action('action_next_month')", "Next", show=False),
        # Context actions delegated to the current screen (work regardless of
        # whether the nav rail or the table holds focus).
        Binding("r", "screen_action('action_report')", "Report", show=False),
        Binding("f", "screen_action('action_fill')", "Fill", show=False),
        Binding("c", "screen_action('action_toggle_conflicts')", "Conflicts", show=False),
        Binding("l", "screen_action('action_login')", "Login", show=False),
        Binding("m", "screen_action('action_login_manual')", "Manual login", show=False),
        Binding("less_than_sign,comma", "screen_action('action_prev_month')", "Prev", show=False),
        Binding(
            "greater_than_sign,full_stop", "screen_action('action_next_month')", "Next", show=False
        ),
    ]

    def __init__(self, service: DataService | None = None) -> None:
        super().__init__()
        self.service = service or DataService({})

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield ListView(
                *[ListItem(Label(label), id=f"nav-{sid}") for sid, label in _NAV],
                id="nav",
            )
            with ContentSwitcher(initial="screen-today", id="main"):
                yield TodayScreen(self.service)
                yield WeekScreen(
                    self.service, offset_weeks=0, screen_id="screen-thisweek", label="This week"
                )
                yield WeekScreen(
                    self.service, offset_weeks=1, screen_id="screen-nextweek", label="Next week"
                )
                yield CalendarScreen(self.service)
                yield StatusScreen(self.service)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#nav", ListView).index = 0

    # ---- transport indicator in the subtitle ----
    def set_transport(self, transport) -> None:
        self.sub_title = f"via {transport}" if transport else "not authenticated"

    # ---- navigation ----
    def action_nav(self, screen_id: str) -> None:
        if screen_id == "__quit__":
            self.exit()
            return
        self.query_one("#main", ContentSwitcher).current = screen_id
        for i, (sid, _) in enumerate(_NAV):
            if sid == screen_id:
                self.query_one("#nav", ListView).index = i
                break

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Switch screens on ACTIVATION (Enter / click) only — never on mere
        # highlight — so arrowing through the rail does not thrash screens and
        # the whole menu is keyboard-driven (esc -> rail, up/down, enter).
        sid = event.item.id.removeprefix("nav-") if event.item.id else ""
        if sid == "__quit__":
            self.exit()
        elif sid:
            self.query_one("#main", ContentSwitcher).current = sid

    # NOTE: on_list_view_highlighted is intentionally absent — highlight no
    # longer switches screens (see on_list_view_selected).

    def _current_screen_widget(self):
        switcher = self.query_one("#main", ContentSwitcher)
        if switcher.current is None:
            return None
        return self.query_one(f"#{switcher.current}")

    def action_screen_action(self, method: str) -> None:
        """Dispatch a context key to the active content screen, if it handles it."""
        widget = self._current_screen_widget()
        fn = getattr(widget, method, None) if widget else None
        if callable(fn):
            fn()

    def action_cycle_focus(self) -> None:
        self.focus_next()

    def action_focus_nav(self) -> None:
        """One-keystroke focus of the nav rail (esc) — no mouse needed."""
        self.query_one("#nav", ListView).focus()

    def action_help(self) -> None:
        self.push_screen(HelpModal())


def run_app() -> None:
    """Launch the interactive UI. Loads config, builds the real DataService."""
    from .. import api

    cfg = api.load_config()
    Doch1App(DataService(cfg)).run()

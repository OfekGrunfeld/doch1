"""FEATURE MANIFEST — the single source of truth for what doch1 must keep doing.

This is the machine-readable contract the EVAL SUITE (tests/eval/test_features.py)
asserts against the *real* code via hermetic fakes (no network / browser / secrets).
It exists so later changes (security fixes, self-improvement) cannot silently
delete or reshape a user-facing capability: if a contract here stops holding, an
eval fails and the regression is caught.

Two layers of features:

  CLI features  — every non-interactive verb the agent/cron drives. Each pins:
      * the ``--json`` envelope: which top-level keys the JSON object MUST carry
        (``json_keys``), and any nested shape we depend on (documented per-feature
        in the suite),
      * the exit-code semantics: 0 = ok, 1 = fail, with the ``auth_expired`` flag
        surfaced in the JSON error body on a dead session so an agent re-logs-in
        rather than blindly retries.

  TUI features  — the Textual screens + the keyboard journeys a human walks. Each
      pins the screen id, its nav key, and the journey a Pilot pass must replay.

Exit-code vocabulary (``ExitContract``):
    OK          -> process exit 0, JSON ``ok``/``authenticated`` truthy
    FAIL        -> process exit 1, JSON ``ok`` False (generic failure)
    AUTH_EXPIRED-> process exit 1, JSON carries ``auth_expired: true`` (re-login)

The manifest is pure data (no imports of the app), so it is import-cheap and can
be consumed by the runner (scripts/eval.py) and by an agent without side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExitContract(str, Enum):
    """Exit-code + JSON-body semantics a command can return."""

    OK = "ok"  # exit 0
    FAIL = "fail"  # exit 1, ok=False
    AUTH_EXPIRED = "auth_expired"  # exit 1, ok=False, auth_expired=True


@dataclass(frozen=True)
class CliFeature:
    """One user-facing CLI verb and its contract."""

    id: str
    command: str  # how it is invoked (argv joined), for docs
    argv: tuple[str, ...]  # the actual argv the suite runs
    summary: str
    # Top-level keys the --json success envelope MUST contain.
    json_keys: tuple[str, ...]
    # The "command" discriminator value inside the JSON envelope.
    json_command: str
    # Exit contracts this verb can produce (the suite asserts each reachable one).
    exits: tuple[ExitContract, ...]
    needs_client: bool = True  # touches transport (vs pure/local like statuses/cron)


@dataclass(frozen=True)
class TuiScreen:
    """One Textual screen reachable in the interactive shell."""

    screen_id: str
    label: str
    nav_key: str  # number key that switches to it


@dataclass(frozen=True)
class TuiJourney:
    """A keyboard journey a Pilot pass must be able to replay."""

    id: str
    summary: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    cli: tuple[CliFeature, ...] = field(default_factory=tuple)
    screens: tuple[TuiScreen, ...] = field(default_factory=tuple)
    journeys: tuple[TuiJourney, ...] = field(default_factory=tuple)

    def cli_ids(self) -> list[str]:
        return [f.id for f in self.cli]

    def feature(self, fid: str) -> CliFeature:
        for f in self.cli:
            if f.id == fid:
                return f
        raise KeyError(fid)


# --------------------------------------------------------------------------- #
# CLI FEATURES                                                                 #
# --------------------------------------------------------------------------- #

CLI_FEATURES: tuple[CliFeature, ...] = (
    CliFeature(
        id="today",
        command="doch1 today --json",
        argv=("today", "--json"),
        summary="Report TODAY as present-at-base (cron default).",
        json_keys=("command", "date", "ok", "status"),
        json_command="today",
        exits=(ExitContract.OK, ExitContract.FAIL, ExitContract.AUTH_EXPIRED),
    ),
    CliFeature(
        id="day",
        command="doch1 day DD.MM.YYYY --json",
        argv=("day", "02.06.2026", "--json"),
        summary="Schedule a single future day (or today).",
        json_keys=("command", "date", "ok", "status"),
        json_command="day",
        exits=(ExitContract.OK, ExitContract.FAIL, ExitContract.AUTH_EXPIRED),
    ),
    CliFeature(
        id="week",
        command="doch1 week [date] --json",
        argv=("week", "--json"),
        summary="Fill the Sun-Sat week: today + remaining future days.",
        json_keys=("command", "results", "status"),
        json_command="week",
        exits=(ExitContract.OK, ExitContract.FAIL, ExitContract.AUTH_EXPIRED),
    ),
    CliFeature(
        id="history",
        command="doch1 history [m] [y] --json",
        argv=("history", "5", "2026", "--json"),
        summary="View PAST reports (reported vs approved, conflicts, notes).",
        json_keys=("command", "month", "year", "days"),
        json_command="history",
        exits=(ExitContract.OK, ExitContract.AUTH_EXPIRED),
    ),
    CliFeature(
        id="status",
        command="doch1 status --json",
        argv=("status", "--json"),
        summary="Check whether the saved session is still valid.",
        json_keys=("command", "authenticated", "transport"),
        json_command="status",
        exits=(ExitContract.OK, ExitContract.AUTH_EXPIRED),
    ),
    CliFeature(
        id="statuses",
        command="doch1 statuses --json",
        argv=("statuses", "--json"),
        summary="List the selectable report statuses (code/Hebrew/English).",
        json_keys=("command", "statuses"),
        json_command="statuses",
        exits=(ExitContract.OK, ExitContract.FAIL),  # FAIL via --refresh (not wired)
        needs_client=False,
    ),
    CliFeature(
        id="cron",
        command="doch1 cron status --json",
        argv=("cron", "status", "--json"),
        summary="Install / list / remove the auto-fill cron jobs.",
        json_keys=("command", "jobs"),
        json_command="cron status",
        exits=(ExitContract.OK,),
        needs_client=False,
    ),
    CliFeature(
        id="login",
        command="doch1 login",
        argv=("login",),
        summary="Log in (headed/assisted or headless Entra) and save the session.",
        # login prints "OK session saved to ..." (human line, no --json envelope).
        json_keys=(),
        json_command="login",
        exits=(ExitContract.OK, ExitContract.FAIL),
        needs_client=False,
    ),
    CliFeature(
        id="ui",
        command="doch1 ui",
        argv=("ui",),
        summary="Launch the interactive Textual UI (humans only).",
        json_keys=(),
        json_command="ui",
        exits=(ExitContract.OK,),
        needs_client=False,
    ),
)


# --------------------------------------------------------------------------- #
# TUI FEATURES                                                                 #
# --------------------------------------------------------------------------- #

TUI_SCREENS: tuple[TuiScreen, ...] = (
    TuiScreen("screen-today", "Today", "1"),
    TuiScreen("screen-thisweek", "This week", "2"),
    TuiScreen("screen-nextweek", "Next week", "3"),
    TuiScreen("screen-history", "Calendar", "4"),
    TuiScreen("screen-status", "Status", "5"),
)

TUI_JOURNEYS: tuple[TuiJourney, ...] = (
    TuiJourney(
        id="launch-readonly",
        summary="Bare launch lands on Today and writes nothing (read-only).",
        keys=(),
    ),
    TuiJourney(
        id="nav-number-keys",
        summary="Number keys 1-5 switch between the five screens.",
        keys=("1", "2", "3", "4", "5"),
    ),
    TuiJourney(
        id="today-report",
        summary="Pressing r on Today reports at base (one service call).",
        keys=("r",),
    ),
    TuiJourney(
        id="week-fill-confirm",
        summary="f opens a confirm modal; y writes the week fill.",
        keys=("2", "f", "y"),
    ),
    TuiJourney(
        id="week-fill-cancel",
        summary="f then n cancels — nothing is written.",
        keys=("2", "f", "n"),
    ),
    TuiJourney(
        id="calendar-nav-conflicts",
        summary="Calendar month nav + conflicts-only toggle keep the grid.",
        keys=("4", "c", "c", "greater_than_sign"),
    ),
    TuiJourney(
        id="help-modal",
        summary="? opens the help modal; escape closes it.",
        keys=("question_mark", "escape"),
    ),
)


MANIFEST = Manifest(cli=CLI_FEATURES, screens=TUI_SCREENS, journeys=TUI_JOURNEYS)


# IDs every eval feature must cover, surfaced for the runner's coverage report.
ALL_FEATURE_IDS: tuple[str, ...] = (
    tuple(f.id for f in CLI_FEATURES)
    + tuple(s.screen_id for s in TUI_SCREENS)
    + tuple(j.id for j in TUI_JOURNEYS)
)

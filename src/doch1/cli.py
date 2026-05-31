"""DOCH1 command-line interface (Typer).

Agent-friendly: every command supports `--json` for machine consumption and
returns stable exit codes (0 = success, 1 = failure). Designed to be driven by
the Hermes agent or a human.

  doch1                 report TODAY as "at base" (cron default)
  doch1 today
  doch1 day 02.06.2026  schedule one future day
  doch1 week [date]     fill the Sun-Sat week containing date (default: today)
  doch1 history [m] [y] view past reports for a month (default: current month)
"""

from __future__ import annotations

import json as _json
import os
import sys
from datetime import date, datetime

import typer

from . import api, observability, render, statuses
from . import cron as cron_mod
from .dates import default_week_anchor

console = render.get_console()

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="IDF DOCH1 presence reporter + history viewer.",
)

# Hebrew status -> English gloss now has a single home in statuses.py (no drift).
# Re-exported here for back-compat with anything importing cli.TRANSLATE/_t.
TRANSLATE = statuses.TRANSLATE
_t = statuses.translate


def _resolve_status(cfg: dict[str, str], key: str | None):
    """Resolve the effective status: --status KEY > env codes > DEFAULT.

    Raises a Typer-friendly Doch1Error on an unknown key so callers can _fail().
    """
    try:
        return statuses.resolve_selection(key, cfg)
    except statuses.UnknownStatusError as e:
        raise api.Doch1Error(str(e)) from e


def _status_json(status) -> dict:
    return {"main": status.main, "secondary": status.secondary, "label": status.en}


# ---------- shared helpers ----------


def _cfg() -> dict[str, str]:
    return api.load_config()


def _client(cfg: dict[str, str]):
    """Pick transport: saved browser session (preferred) else pasted cookie.
    Both are context managers exposing post_multipart/post_json."""
    from .session import BrowserClient, state_path

    if state_path().exists():
        headless = os.environ.get("DOCH1_HEADFUL") not in ("1", "true")
        return BrowserClient(headless=headless)
    cookie = cfg.get("DOCH1_COOKIE")
    if cookie:
        return api.RequestsClient(cookie)
    raise api.Doch1Error(
        "No session. Run `doch1 login` (or set DOCH1_COOKIE in .env).", auth_expired=True
    )


def _transport_label() -> str | None:
    """SAFE transport name for observability: 'browser' or 'cookie' (never the
    cookie/token VALUE). None if neither is configured."""
    from .session import state_path

    if state_path().exists():
        return "browser"
    if _cfg().get("DOCH1_COOKIE"):
        return "cookie"
    return None


def _log_outcome(
    command: str,
    timer: observability.RunTimer,
    *,
    ok: bool,
    auth_expired: bool = False,
    category: str | None = None,
) -> None:
    """Emit ONE secret-safe observability line for a command outcome (no-op
    unless DOCH1_LOG is enabled). Passes only a category, never raw error text."""
    observability.log_run(
        command,
        ok=ok,
        reason=observability.classify_reason(ok=ok, auth_expired=auth_expired, category=category),
        duration_ms=timer.ms(),
        transport=_transport_label(),
        auth_expired=auth_expired,
    )


def _sanitize_alert(text: str) -> str:
    """Strip anything that could carry raw server bodies / tracebacks out of an
    outbound Telegram message.

    Telegram is a third-party chat: the payload must NOT contain raw server
    response text or full exception/traceback detail. We keep only a short, safe
    one-line summary (control chars removed, length-capped).
    """
    if not text:
        return "DOCH1 alert"
    # Collapse to a single line and drop control characters.
    cleaned = "".join(ch for ch in text if ch == " " or (ch.isprintable() and ch not in "\r\n\t"))
    cleaned = " ".join(cleaned.split())
    # Hard length cap so even a crafted short error can't smuggle a payload out.
    return cleaned[:120] if cleaned else "DOCH1 alert"


def _alert_text(command: str, *, auth_expired: bool = False) -> str:
    """Build a SAFE outbound alert summary: command + a generic reason, never raw
    server bodies or exception/traceback text."""
    reason = "session expired (re-login needed)" if auth_expired else "operation failed"
    return f"⚠️ DOCH1 {command} FAILED: {reason}"


# Pinned Telegram host — never interpolated/overridable, so the bot token can
# only ever be sent to the real api.telegram.org.
_TELEGRAM_API = "https://api.telegram.org"


def _alert(cfg: dict[str, str], text: str) -> None:
    import requests

    token, chat_id = cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"{_TELEGRAM_API}/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": _sanitize_alert(text)},
            timeout=15,
            # SECURITY: never follow a redirect — a MITM 30x could otherwise
            # replay the bot-token-bearing URL (and Referer) to an attacker host.
            allow_redirects=False,
        )
    except requests.RequestException:
        pass


def _fail(
    cfg: dict[str, str],
    command: str,
    msg: str,
    *,
    json_out: bool = False,
    alert: str | None = None,
    auth_expired: bool = False,
) -> typer.Exit:
    # In --json mode emit a structured error to stdout so the agent can parse it;
    # otherwise a human line to stderr. Either way exit 1 (stable contract).
    if json_out:
        typer.echo(
            _json.dumps(
                {"command": command, "ok": False, "error": msg, "auth_expired": auth_expired},
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"FAIL {msg}", err=True)
    if alert:
        _alert(cfg, alert)
    return typer.Exit(code=1)


def _parse_date(s: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date '{s}': use DD.MM.YYYY or YYYY-MM-DD")


def _week_days(anchor: date) -> list[date]:
    from datetime import timedelta

    sunday = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    return [sunday + timedelta(days=i) for i in range(7)]


# ---------- commands ----------


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _should_launch_ui() -> bool:
    """True iff a bare `doch1` should launch the interactive UI.

    Forced on by DOCH1_FORCE_UI; otherwise off under the kill-switch, off when
    not on a TTY (cron), and off under CI / DOCH1_CRON. Default on for a real
    interactive terminal.
    """
    if _env_on("DOCH1_FORCE_UI"):
        return True
    if _env_on("DOCH1_NONINTERACTIVE"):
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("CI") or os.environ.get("DOCH1_CRON"):
        return False
    return True


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context):
    """No subcommand: launch interactive UI on a TTY, else run `today` (cron)."""
    if ctx.invoked_subcommand is not None:
        return
    if _should_launch_ui():
        from .tui.app import run_app  # lazy: textual imported only here

        run_app()
    else:
        today(json_out=False)


@app.command()
def ui():
    """Launch the interactive terminal UI (tables, arrow/mouse navigation).

    Always launches regardless of TTY gating — needs a real terminal.
    """
    from .tui.app import run_app

    run_app()


@app.command()
def today(
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    status_key: str | None = typer.Option(
        None, "--status", help="Status to report (default: at-base / DOCH1_MAIN_CODE)."
    ),
):
    """Report TODAY (default: present at base 01/01)."""
    cfg = _cfg()
    _t0 = observability.RunTimer()
    try:
        sel = _resolve_status(cfg, status_key)
        with _client(cfg) as client:
            ok = api.report_today(client, sel)
    except api.Doch1Error as e:
        _log_outcome("today", _t0, ok=False, auth_expired=e.auth_expired)
        raise _fail(
            cfg,
            "today",
            str(e),
            json_out=json_out,
            alert=_alert_text("today", auth_expired=e.auth_expired),
            auth_expired=e.auth_expired,
        ) from e
    if not ok:
        _log_outcome("today", _t0, ok=False, category="rejected")
        raise _fail(
            cfg,
            "today",
            "report not accepted (server returned non-true)",
            json_out=json_out,
            alert="⚠️ DOCH1 today FAILED: report rejected",
        )
    _log_outcome("today", _t0, ok=True)
    if json_out:
        typer.echo(
            _json.dumps(
                {
                    "command": "today",
                    "date": date.today().isoformat(),
                    "ok": True,
                    "status": _status_json(sel),
                }
            )
        )
    else:
        render.render_today_result(console, True, status=sel)


@app.command()
def day(
    target: str = typer.Argument(..., help="Date DD.MM.YYYY or YYYY-MM-DD"),
    json_out: bool = typer.Option(False, "--json"),
    status_key: str | None = typer.Option(
        None, "--status", help="Status to report (default: at-base / DOCH1_MAIN_CODE)."
    ),
):
    """Schedule a single day (default: present at base)."""
    cfg = _cfg()
    try:
        d = _parse_date(target)
    except ValueError as e:
        raise _fail(cfg, "day", str(e), json_out=json_out) from e
    try:
        sel = _resolve_status(cfg, status_key)
        with _client(cfg) as client:
            ok = (
                api.report_today(client, sel)
                if d == date.today()
                else api.report_future(client, d, status=sel)
            )
    except api.Doch1Error as e:
        raise _fail(
            cfg,
            "day",
            str(e),
            json_out=json_out,
            alert=_alert_text(f"day {d.isoformat()}", auth_expired=e.auth_expired),
            auth_expired=e.auth_expired,
        ) from e
    if not ok:
        raise _fail(
            cfg,
            "day",
            f"{d.isoformat()} not accepted",
            json_out=json_out,
            alert=f"⚠️ DOCH1 {d} FAILED: rejected",
        )
    if json_out:
        typer.echo(
            _json.dumps(
                {"command": "day", "date": d.isoformat(), "ok": True, "status": _status_json(sel)}
            )
        )
    else:
        typer.echo(f"OK {d.strftime('%d.%m.%Y')}: {sel.en} ({sel.codes})")


@app.command()
def week(
    anchor: str | None = typer.Argument(None, help="Any date in the target week"),
    json_out: bool = typer.Option(False, "--json"),
    status_key: str | None = typer.Option(
        None, "--status", help="Status to report (default: at-base / DOCH1_MAIN_CODE)."
    ),
):
    """Fill the Sun-Sat week: today + remaining future days; skip filled/past."""
    cfg = _cfg()
    _t0 = observability.RunTimer()
    try:
        a = _parse_date(anchor) if anchor else default_week_anchor()
    except ValueError as e:
        _log_outcome("week", _t0, ok=False, category="bad_input")
        raise _fail(cfg, "week", str(e), json_out=json_out) from e
    try:
        sel = _resolve_status(cfg, status_key)
        # Shared loop with the TUI (tui.data is textual-free at import).
        from .tui.data import fill_week_plan

        days = _week_days(a)
        with _client(cfg) as client:
            results, failures = fill_week_plan(client, days, date.today(), status=sel)
    except api.Doch1Error as e:
        _log_outcome("week", _t0, ok=False, auth_expired=e.auth_expired)
        raise _fail(
            cfg,
            "week",
            str(e),
            json_out=json_out,
            alert=_alert_text("week", auth_expired=e.auth_expired),
            auth_expired=e.auth_expired,
        ) from e

    if json_out:
        typer.echo(
            _json.dumps({"command": "week", "results": results, "status": _status_json(sel)})
        )
    else:
        render.render_week(console, results, status=sel)
    if failures:
        _log_outcome("week", _t0, ok=False, category="rejected")
        _alert(cfg, "⚠️ DOCH1 week-fill failures: " + ", ".join(failures))
        raise typer.Exit(code=1)
    _log_outcome("week", _t0, ok=True)


@app.command()
def history(
    month: int | None = typer.Argument(None, help="Month 1-12 (default: current)"),
    year: int | None = typer.Argument(None, help="Year (default: current)"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    conflicts_only: bool = typer.Option(
        False, "--conflicts", help="Only days where approved != reported"
    ),
):
    """View PAST reports for a month (reported vs approved, flags, notes)."""
    cfg = _cfg()
    _t0 = observability.RunTimer()
    now = date.today()
    m, y = month or now.month, year or now.year
    try:
        with _client(cfg) as client:
            days = api.member_history(client, m, y)
    except api.Doch1Error as e:
        _log_outcome("history", _t0, ok=False, auth_expired=e.auth_expired)
        raise _fail(
            cfg,
            "history",
            str(e),
            json_out=json_out,
            alert=_alert_text("history", auth_expired=e.auth_expired),
            auth_expired=e.auth_expired,
        ) from e
    if conflicts_only:
        days = [d for d in days if d.conflict]
    _log_outcome("history", _t0, ok=True)

    if json_out:
        typer.echo(
            _json.dumps(
                {
                    "command": "history",
                    "month": m,
                    "year": y,
                    "days": [
                        {
                            "date": d.date.isoformat(),
                            "reported": d.reported,
                            "determined": d.determined,
                            "approved": d.approved,
                            "effective": d.effective,
                            "effective_en": _t(d.effective),
                            "in_base": d.in_base,
                            "conflict": d.conflict,
                            "note": d.note,
                        }
                        for d in days
                    ],
                },
                ensure_ascii=False,
            )
        )
        return

    render.render_history(console, days, m, y, _t, conflicts_only=conflicts_only)


@app.command()
def login(
    timeout: int = typer.Option(300, help="Seconds to wait for you to finish login"),
    manual: bool = typer.Option(
        False, "--manual/--no-manual", help="Force assisted headed login (no password used)."
    ),
    probe_sms: bool = typer.Option(
        False,
        "--probe-sms",
        hidden=True,
        help="MANUAL/LIVE: pop a headed browser and prove the SMS box is reached. Needs a display.",
    ),
):
    """Log in and save the session. DOCH1_PASS is OPTIONAL.

    With DOCH1_USER + DOCH1_PASS set, runs an automated headless Entra
    email -> password flow up to the SMS one-time-code step, then PROMPTS YOU IN
    THIS TERMINAL for the 6-digit code (or auto-fills it if DOCH1_TOTP_SEED is set).
    Runs headless by default; set DOCH1_HEADFUL=1 to watch the browser.

    Without DOCH1_PASS (or with --manual), a VISIBLE browser opens and YOU finish
    the login by hand (MFA + Stay signed in). The email is pre-filled if DOCH1_USER
    is set, and when DOCH1_PASS is ALSO set the password is pre-filled too (dual
    autofill) — the fill order is guarded so a missed email box can never cause the
    password to be typed into the wrong field. No password is stored. Use this when
    headless auto-login is blocked by Conditional Access.

    Reuses the saved session for all later commands; Entra sessions last ~weeks.
    Re-run this when a command reports 'Auth expired'.
    """
    cfg = _cfg()
    from .session import login as do_login

    # MANUAL/LIVE harness: pop a headed browser and prove we reach the SMS box.
    # Requires a real display (or xvfb); it is NOT a real login and saves nothing.
    if probe_sms:
        from .session import probe_sms as do_probe

        pass_cfg = (cfg.get("DOCH1_PASS") or "").strip()
        try:
            do_probe(username=cfg.get("DOCH1_USER"), password=(pass_cfg or None), timeout_s=timeout)
        except Exception as e:
            raise _fail(cfg, "login", f"probe-sms failed: {e}") from e
        typer.echo("OK probe-sms reached the SMS step.")
        raise typer.Exit(0)
    # Treat blank/whitespace-only DOCH1_PASS as no password. --manual forces
    # assisted (headed) mode even when a password is configured — but the password
    # is STILL passed through so the assisted browser pre-fills BOTH the email and
    # the password (dual autofill). No password is stored and the human finishes
    # MFA; assisted mode does no automated password-submit. `assisted=None` lets
    # session.login derive the mode from password presence in the non-manual path.
    pass_cfg = (cfg.get("DOCH1_PASS") or "").strip()
    password = pass_cfg or None
    assisted = True if manual else None
    try:
        dest = do_login(
            timeout_s=timeout,
            totp_seed=cfg.get("DOCH1_TOTP_SEED"),
            username=cfg.get("DOCH1_USER"),
            password=password,
            assisted=assisted,
        )
    except Exception as e:
        raise _fail(cfg, "login", f"login failed: {e}") from e
    typer.echo(f"OK session saved to {dest}")


# ---------- cron sub-app (auto-fill crontab management) ----------

cron_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Install / list / remove the auto-fill cron jobs.",
)
app.add_typer(cron_app, name="cron")


def _proj_dir() -> str:
    """Project root (parent of the doch1 package)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py() -> str:
    """Interpreter to use in cron lines: the project venv if present, else sys."""
    venv = os.path.join(_proj_dir(), ".venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def _precreate_cron_log(log_path: str) -> None:
    """Create doch1.log at 0o600 before cron starts appending to it.

    SECURITY:
      - The cron job appends with the shell's default umask, which typically
        leaves doch1.log world-readable — leaking presence cadence / timing /
        error diagnostics to any local user. Pre-creating it 0o600 closes that.
      - An attacker could pre-plant a SYMLINK at doch1.log (e.g. -> ~/.ssh/
        authorized_keys) so cron's ``>>`` writes through it. Refuse to proceed
        if the path is a symlink.
    """
    if os.path.islink(log_path):
        raise api.Doch1Error(
            f"Refusing to use {log_path}: it is a symlink (possible attack). Remove it and re-run."
        )
    # O_NOFOLLOW: if it becomes a symlink between the check and the open, fail.
    flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(log_path, flags, 0o600)
    except OSError as e:
        raise api.Doch1Error(f"Could not create log file {log_path}: {e}") from e
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _crontab_read() -> str:
    """Return the current user crontab text ('' if none / no crontab)."""
    import subprocess

    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise api.Doch1Error(f"crontab not available: {e}") from e
    if out.returncode != 0:
        # `crontab -l` exits non-zero when no crontab exists — treat as empty.
        return ""
    return out.stdout


def _crontab_write(text: str) -> None:
    """Replace the user crontab with `text` via `crontab -`."""
    import subprocess

    try:
        proc = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
    except FileNotFoundError as e:
        raise api.Doch1Error(f"crontab not available: {e}") from e
    if proc.returncode != 0:
        raise api.Doch1Error(f"crontab write failed: {proc.stderr.strip()}")


@cron_app.command("install")
def cron_install(
    daily: str = typer.Option(cron_mod.DEFAULT_DAILY, "--daily", help="Daily schedule (cron expr)"),
    weekly: str = typer.Option(
        cron_mod.DEFAULT_WEEKLY, "--weekly", help="Weekly schedule (cron expr)"
    ),
    no_weekly: bool = typer.Option(False, "--no-weekly", help="Install only the daily job"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Install (or update) the auto-fill cron jobs. Idempotent."""
    cfg = _cfg()
    try:
        lines = cron_mod.build_lines(
            _proj_dir(), _py(), daily=daily, weekly=weekly, with_weekly=not no_weekly
        )
    except ValueError as e:
        # Reject an injected/malformed --daily/--weekly schedule cleanly instead
        # of letting it reach the crontab (line-injection guard).
        raise _fail(cfg, "cron install", str(e), json_out=json_out) from e
    try:
        _precreate_cron_log(os.path.join(_proj_dir(), "doch1.log"))
    except api.Doch1Error as e:
        raise _fail(cfg, "cron install", str(e), json_out=json_out) from e
    try:
        merged = cron_mod.merge(_crontab_read(), lines)
        _crontab_write(merged)
    except api.Doch1Error as e:
        raise _fail(cfg, "cron install", str(e), json_out=json_out) from e
    if json_out:
        typer.echo(
            _json.dumps(
                {
                    "command": "cron install",
                    "ok": True,
                    "installed": [{"tag": t, "line": ln} for t, ln in lines],
                }
            )
        )
    else:
        typer.echo("OK installed cron jobs:")
        for tag, ln in lines:
            typer.echo(f"  {tag}")
            typer.echo(f"  {ln}")


@cron_app.command("list")
@cron_app.command("status")
def cron_status(json_out: bool = typer.Option(False, "--json")):
    """Show which auto-fill cron jobs are installed (read-only)."""
    cfg = _cfg()
    try:
        st = cron_mod.status(_crontab_read())
    except api.Doch1Error as e:
        raise _fail(cfg, "cron status", str(e), json_out=json_out) from e
    if json_out:
        typer.echo(_json.dumps({"command": "cron status", "jobs": st}))
    else:
        for tag, info in st.items():
            mark = "installed" if info["present"] else "absent"
            typer.echo(f"{tag}: {mark}")
            if info["present"] and info["line"]:
                typer.echo(f"  {info['line']}")


@cron_app.command("remove")
def cron_remove(json_out: bool = typer.Option(False, "--json")):
    """Remove both auto-fill cron jobs (daily + weekly)."""
    cfg = _cfg()
    tags = [cron_mod.TAG_DAILY, cron_mod.TAG_WEEKLY]
    try:
        stripped = cron_mod.remove(_crontab_read(), tags)
        _crontab_write(stripped)
    except api.Doch1Error as e:
        raise _fail(cfg, "cron remove", str(e), json_out=json_out) from e
    if json_out:
        typer.echo(_json.dumps({"command": "cron remove", "ok": True, "removed": tags}))
    else:
        typer.echo("OK removed auto-fill cron jobs.")


@app.command("statuses")
def statuses_cmd(
    json_out: bool = typer.Option(False, "--json"),
    refresh: bool = typer.Option(
        False, "--refresh", help="LIVE: discover real codes from the site (not yet wired)."
    ),
):
    """List the selectable report statuses (code / Hebrew / English).

    Only the at-base default is known offline. The holiday/leave/off-base codes
    must be captured from a HEADED, authenticated session against one.prat.idf.il
    by observing the /primaries + /secondaries network requests in the UI status
    picker (their exact paths/fields are not in the codebase). `--refresh` is the
    placeholder for that maintainer ritual; it is NOT yet wired.
    """
    if refresh:
        msg = (
            "statuses --refresh is not yet wired: the real /primaries + "
            "/secondaries endpoint paths and field names are UNKNOWN and must be "
            "captured from a HEADED, authenticated session (run `doch1 login`, then "
            "open the site's status picker and observe the network requests). "
            "Until then only the at-base default (01/01) is selectable."
        )
        if json_out:
            typer.echo(_json.dumps({"command": "statuses", "ok": False, "error": msg}))
        else:
            typer.echo(f"TODO {msg}", err=True)
        raise typer.Exit(code=1)
    rows = [statuses.REGISTRY[k] for k in sorted(statuses.REGISTRY)]
    if json_out:
        typer.echo(
            _json.dumps(
                {
                    "command": "statuses",
                    "statuses": [
                        {"main": s.main, "secondary": s.secondary, "he": s.he, "en": s.en}
                        for s in rows
                    ],
                },
                ensure_ascii=False,
            )
        )
        return
    typer.echo("Selectable statuses (code  Hebrew  English):")
    for s in rows:
        typer.echo(f"  {s.codes:>7}  {s.he}  ->  {s.en}")
    typer.echo(
        "Only at-base (01/01) is known offline; run `doch1 statuses --refresh` "
        "to discover the rest (needs a live session)."
    )


@app.command()
def status(json_out: bool = typer.Option(False, "--json")):
    """Check whether the saved session is still valid."""
    cfg = _cfg()
    _t0 = observability.RunTimer()
    from .session import state_path

    try:
        with _client(cfg) as client:
            api.list_scheduled(client, date.today().month, date.today().year)
        ok = True
    except api.Doch1Error as e:
        _log_outcome("status", _t0, ok=False, auth_expired=e.auth_expired)
        if json_out:
            typer.echo(_json.dumps({"command": "status", "authenticated": False, "error": str(e)}))
        else:
            render.render_status(console, False, error=str(e))
        raise typer.Exit(code=1) from e
    _log_outcome("status", _t0, ok=True)
    src = "browser-session" if state_path().exists() else "cookie"
    if json_out:
        typer.echo(_json.dumps({"command": "status", "authenticated": ok, "transport": src}))
    else:
        render.render_status(console, True, transport=src)


if __name__ == "__main__":
    app()

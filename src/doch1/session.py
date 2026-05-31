"""Playwright-backed transport for DOCH1.

Why a browser: one.prat.idf.il sits behind the Imperva (Incapsula) WAF, which
blocks plain `requests` and expires the `incap_ses` cookie within minutes. A
real Chromium context passes the WAF's JS challenge and keeps the session fresh.
All API calls go through context.request, which shares the browser's cookies.

Auth comes from a saved Playwright storageState (`auth.json`) captured at login.
`login()` opens the Microsoft Entra flow for a one-time human MFA, then persists
the session; subsequent runs reuse it (Entra refresh tokens last ~90 days).

A TOTP seed (DOCH1_TOTP_SEED) is supported for future fully-unattended re-auth,
but the IDF tenant currently enrols only SMS/Authenticator-push, so login is
interactive once per session lifetime.
"""

from __future__ import annotations

import os
import queue
import select
import sys
import threading
import time
from pathlib import Path

from .api import BASE, Doch1Error


def _is_app_url(u: str) -> bool:
    """True iff `u` is genuinely on the authenticated app origin.

    SECURITY: the old check used ``"one.prat.idf.il" in u`` (substring), which a
    crafted URL like ``https://evil.com/one.prat.idf.il`` would satisfy. We bind
    to the real origin with a prefix/exact match instead, and still require that
    we are no longer on the Microsoft login origin or the app's /login page.
    """
    if not isinstance(u, str):
        return False
    on_origin = u == BASE or u.startswith(BASE + "/")
    return (
        on_origin and "login.microsoftonline.com" not in u and not u.rstrip("/").endswith("/login")
    )


def _fill_field(page, selector, value, submit_selector=None) -> str:
    """Three-way fill classifier — the building block for the autofill guard.

    Returns one of:
      - "skipped": the value was blank/falsy (an INTENDED skip); the page is
        never touched.
      - "absent": a real value was supplied but the selector never appeared (or
        the box was None / the wait raised) — a GENUINE miss, NOT a skip. This is
        the case the credential sequencer must guard against so a silent email
        miss cannot cascade into typing the password into the wrong field.
      - "filled": the box was found and `.fill(value)` ran (plus submit/Enter).

    Distinguishing "skipped" from "absent" is the whole point: a blank email is
    the user's choice, but an absent email box means we are NOT on the screen we
    think we are, and the next credential must NOT be typed blind.
    """
    if not (value and str(value).strip()):
        return "skipped"
    try:
        box = page.wait_for_selector(selector, timeout=20000)
    except Exception:
        return "absent"
    if box is None:
        return "absent"
    box.fill(value)
    if submit_selector:
        try:
            page.click(submit_selector, timeout=5000)
        except Exception:
            box.press("Enter")
    else:
        box.press("Enter")
    return "filled"


def _fill_if_present(page, selector, value, submit_selector=None) -> bool:
    """Fill `selector` with `value` if both the value and the box exist.

    Thin bool wrapper over `_fill_field`: True iff a box was found and filled. A
    blank/falsy value or a missing selector both yield False so an optional
    screen never crashes the flow. Prefer `_fill_field` where the caller needs to
    distinguish an intended skip from a genuine miss (the autofill guard does).
    """
    return _fill_field(page, selector, value, submit_selector) == "filled"


def _fill_credentials(fill_email, fill_password, *, wait=None) -> dict:
    """Sequence the email then password autofill with an order/focus GUARD.

    Email and password live on SEPARATE Entra screens (email -> Next -> password).
    `fill_email` / `fill_password` are injected zero-arg callables that each
    return one of "skipped" | "absent" | "filled" (see `_fill_field`). `wait`, if
    given, is called once after a successful email fill to let the Next click
    advance to the password screen.

    THE GUARD: if the email fill genuinely MISSED ("absent" — a real value was
    supplied but the box never appeared), the page is still on (or stuck before)
    the email screen, so the password is NOT filled — typing it blind could land
    it in the wrong/visible field. An intended email *skip* (blank username) is
    NOT a miss and does not block the password (which fills via its own
    password-only selector, so it can only ever land in a password field).

    Returns a dict: {"email", "password", "password_guarded"} where
    `password_guarded` is True iff the password fill was suppressed by the guard.
    """
    email_result = fill_email()
    if email_result == "filled" and wait is not None:
        wait()
    if email_result == "absent":
        # Silent email miss: do NOT cascade into a blind password fill.
        return {"email": email_result, "password": None, "password_guarded": True}
    password_result = fill_password()
    return {
        "email": email_result,
        "password": password_result,
        "password_guarded": False,
    }


def _poll_stdin_line(stop_event, *, poll=0.5):
    """Poll-with-timeout read of a single line from stdin.

    Wakes every `poll` seconds (via select) to re-check `stop_event` so the
    caller can cancel cleanly, instead of parking forever inside a bare
    `input()`/`readline()`. Returns:
      - the stripped line once one is available,
      - "" (empty string) on a poll-timeout with nothing ready (caller loops),
      - raises EOFError when stdin closes (e.g. piped input exhausted).

    On platforms/streams where select on stdin is unsupported, this degrades to
    a single blocking readline (the cancellation guarantee then relies on the
    reader being a daemon thread).
    """
    fd = sys.stdin
    while not stop_event.is_set():
        try:
            ready, _, _ = select.select([fd], [], [], poll)
        except (OSError, ValueError):
            # select unsupported on this stream; fall back to one readline.
            line = fd.readline()
            if line == "":
                raise EOFError() from None
            return line.strip()
        if stop_event.is_set():
            return ""
        if ready:
            line = fd.readline()
            if line == "":
                raise EOFError()
            return line.strip()
    return ""


class _TerminalCodeReader:
    """Injectable, page-free terminal SMS-code reader.

    A background daemon thread calls the injected `read_line(stop_event)` and
    drops the resulting code onto an internal queue. `get_code()` polls that
    queue WITHOUT blocking. The thread NEVER touches the Playwright page — its
    sole job is to produce a string — so it can never contend with the main
    thread's sync page calls. `cancel()` sets the stop event so a cooperative
    `read_line` wakes and the thread exits; the flow can therefore always join
    quickly and never leave a live blocking read behind.

    Non-TTY stdin (cron, piped, no terminal): the reader degrades immediately to
    "no terminal code source" — the thread is never started and `get_code()`
    always returns None, so it can never block process exit.

    EOF / KeyboardInterrupt raised by `read_line` are swallowed (treated as
    "user gave up on the terminal; finish in the browser"); no exception escapes.
    """

    def __init__(self, *, read_line=None, is_tty=None, poll=0.5):
        if is_tty is None:
            try:
                is_tty = sys.stdin.isatty()
            except Exception:
                is_tty = False
        if read_line is None:

            def read_line(stop_event):
                return _poll_stdin_line(stop_event, poll=poll)

        self._read_line = read_line
        self._is_tty = bool(is_tty)
        self._stop = threading.Event()
        self._q: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # No terminal -> no code source. Never start a thread that could block.
        if not self._is_tty:
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                line = self._read_line(self._stop)
            except (EOFError, KeyboardInterrupt):
                return  # user aborted the terminal path; let the browser finish
            except Exception:
                return
            if self._stop.is_set():
                return
            if line:
                self._q.put(line)
                return
            # Empty string => poll-timeout with nothing ready; loop and re-check.

    def get_code(self) -> str | None:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def cancel(self) -> None:
        self._stop.set()

    def join(self, timeout=None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def _resolve_otp(
    get_code,
    is_authed,
    submit_code,
    *,
    timeout=300.0,
    poll=0.5,
    now=time.monotonic,
    sleep=time.sleep,
    on_done=None,
) -> str:
    """Race a background code source against the browser finishing auth itself.

    Each iteration checks `is_authed()` FIRST (the human typed the code in the
    window -> "browser"); otherwise pulls `get_code()` and, if a code is
    available, submits it -> "terminal". With neither, sleep(poll) and retry
    until `timeout` elapses, at which point raise a Doch1Error.

    `on_done`, if given, is invoked exactly once on EVERY exit (browser win,
    terminal win, or timeout) so the caller can cancel the terminal reader and
    guarantee no blocking read is left alive.
    """
    start = now()
    try:
        while True:
            if is_authed():
                return "browser"
            code = get_code()
            if code:
                submit_code(code)
                return "terminal"
            if now() - start > timeout:
                raise Doch1Error(
                    "Timed out waiting for the SMS code (terminal or browser).",
                    auth_expired=True,
                )
            sleep(poll)
    finally:
        if on_done is not None:
            on_done()


def _needs_headed_for_sms(headless: bool, otc_present: bool) -> bool:
    """Pure predicate: does this tenant need a HEADED window for the SMS step?

    The IDF Entra tenant is SMS-only MFA and a headless Chromium can reach the
    one-time-code (OTC) box but the human can never see it to type the texted
    code, so the headless flow dies silently at the OTC timeout. The only signal
    we have mid-flow is "we are headless AND we reached an OTC screen" — that
    combination means the run cannot succeed headless.

    Returns True only when (headless AND otc_present); every other combination
    is False (no decision / fine as-is).
    """
    return bool(headless) and bool(otc_present)


def _raise_if_headless_sms(headless: bool, otc_present: bool) -> None:
    """Fail-fast guard: raise EARLY when headless meets the SMS/OTC step.

    Wired into auto-mode right after the OTC element is resolved, BEFORE the long
    SMS-code wait, so a headless+SMS run errors instantly with an actionable
    message instead of burning the full OTC timeout. No-op (returns None) in
    every other case, including the normal headed SMS path.
    """
    if _needs_headed_for_sms(headless, otc_present):
        raise Doch1Error(
            "SMS MFA needs a headed window — re-run with `--manual` "
            "(or set DOCH1_HEADFUL=1). This tenant is SMS-only and the OTP "
            "box can't be completed headless.",
            auth_expired=False,
        )
    return None


def _verify_authenticated(ctx) -> bool:
    """True iff the context's session can call an authenticated endpoint."""
    from datetime import datetime

    from .api import P_FUTURE_LIST

    now = datetime.now()
    try:
        resp = ctx.request.post(
            BASE + P_FUTURE_LIST,
            data={"month": now.month, "year": now.year},
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=30000,
        )
    except Exception:
        return False
    ct = resp.headers.get("content-type", "")
    if resp.status != 200 or "application/json" not in ct:
        return False
    try:
        return "days" in resp.json()
    except Exception:
        return False


def state_path() -> Path:
    from .api import safe_override_path

    override = os.environ.get("DOCH1_STATE")
    if override:
        return safe_override_path(override, var="DOCH1_STATE")
    return Path.home() / ".config" / "doch1" / "auth.json"


def _persist_state(ctx, dest: Path) -> None:
    """Persist Playwright storageState to `dest` with 0o600 perms, closing the
    world-readable window.

    storageState() holds the live session token / refresh cookies. We write to a
    temp file in the same directory, chmod it to 0o600 BEFORE it carries the
    secret's final name, then atomically rename into place — so the secret never
    exists at the destination path with default (often world-readable) perms.
    """
    dest = Path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(dest.parent, 0o700)
        except OSError:
            pass
        tmp = dest.with_name(dest.name + f".tmp.{os.getpid()}")
        ctx.storage_state(path=str(tmp))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, dest)
        # Belt-and-suspenders: ensure the final path is 0o600 even if some
        # platform reset perms on rename.
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
    except Exception:
        # Best-effort fallback: a direct write is better than losing the session.
        ctx.storage_state(path=str(dest))
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass


class BrowserClient:
    """Context manager. Loads saved session, primes the WAF, exposes
    post_multipart/post_json, and refreshes the stored cookies on exit."""

    def __init__(self, state: Path | None = None, headless: bool = True):
        self.state = Path(state) if state else state_path()
        self.headless = headless
        self._pw = self._browser = self._ctx = None

    # -- lifecycle --
    def __enter__(self):
        from playwright.sync_api import sync_playwright

        if not self.state.exists():
            raise Doch1Error(
                f"No saved session at {self.state}. Run `doch1 login`.", auth_expired=True
            )
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._ctx = self._browser.new_context(storage_state=str(self.state))
        self._prime()
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx is not None:
                # Persist refreshed cookies (incap_ses rotates each visit), with
                # 0o600 perms so the live session token is never world-readable.
                _persist_state(self._ctx, self.state)
        except Exception:
            pass
        finally:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()

    def _prime(self) -> None:
        """Visit the app so the WAF JS challenge runs; verify still authed."""
        page = self._ctx.new_page()
        try:
            page.goto(f"{BASE}/hp", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            if "login.microsoftonline.com" in page.url or page.url.rstrip("/").endswith("/login"):
                raise Doch1Error("Session expired — run `doch1 login`.", auth_expired=True)
        finally:
            page.close()

    # -- transport --
    def _guard(self, resp) -> None:
        ct = resp.headers.get("content-type", "")
        if resp.status in (401, 403):
            raise Doch1Error(f"Auth expired (HTTP {resp.status})", auth_expired=True)
        if "text/html" in ct:
            raise Doch1Error("Got HTML not JSON — session/WAF wall", auth_expired=True)
        if not resp.ok:
            # Do NOT embed the raw server body — it may be forwarded to Telegram.
            raise Doch1Error(f"HTTP {resp.status} error")

    def post_multipart(self, path: str, fields: dict[str, str]) -> str:
        resp = self._ctx.request.post(BASE + path, multipart=dict(fields), timeout=30000)
        self._guard(resp)
        return resp.text().strip().strip('"')

    def post_json(self, path: str, body: dict) -> dict:
        resp = self._ctx.request.post(
            BASE + path,
            data=body,
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=30000,
        )
        self._guard(resp)
        try:
            return resp.json()
        except Exception as exc:
            raise Doch1Error("Bad JSON response") from exc


def login(
    state: Path | None = None,
    timeout_s: int = 300,
    totp_seed: str | None = None,
    username: str | None = None,
    password: str | None = None,
    headless: bool | None = None,
    otp_callback=None,
    assisted: bool | None = None,
) -> Path:
    """Entra login. Two modes, chosen by whether a non-empty `password` is passed.

    Assisted mode: opens a VISIBLE browser (forced headed regardless of
    DOCH1_HEADFUL), pre-fills the email if `username` is given AND, when a
    `password` is also supplied, pre-fills the password too (dual autofill) —
    GUARDING the order so a silent email-fill miss never types the password into
    the wrong field. The human still finishes MFA + Stay-signed-in by hand and no
    password is ever stored. It then waits for the URL to land back on
    one.prat.idf.il, verifies real authentication, and persists.

    Mode selection: `assisted=None` (default) chooses assisted iff no non-empty
    `password` is given. Pass `assisted=True` to FORCE assisted mode even with a
    password present — the password is then used ONLY for the headed autofill (no
    automated submit, no storage); pass `assisted=False` to force auto mode.

    Auto mode (password present), unchanged: reads username/password, fills
    the Microsoft Entra email -> Next -> password -> Sign in flow, advances to the
    SMS one-time-code step (clicking the Text/SMS option if a method picker
    appears), then obtains the 6-digit code either from `otp_callback()` or by
    prompting on the terminal. Submits it, accepts "Stay signed in?", verifies real
    authentication, and persists the session. Returns the saved state path.

    Runs headless by default; set DOCH1_HEADFUL=1 (or pass headless=False) to show a
    real browser window. A `totp_seed` is honoured as a fallback for the OTC step on
    authenticator-TOTP tenants. Only the OTC step is mandatory — optional screens
    that don't appear are skipped rather than treated as errors.
    """
    from playwright.sync_api import sync_playwright

    # Assisted mode: a human finishes login in a real window. Force headed
    # regardless of DOCH1_HEADFUL (the human needs the UI) and skip ALL automated
    # password-submit/OTC/KMSI automation — but STILL autofill both creds if
    # given. Default: assisted iff no password; callers may force it (so --manual
    # can autofill the password without switching to auto-submit mode).
    if assisted is None:
        assisted = not (password and password.strip())
    if assisted:
        headless = False
    elif headless is None:
        headless = os.environ.get("DOCH1_HEADFUL") not in ("1", "true")

    dest = Path(state) if state else state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Selector groups (primary + fallbacks), comma-joined for Playwright.
    SEL_EMAIL = "input[type=email], input[name=loginfmt], input#i0116"
    SEL_EMAIL_NEXT = "button[name=Next], input#idSIButton9, input[type=submit]"
    SEL_PASSWORD = "input[type=password], input[name=passwd], input#i0118"
    SEL_PASSWORD_SUBMIT = "button[name='Sign in'], input#idSIButton9, input[type=submit]"
    SEL_OTC = "input#idTxtBx_SAOTCC_OTC, input[name=otc]"
    SEL_OTC_SUBMIT = "input#idSubmit_SAOTCC_Continue, input#idSIButton9, button[name=Verify]"
    SEL_KMSI_YES = "input[type=submit][value='Yes'], input#idSIButton9"

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(f"{BASE}/hp", wait_until="domcontentloaded", timeout=60000)

        if assisted:
            # -- Assisted (headed): autofill BOTH creds when present, GUARDING the
            # order so a silent email-fill miss can never cause the password to be
            # typed into the wrong field, then race a terminal SMS-code reader
            # against the human typing it in the window. --
            cred = _fill_credentials(
                lambda: _fill_field(page, SEL_EMAIL, username, SEL_EMAIL_NEXT),
                lambda: _fill_field(page, SEL_PASSWORD, password, SEL_PASSWORD_SUBMIT),
                wait=lambda: page.wait_for_timeout(1500),
            )
            if cred["password"] == "filled":
                page.wait_for_timeout(1500)

            def _is_authed() -> bool:
                return _is_app_url(page.url)

            # Reach the SMS one-time-code box (reuse the OTC-wait / method-picker
            # logic), unless the browser is already back on the app.
            otc = None
            if not _is_authed():
                deadline_picker_handled = False
                for _ in range(25):
                    if _is_authed():
                        break
                    try:
                        otc = page.query_selector(SEL_OTC)
                    except Exception:
                        otc = None
                    if otc:
                        break
                    if not deadline_picker_handled:
                        try:
                            body = page.inner_text("body", timeout=1000).lower()
                        except Exception:
                            body = ""
                        if (
                            "verify your identity" in body
                            or "how do you want to sign in" in body
                            or "another way" in body
                        ):
                            clicked = False
                            try:
                                page.get_by_role(
                                    "button",
                                    name=__import__("re").compile(r"text|sms", __import__("re").I),
                                ).first.click(timeout=2000)
                                clicked = True
                            except Exception:
                                pass
                            if not clicked:
                                try:
                                    page.get_by_role(
                                        "link",
                                        name=__import__("re").compile(
                                            r"text|sms", __import__("re").I
                                        ),
                                    ).first.click(timeout=2000)
                                    clicked = True
                                except Exception:
                                    pass
                            if not clicked:
                                try:
                                    page.click("a#signInAnotherWay", timeout=2000)
                                    clicked = True
                                except Exception:
                                    pass
                            if clicked:
                                deadline_picker_handled = True
                                page.wait_for_timeout(1500)
                    page.wait_for_timeout(1000)

            if not _is_authed():
                print(
                    "Enter the SMS code in this terminal, OR type it directly "
                    "in the browser window — whichever you do first."
                )

                # Page-free, cancellable terminal reader. The reader thread ONLY
                # produces a string onto a queue; it NEVER touches `page`. Only
                # THIS (main) thread ever calls page.* (via _submit_code and
                # _is_authed), so the reader can never contend with Playwright's
                # sync API. On non-TTY stdin (cron) the reader self-disables.
                reader = _TerminalCodeReader()
                reader.start()

                # _submit_code runs on the MAIN thread only (the _resolve_otp
                # loop). This is the single-page-owner invariant — do NOT move
                # any page.* call into the reader thread.
                def _submit_code(code):
                    box = page.wait_for_selector(SEL_OTC, timeout=20000)
                    box.fill(str(code).strip())
                    try:
                        page.click(SEL_OTC_SUBMIT, timeout=5000)
                    except Exception:
                        box.press("Enter")

                try:
                    _resolve_otp(
                        reader.get_code,
                        _is_authed,
                        _submit_code,
                        timeout=float(timeout_s),
                        on_done=reader.cancel,
                    )
                finally:
                    # Belt-and-braces: always signal stop and reap the thread so
                    # no live blocking read can wedge teardown or the next prompt.
                    reader.cancel()
                    reader.join(timeout=1.0)

            # "Stay signed in?" (best-effort).
            try:
                page.click(SEL_KMSI_YES, timeout=5000)
            except Exception:
                pass
        else:
            # -- email -> Next (optional; an existing Entra session may skip it) --
            if username:
                try:
                    print("Filling email...")
                    box = page.wait_for_selector(SEL_EMAIL, timeout=20000)
                    box.fill(username)
                    try:
                        page.click(SEL_EMAIL_NEXT, timeout=5000)
                    except Exception:
                        box.press("Enter")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass  # account/session may skip this step

            # -- password -> Sign in --
            try:
                print("Filling password...")
                box = page.wait_for_selector(SEL_PASSWORD, timeout=20000)
                box.fill(password)
                try:
                    page.click(SEL_PASSWORD_SUBMIT, timeout=5000)
                except Exception:
                    box.press("Enter")
                page.wait_for_timeout(1500)
            except Exception:
                pass

            # -- MFA: wait for EITHER the OTC box or a method/identity picker --
            print("Advancing to SMS verification...")
            otc = None
            deadline_picker_handled = False
            for _ in range(25):
                try:
                    otc = page.query_selector(SEL_OTC)
                except Exception:
                    otc = None
                if otc:
                    break
                if not deadline_picker_handled:
                    # "Verify your identity" / "How do you want to sign in?" picker.
                    try:
                        body = page.inner_text("body", timeout=1000).lower()
                    except Exception:
                        body = ""
                    if (
                        "verify your identity" in body
                        or "how do you want to sign in" in body
                        or "another way" in body
                    ):
                        clicked = False
                        # Prefer an explicit Text/SMS option.
                        try:
                            page.get_by_role(
                                "button",
                                name=__import__("re").compile(r"text|sms", __import__("re").I),
                            ).first.click(timeout=2000)
                            clicked = True
                        except Exception:
                            pass
                        if not clicked:
                            try:
                                page.get_by_role(
                                    "link",
                                    name=__import__("re").compile(r"text|sms", __import__("re").I),
                                ).first.click(timeout=2000)
                                clicked = True
                            except Exception:
                                pass
                        if not clicked:
                            # Fall back to opening the "use a different method" picker.
                            try:
                                page.click("a#signInAnotherWay", timeout=2000)
                                clicked = True
                            except Exception:
                                pass
                        if clicked:
                            deadline_picker_handled = True
                            page.wait_for_timeout(1500)
                page.wait_for_timeout(1000)

            if otc is None:
                try:
                    otc = page.wait_for_selector(SEL_OTC, timeout=10000)
                except Exception:
                    otc = None

            # -- FAIL-FAST: headless + SMS can never complete on this tenant.
            # If we reached an OTC screen while headless and no TOTP seed can
            # auto-answer it, error EARLY with an actionable message instead of
            # burning the full SMS-code timeout in a window the user can't see. --
            if otc is not None and not totp_seed:
                _raise_if_headless_sms(headless, otc_present=True)

            # -- TOTP-seed fallback: auto-answer the OTC without SMS, if configured --
            filled_via_totp = False
            if otc is not None and totp_seed and otp_callback is None:
                try:
                    # Use explicit UTC so a DST gap / naive-local mktime edge case
                    # can't produce the wrong code (which would silently fall back
                    # to SMS). pyotp derives the counter from the unix timestamp.
                    from datetime import datetime, timezone

                    import pyotp

                    code = pyotp.TOTP(totp_seed.replace(" ", "")).at(datetime.now(timezone.utc))
                    otc.fill(code)
                    page.click(SEL_OTC_SUBMIT, timeout=5000)
                    filled_via_totp = True
                except Exception:
                    filled_via_totp = False

            # -- SMS code: callback or terminal prompt (the one mandatory step) --
            if otc is not None and not filled_via_totp:
                print("Waiting for SMS code entry...")
                if otp_callback is not None:
                    code = str(otp_callback()).strip()
                else:
                    # SECURITY/robustness: a bare input() in a non-TTY context
                    # (cron / CI / piped) blocks forever — only SIGKILL recovers.
                    # Fail fast instead, with an actionable message, when there is
                    # no interactive terminal and no otp_callback to supply the code.
                    try:
                        _is_tty = sys.stdin.isatty()
                    except Exception:
                        _is_tty = False
                    if not _is_tty:
                        raise Doch1Error(
                            "SMS code required but stdin is not a terminal "
                            "(cron/CI/piped) and no otp_callback was given. "
                            "Run `doch1 login` interactively, or provide a TOTP "
                            "seed (DOCH1_TOTP_SEED).",
                            auth_expired=False,
                        )
                    code = input("Enter the SMS code sent to your phone: ").strip()
                otc.fill(code)
                try:
                    page.click(SEL_OTC_SUBMIT, timeout=5000)
                except Exception:
                    otc.press("Enter")
                page.wait_for_timeout(1500)
            elif otc is None:
                raise Doch1Error(
                    "Could not reach the SMS code entry screen — nothing was saved.",
                    auth_expired=True,
                )

            # -- "Stay signed in?" (best-effort) --
            try:
                page.click(SEL_KMSI_YES, timeout=5000)
            except Exception:
                pass

        # Wait until we're back on the authenticated app.
        page.wait_for_url(
            lambda u: _is_app_url(u),
            timeout=timeout_s * 1000,
        )
        page.wait_for_timeout(2000)
        # Verify the session is REALLY authenticated before persisting it —
        # the SPA shell can load at /hp before Entra auth resolves.
        authed = False
        for _ in range(5):
            if _verify_authenticated(ctx):
                authed = True
                break
            page.wait_for_timeout(2000)
        if not authed:
            raise Doch1Error(
                "Login did not complete — the session is NOT authenticated, "
                "so nothing was saved. Finish the Microsoft login and retry.",
                auth_expired=True,
            )
        _persist_state(ctx, dest)
        return dest
    finally:
        browser.close()
        pw.stop()


def probe_sms(
    username: str | None = None, password: str | None = None, timeout_s: int = 300
) -> bool:
    """MANUAL / LIVE smoke harness — pops a HEADED Chromium and proves the SMS
    path reaches the OTC box. NEVER run by pytest (it requires a real display).

    Launches a headed browser via the same machinery as login(), navigates to
    BASE/hp, drives email (username) + password (password) if present, and waits
    for the SMS one-time-code box (SEL_OTC). On success prints "REACHED SMS STEP"
    and blocks so a human can type the texted code in the window; returns True.
    If it cannot reach the OTC box it raises Doch1Error.

    Run it (needs a display):
        rtk; uv run doch1 login --probe-sms                 # real desktop
        rtk; xvfb-run -a uv run doch1 login --probe-sms     # headless server
    (xvfb proves the popup + reaching the box; the SMS itself still needs a human
    with the phone.)
    """
    from playwright.sync_api import sync_playwright

    SEL_EMAIL = "input[type=email], input[name=loginfmt], input#i0116"
    SEL_EMAIL_NEXT = "button[name=Next], input#idSIButton9, input[type=submit]"
    SEL_PASSWORD = "input[type=password], input[name=passwd], input#i0118"
    SEL_PASSWORD_SUBMIT = "button[name='Sign in'], input#idSIButton9, input[type=submit]"
    SEL_OTC = "input#idTxtBx_SAOTCC_OTC, input[name=otc]"

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False)  # ALWAYS headed — that's the point
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(f"{BASE}/hp", wait_until="domcontentloaded", timeout=60000)
        if _fill_if_present(page, SEL_EMAIL, username, SEL_EMAIL_NEXT):
            page.wait_for_timeout(1500)
        if _fill_if_present(page, SEL_PASSWORD, password, SEL_PASSWORD_SUBMIT):
            page.wait_for_timeout(1500)
        try:
            page.wait_for_selector(SEL_OTC, timeout=timeout_s * 1000)
        except Exception as exc:
            raise Doch1Error(
                "Did NOT reach the SMS one-time-code box. The headed SMS path is "
                "not proven on this run.",
                auth_expired=True,
            ) from exc
        print("REACHED SMS STEP")
        print(
            "Type the texted SMS code in the browser window to finish, "
            "then close it. (Ctrl-C here to abort.)"
        )
        page.wait_for_timeout(timeout_s * 1000)
        return True
    finally:
        browser.close()
        pw.stop()

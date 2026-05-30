"""TDD red-phase tests for two not-yet-existing login-flow helpers.

These import `_fill_if_present` and `_resolve_otp` from `doch1.session`, which do
not exist yet. The whole module therefore fails to import (collection error) until
the helpers are implemented — exactly the intended RED state.

No network and no real Playwright: everything is driven by fakes/stubs.
"""

from __future__ import annotations

import pytest

from doch1.api import Doch1Error
from doch1.session import (
    _fill_credentials,
    _fill_field,
    _fill_if_present,
    _needs_headed_for_sms,
    _raise_if_headless_sms,
    _resolve_otp,
    _TerminalCodeReader,
)

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeBox:
    """Stand-in for a Playwright element handle."""

    def __init__(self):
        self.filled = None
        self.pressed = None

    def fill(self, value):
        self.filled = value

    def press(self, key):
        self.pressed = key


class FakePage:
    """Minimal fake of the Playwright Page surface used by _fill_if_present."""

    def __init__(self, box=None, raise_on_wait=False):
        self._box = box
        self._raise_on_wait = raise_on_wait
        self.clicked = None
        self.wait_calls = []

    def wait_for_selector(self, selector, timeout=None):
        self.wait_calls.append((selector, timeout))
        if self._raise_on_wait:
            raise RuntimeError("selector never appeared")
        return self._box

    def click(self, selector, timeout=None):
        self.clicked = selector


# --------------------------------------------------------------------------- #
# _fill_if_present
# --------------------------------------------------------------------------- #


def test_fill_if_present_blank_value_returns_false_and_never_fills():
    box = FakeBox()
    page = FakePage(box=box)
    result = _fill_if_present(page, "input#email", "")
    assert result is False
    assert box.filled is None
    # A blank value must short-circuit before ever touching the page.
    assert page.wait_calls == []


def test_fill_if_present_real_value_fills_and_returns_true():
    box = FakeBox()
    page = FakePage(box=box)
    result = _fill_if_present(page, "input#email", "user@example.com")
    assert result is True
    assert box.filled == "user@example.com"


def test_fill_if_present_selector_not_found_returns_false_no_crash():
    page = FakePage(raise_on_wait=True)
    result = _fill_if_present(page, "input#missing", "anything")
    assert result is False


# --------------------------------------------------------------------------- #
# _fill_field — three-way classifier (skipped / absent / filled)
#
# Distinguishes "value blank => intended skip" from "selector genuinely absent
# => a real miss", so the credential sequencer can guard the order/focus footgun
# (a silent email miss must never cascade into typing the password blind).
# --------------------------------------------------------------------------- #


def test_fill_field_blank_value_is_skipped_never_touches_page():
    box = FakeBox()
    page = FakePage(box=box)
    assert _fill_field(page, "input#email", "") == "skipped"
    assert _fill_field(page, "input#email", None) == "skipped"
    assert box.filled is None
    assert page.wait_calls == []


def test_fill_field_present_value_is_filled():
    box = FakeBox()
    page = FakePage(box=box)
    assert _fill_field(page, "input#email", "user@example.com") == "filled"
    assert box.filled == "user@example.com"


def test_fill_field_selector_absent_is_absent_not_skipped():
    # A real value but the box never appears: a genuine miss, distinct from skip.
    page = FakePage(raise_on_wait=True)
    assert _fill_field(page, "input#missing", "secret") == "absent"


# --------------------------------------------------------------------------- #
# _fill_credentials — order/focus guard for assisted dual autofill (W3)
#
# Sequences email then password. THE FOOTGUN: email and password live on
# separate Entra screens (email -> Next -> password). If the email fill silently
# misses (selector absent) the page is still on the email screen, so the password
# must NOT be typed (it could land in the wrong/visible field). The sequencer
# encodes: only fill the password once the email step did not genuinely miss.
# Injected per-field fillers return "skipped" | "absent" | "filled".
# --------------------------------------------------------------------------- #


def _seq_fillers(email_result, password_result):
    calls = []

    def fill_email():
        calls.append("email")
        return email_result

    def fill_password():
        calls.append("password")
        return password_result

    return fill_email, fill_password, calls


def test_fill_credentials_both_present_fills_email_then_password():
    fe, fp, calls = _seq_fillers("filled", "filled")
    waits = []
    res = _fill_credentials(fe, fp, wait=lambda: waits.append(1))
    assert calls == ["email", "password"]  # email strictly before password
    assert res["email"] == "filled"
    assert res["password"] == "filled"
    assert res["password_guarded"] is False


def test_fill_credentials_email_absent_guards_password_no_misfill():
    # Email selector genuinely absent (a real miss) -> password MUST NOT be typed.
    fe, fp, calls = _seq_fillers("absent", "filled")
    res = _fill_credentials(fe, fp, wait=lambda: None)
    assert "password" not in calls  # the password filler was never invoked
    assert res["email"] == "absent"
    assert res["password_guarded"] is True
    assert res["password"] is None


def test_fill_credentials_blank_password_short_circuits_email_still_fills():
    # No password supplied: email fills, password filler reports "skipped".
    fe, fp, calls = _seq_fillers("filled", "skipped")
    res = _fill_credentials(fe, fp, wait=lambda: None)
    assert calls == ["email", "password"]
    assert res["email"] == "filled"
    assert res["password"] == "skipped"
    assert res["password_guarded"] is False


def test_fill_credentials_blank_email_real_password_no_cross_field_misfill():
    # No username supplied (email skipped, intended) but a password IS supplied.
    # An intended email *skip* is NOT a miss, so the password may still fill —
    # but a blank email is the user's choice (e.g. existing Entra session shows
    # the password screen). The guard only fires on a genuine "absent" miss.
    fe, fp, calls = _seq_fillers("skipped", "filled")
    res = _fill_credentials(fe, fp, wait=lambda: None)
    assert calls == ["email", "password"]
    assert res["email"] == "skipped"
    assert res["password"] == "filled"
    assert res["password_guarded"] is False


# --------------------------------------------------------------------------- #
# _resolve_otp
# --------------------------------------------------------------------------- #


def test_resolve_otp_browser_wins_first_check():
    submitted = []
    res = _resolve_otp(
        get_code=lambda: pytest.fail("get_code must not be called when browser wins"),
        is_authed=lambda: True,
        submit_code=lambda code: submitted.append(code),
        now=lambda: 0.0,
        sleep=lambda _s: None,
    )
    assert res == "browser"
    assert submitted == []


def test_resolve_otp_terminal_code_after_two_empty_polls():
    codes = iter([None, None, "654321"])
    submitted = []
    sleeps = []
    res = _resolve_otp(
        get_code=lambda: next(codes),
        is_authed=lambda: False,
        submit_code=lambda code: submitted.append(code),
        now=lambda: 0.0,
        sleep=lambda s: sleeps.append(s),
    )
    assert res == "terminal"
    assert submitted == ["654321"]
    # Two empty polls slept before the third iteration produced the code.
    assert len(sleeps) == 2


def test_resolve_otp_times_out_raises_doch1error():
    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 100.0
        return clock["t"]

    with pytest.raises(Doch1Error) as excinfo:
        _resolve_otp(
            get_code=lambda: None,
            is_authed=lambda: False,
            submit_code=lambda code: pytest.fail("submit_code must not be called on timeout"),
            timeout=10.0,
            now=fake_now,
            sleep=lambda _s: None,
        )
    assert excinfo.value.auth_expired is True
    assert "SMS code" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# _TerminalCodeReader
#
# The reader is a pure, injectable abstraction so it is unit-testable without a
# real TTY, real stdin, or real Playwright. `read_line` is an injected callable
# that simulates a poll-with-timeout line reader (it returns a line, returns the
# empty string on a poll-timeout, or raises EOFError/KeyboardInterrupt). The
# reader thread NEVER touches the Playwright page — it only produces a string.
# --------------------------------------------------------------------------- #


def test_terminal_reader_returns_typed_code_via_get_code():
    def read_line(stop_event):
        # First call yields the code; never called again because get_code drains.
        return "123456"

    reader = _TerminalCodeReader(read_line=read_line, is_tty=True)
    reader.start()
    # Poll get_code until the background thread has put the code on the queue.
    code = None
    for _ in range(200):
        code = reader.get_code()
        if code is not None:
            break
        time_sleep_tiny()
    reader.cancel()
    assert code == "123456"


def test_terminal_reader_cancel_stops_thread_no_hang():
    """A fake read_line that respects the stop_event: cancel() => clean join."""

    def read_line(stop_event):
        # Poll-with-timeout loop that wakes to honour cancellation.
        while not stop_event.is_set():
            stop_event.wait(0.01)
        return ""  # woke because we were cancelled; produce nothing useful

    reader = _TerminalCodeReader(read_line=read_line, is_tty=True)
    reader.start()
    reader.cancel()
    # join(timeout) must return promptly — the thread must not be wedged.
    reader.join(timeout=2.0)
    assert reader.is_alive() is False


def test_terminal_reader_non_tty_returns_none_and_never_starts_thread():
    calls = []

    def read_line(stop_event):
        calls.append(1)
        return "should-never-be-read"

    reader = _TerminalCodeReader(read_line=read_line, is_tty=False)
    reader.start()
    assert reader.get_code() is None
    # Non-TTY (cron): the reader must never block and never invoke read_line.
    assert calls == []
    assert reader.is_alive() is False
    reader.cancel()  # idempotent / safe even though nothing started


def test_terminal_reader_eof_is_swallowed_get_code_returns_none():
    def read_line(stop_event):
        raise EOFError()

    reader = _TerminalCodeReader(read_line=read_line, is_tty=True)
    reader.start()
    reader.join(timeout=2.0)
    assert reader.get_code() is None
    assert reader.is_alive() is False


def test_terminal_reader_keyboardinterrupt_is_swallowed():
    def read_line(stop_event):
        raise KeyboardInterrupt()

    reader = _TerminalCodeReader(read_line=read_line, is_tty=True)
    reader.start()
    reader.join(timeout=2.0)
    assert reader.get_code() is None
    assert reader.is_alive() is False


def test_resolve_otp_cancels_reader_when_browser_wins():
    """Integration: browser finishing auth must cancel the terminal reader."""
    cancelled = {"v": False}

    class _Reader:
        def get_code(self):
            return None

        def cancel(self):
            cancelled["v"] = True

    reader = _Reader()
    res = _resolve_otp(
        get_code=reader.get_code,
        is_authed=lambda: True,
        submit_code=lambda code: pytest.fail("submit_code must not run when browser wins"),
        now=lambda: 0.0,
        sleep=lambda _s: None,
        on_done=reader.cancel,
    )
    assert res == "browser"
    assert cancelled["v"] is True


def test_resolve_otp_cancels_reader_on_timeout():
    cancelled = {"v": False}
    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 100.0
        return clock["t"]

    with pytest.raises(Doch1Error):
        _resolve_otp(
            get_code=lambda: None,
            is_authed=lambda: False,
            submit_code=lambda code: None,
            timeout=10.0,
            now=fake_now,
            sleep=lambda _s: None,
            on_done=lambda: cancelled.__setitem__("v", True),
        )
    assert cancelled["v"] is True


def time_sleep_tiny():
    import time as _t

    _t.sleep(0.001)


# --------------------------------------------------------------------------- #
# _needs_headed_for_sms — pure headless+SMS mismatch predicate (W2)
#
# The browser launch is not unit-testable, but the DECISION is. The predicate
# maps (headless, otc_present) -> "this tenant needs a headed window for SMS".
# --------------------------------------------------------------------------- #


def test_needs_headed_when_headless_and_otc_present():
    # SMS box reached while running headless -> the IDF SMS tenant can't be
    # satisfied headless: needs a headed window.
    assert _needs_headed_for_sms(headless=True, otc_present=True) is True


def test_no_decision_when_headed_and_otc_present():
    # Already headed: the human can read the SMS box. Fine.
    assert _needs_headed_for_sms(headless=False, otc_present=True) is False


def test_no_decision_when_headless_but_no_otc_yet():
    # No OTC screen reached yet -> nothing to decide.
    assert _needs_headed_for_sms(headless=True, otc_present=False) is False


def test_no_decision_when_headed_and_no_otc():
    assert _needs_headed_for_sms(headless=False, otc_present=False) is False


# --------------------------------------------------------------------------- #
# _raise_if_headless_sms — fail-fast guard wired into auto-mode
# --------------------------------------------------------------------------- #


def test_raise_if_headless_sms_raises_documented_error():
    with pytest.raises(Doch1Error) as excinfo:
        _raise_if_headless_sms(headless=True, otc_present=True)
    msg = str(excinfo.value)
    assert "headed window" in msg
    assert "--manual" in msg
    assert "DOCH1_HEADFUL" in msg
    # Not an auth-expiry; it's a config/usage mismatch.
    assert excinfo.value.auth_expired is False


def test_raise_if_headless_sms_noop_when_headed():
    # Must NOT raise when already headed (the normal headed SMS path).
    assert _raise_if_headless_sms(headless=False, otc_present=True) is None


def test_raise_if_headless_sms_noop_when_no_otc():
    # Must NOT raise before an OTC screen is reached.
    assert _raise_if_headless_sms(headless=True, otc_present=False) is None

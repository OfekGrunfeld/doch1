"""Regression tests for the CONFIRMED security findings (see plans/SECURITY_AUDIT.md).

Three classes of fix, all hermetic (no network / no browser):
  1. Unguarded datetime.fromisoformat() on UNTRUSTED server JSON must not crash
     scheduled_window() / member_history() — bad records are skipped.
  2. auth.json is chmod 0o600 on EVERY persist (login + live-session refresh).
  3. Outbound Telegram alert payloads carry no raw server body / exception text.
"""

from __future__ import annotations

import os
import stat

import pytest
from conftest import FakeClient

from doch1 import api, cli

# ---------- 1. malformed server dates must not crash ----------


@pytest.mark.parametrize(
    "bad_date",
    [
        "not-a-date",
        "2026-13-45",  # impossible month/day
        "",  # empty
        "32.01.2026",  # wrong format entirely
        "2026/01/01",  # slashes
        "\x00\x1bgarbage",  # control chars / ANSI
        12345,  # not even a string (TypeError territory)
        None,
        {"nested": "obj"},
    ],
)
def test_member_history_skips_malformed_dates(bad_date):
    c = FakeClient(json_reply={"days": [{"date": bad_date, "reportedMainName": "x"}]})
    # Must not raise ValueError/TypeError — the bad record is simply skipped.
    out = api.member_history(c, 5, 2026)
    assert out == []


def test_member_history_keeps_good_skips_bad():
    c = FakeClient(
        json_reply={
            "days": [
                {"date": "garbage"},
                {"date": "2026-05-10", "reportedMainName": "At base"},
                {"date": None},
            ]
        }
    )
    out = api.member_history(c, 5, 2026)
    assert len(out) == 1
    assert out[0].date.isoformat() == "2026-05-10"


def test_scheduled_window_skips_malformed_dates_and_bounds():
    c = FakeClient(
        json_reply={
            "days": [{"date": "nope"}, {"date": "2026-05-10"}],
            "minDate": "also-bad",
            "maxDate": "2026-13-99",
        }
    )
    filled, min_d, max_d = api.scheduled_window(c, {(5, 2026)})
    assert {d.isoformat() for d in filled} == {"2026-05-10"}
    # Malformed window bounds are treated as absent, not a crash.
    assert min_d is None
    assert max_d is None


def test_scheduled_window_good_bounds_parse():
    c = FakeClient(json_reply={"days": [], "minDate": "2026-05-01", "maxDate": "2026-05-31"})
    _, min_d, max_d = api.scheduled_window(c, {(5, 2026)})
    assert min_d.isoformat() == "2026-05-01"
    assert max_d.isoformat() == "2026-05-31"


# ---------- 2. auth.json perms on every persist ----------


class _FakeCtx:
    """Stand-in for a Playwright context: writing storage_state drops a file at
    the requested path with default (world-readable) perms, like the real one."""

    def storage_state(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"cookies": []}')
        os.chmod(path, 0o644)  # simulate default-umask world-readable write


def test_persist_state_is_chmod_600(tmp_path):
    from doch1 import session

    dest = tmp_path / "nested" / "auth.json"
    session._persist_state(_FakeCtx(), dest)
    assert dest.exists()
    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    # No stray temp files left behind.
    assert list(dest.parent.glob("*.tmp.*")) == []


def test_persist_state_parent_is_private(tmp_path):
    from doch1 import session

    dest = tmp_path / "cfg" / "auth.json"
    session._persist_state(_FakeCtx(), dest)
    parent_mode = stat.S_IMODE(dest.parent.stat().st_mode)
    assert parent_mode == 0o700, f"expected 0o700, got {oct(parent_mode)}"


# ---------- 3. alert text sanitization ----------


def test_sanitize_alert_strips_control_chars_and_caps():
    raw = "FAILED\n\r\t<html>500 Internal\x1b[31m server stacktrace</html>" + "X" * 500
    out = cli._sanitize_alert(raw)
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert "\x1b" not in out
    assert len(out) <= 120


def test_alert_text_has_no_raw_exception():
    raw_server = "HTTP 500: <secret server diagnostic body>"
    err = api.Doch1Error(raw_server, auth_expired=False)
    msg = cli._alert_text("today", auth_expired=err.auth_expired)
    assert "secret server diagnostic" not in msg
    assert "<" not in msg and ">" not in msg
    assert "today" in msg


def test_alert_text_reports_auth_expired_flag():
    msg = cli._alert_text("history", auth_expired=True)
    assert "session expired" in msg
    assert "history" in msg


def test_alert_payload_sanitized_end_to_end(monkeypatch):
    """Even if a caller passes raw text, the outbound Telegram body is cleaned."""
    captured = {}

    def _fake_post(url, data=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["text"] = data["text"]
        captured["kwargs"] = kwargs

        class _R:
            pass

        return _R()

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)
    cfg = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}
    cli._alert(cfg, "boom\n\rHTTP 500: <raw server body>\x1b[0m")
    assert "\n" not in captured["text"] and "\r" not in captured["text"]
    assert "\x1b" not in captured["text"]
    assert len(captured["text"]) <= 120
    # Telegram POST must pin the host and never follow a redirect (token leak).
    assert captured["url"].startswith("https://api.telegram.org/")
    assert captured["kwargs"].get("allow_redirects") is False


# ---------- server-response leak guard at the transport layer ----------


def test_requests_client_guard_drops_server_body():
    msg = None
    try:
        api.RequestsClient._guard(500, "application/json", "<secret server body 12345>")
    except api.Doch1Error as e:
        msg = str(e)
    assert msg == "HTTP 500 error"
    assert "secret server body" not in msg

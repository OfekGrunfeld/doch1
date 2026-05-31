"""Hermetic tests for opt-in, SECRET-SAFE run-logging (observability.py).

Proves the two contractual guarantees the security audit demands:

1. When enabled (DOCH1_LOG=1) a structured JSON line is emitted per run, with a
   CLOSED, secret-free schema and a reason *category* (never raw server text).
2. The line NEVER contains any password / cookie / token / OTP / account-id /
   server-body — even when those secrets are present in the environment/config.

No network, no browser, no real secrets leave the box: the CLI is driven through
Typer's CliRunner with a FakeClient transport (mirrors tests/eval).
"""

from __future__ import annotations

import contextlib
import json
import os
import stat

import pytest
from conftest import FakeClient
from typer.testing import CliRunner

from doch1 import api, cli, observability

runner = CliRunner()

# Values that must NEVER appear in any log line. Seeded into config/env below.
_SECRETS = {
    "DOCH1_PASS": "sup3r-secret-passw0rd",
    "DOCH1_COOKIE": "WAFcookie=AAAA.BBBB.CCCC; reese84=zzz",
    "TELEGRAM_BOT_TOKEN": "123456:AAH-telegram-bot-token-XYZ",
    "TELEGRAM_CHAT_ID": "987654321",
    "DOCH1_TOTP_SEED": "JBSWY3DPEHPK3PXP",
    "DOCH1_USER": "soldier@idf.il",
}


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """Enable logging to an isolated file and seed forbidden secrets into env."""
    path = tmp_path / "doch1.log"
    monkeypatch.setenv("DOCH1_LOG", "1")
    monkeypatch.setenv("DOCH1_LOG_FILE", str(path))
    for k, v in _SECRETS.items():
        monkeypatch.setenv(k, v)
    return path


def _patch_client(monkeypatch, client) -> None:
    @contextlib.contextmanager
    def fake_client_cm(_cfg):
        with client as c:
            yield c

    monkeypatch.setattr(cli, "_client", fake_client_cm)

    class _FakeStatePath:
        def exists(self) -> bool:
            return True

    monkeypatch.setattr("doch1.session.state_path", lambda: _FakeStatePath())


def _read_lines(path):
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# 1. A line is emitted on demand                                              #
# --------------------------------------------------------------------------- #


def test_today_emits_one_structured_line(monkeypatch, log_file):
    _patch_client(monkeypatch, FakeClient(multipart_reply="true"))
    res = runner.invoke(cli.app, ["today", "--json"])
    assert res.exit_code == 0
    rows = _read_lines(log_file)
    assert len(rows) == 1
    row = rows[0]
    # Closed schema: exactly these keys, nothing else.
    assert set(row) == {
        "ts",
        "command",
        "result",
        "reason",
        "duration_ms",
        "transport",
        "auth_expired",
    }
    assert row["command"] == "today"
    assert row["result"] == "ok"
    assert row["reason"] == "ok"
    assert row["transport"] == "browser"
    assert row["auth_expired"] is False
    assert isinstance(row["duration_ms"], int)


def test_auth_expired_outcome_logged_as_category(monkeypatch, log_file):
    _patch_client(monkeypatch, FakeClient(fail_status=401))
    res = runner.invoke(cli.app, ["today", "--json"])
    assert res.exit_code == 1
    row = _read_lines(log_file)[0]
    assert row["result"] == "fail"
    assert row["reason"] == "auth_expired"
    assert row["auth_expired"] is True


def test_rejected_outcome_logged_as_category(monkeypatch, log_file):
    _patch_client(monkeypatch, FakeClient(multipart_reply="false"))
    res = runner.invoke(cli.app, ["today", "--json"])
    assert res.exit_code == 1
    row = _read_lines(log_file)[0]
    assert row["result"] == "fail"
    assert row["reason"] == "rejected"


def test_week_history_status_each_emit_lines(monkeypatch, log_file):
    for argv, client in (
        (["week", "--json"], FakeClient(json_reply={"days": []})),
        (["history", "--json"], FakeClient(json_reply={"days": []})),
        (["status", "--json"], FakeClient(json_reply={"days": []})),
    ):
        _patch_client(monkeypatch, client)
        runner.invoke(cli.app, argv)
    cmds = [r["command"] for r in _read_lines(log_file)]
    assert {"week", "history", "status"}.issubset(set(cmds))


def test_bad_date_logged_as_bad_input(monkeypatch, log_file):
    _patch_client(monkeypatch, FakeClient())
    res = runner.invoke(cli.app, ["week", "not-a-date", "--json"])
    assert res.exit_code == 1
    row = _read_lines(log_file)[0]
    assert row["reason"] == "bad_input"


# --------------------------------------------------------------------------- #
# 2. NO secret/PII ever appears in a log line                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "argv,client",
    [
        (["today", "--json"], FakeClient(multipart_reply="true")),
        (["today", "--json"], FakeClient(fail_status=401)),
        (["history", "--json"], FakeClient(json_reply={"days": []})),
        (["status", "--json"], FakeClient(fail_status=403)),
    ],
)
def test_no_forbidden_secret_in_log(monkeypatch, log_file, argv, client):
    _patch_client(monkeypatch, client)
    runner.invoke(cli.app, argv)
    raw = log_file.read_text()
    for name, secret in _SECRETS.items():
        assert secret not in raw, f"{name} value leaked into the log line"
    # Forbidden *field* keys must also be absent from the structured record.
    for row in _read_lines(log_file):
        for forbidden in (
            "password",
            "cookie",
            "token",
            "otp",
            "seed",
            "secret",
            "account",
            "chat_id",
            "user",
            "error",
            "body",
        ):
            assert forbidden not in set(row), f"forbidden field {forbidden!r} present"


def test_tripwire_rejects_a_secret_value(monkeypatch, tmp_path):
    """If a regression ever put a secret into a field, the write must FAIL loudly."""
    path = tmp_path / "doch1.log"
    monkeypatch.setenv("DOCH1_LOG", "1")
    monkeypatch.setenv("DOCH1_LOG_FILE", str(path))
    monkeypatch.setenv("DOCH1_PASS", "leaky-password-value")
    # Force load_config to surface DOCH1_PASS as a known secret-bearing key.
    monkeypatch.setattr(api, "load_config", lambda: {"DOCH1_PASS": "leaky-password-value"})
    with pytest.raises(ValueError, match="tripwire"):
        # Smuggle the secret in via the command field to trip the sweep.
        observability.log_run(
            "leaky-password-value",
            ok=False,
            reason="error",
            duration_ms=1,
            transport="browser",
        )
    assert not path.exists() or path.read_text() == ""


# --------------------------------------------------------------------------- #
# 3. Default OFF + file perms                                                 #
# --------------------------------------------------------------------------- #


def test_disabled_by_default(monkeypatch, tmp_path):
    path = tmp_path / "doch1.log"
    monkeypatch.delenv("DOCH1_LOG", raising=False)
    monkeypatch.setenv("DOCH1_LOG_FILE", str(path))
    _patch_client(monkeypatch, FakeClient(multipart_reply="true"))
    res = runner.invoke(cli.app, ["today", "--json"])
    assert res.exit_code == 0
    assert not path.exists(), "no log file should be created when DOCH1_LOG is unset"


def test_log_file_created_0600(monkeypatch, log_file):
    _patch_client(monkeypatch, FakeClient(multipart_reply="true"))
    runner.invoke(cli.app, ["today", "--json"])
    assert log_file.exists()
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert mode == 0o600, f"log file should be 0o600, got {oct(mode)}"


def test_classify_reason_closed_vocabulary():
    assert observability.classify_reason(ok=True) == "ok"
    assert observability.classify_reason(ok=False, auth_expired=True) == "auth_expired"
    assert observability.classify_reason(ok=False, category="rejected") == "rejected"
    # An unknown/raw category can NEVER pass through — collapses to "error".
    assert observability.classify_reason(ok=False, category="HTTP 500: <html>boom") == "error"

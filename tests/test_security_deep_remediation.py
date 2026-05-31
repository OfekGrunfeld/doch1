"""Regression tests for the CONFIRMED findings in plans/SECURITY_AUDIT_DEEP.md.

All hermetic (no network / no browser). Each test names the audit finding it
locks down.
"""

from __future__ import annotations

import os
import stat

import pytest

from doch1 import api, cron
from doch1.api import Doch1Error

# ---------- #1 crontab newline injection + #14 unquoted-path word-splitting ----


def test_cron_rejects_newline_injection_daily():
    # The headline critical: a newline in --daily must not smuggle a 2nd cron line.
    with pytest.raises(ValueError):
        cron.build_lines("/p", "/p/py", daily="30 7 * * *\n* * * * * /bin/sh -i")


def test_cron_rejects_newline_injection_weekly():
    with pytest.raises(ValueError):
        cron.build_lines("/p", "/p/py", weekly="40 7 * * 0\n@reboot /bin/sh")


@pytest.mark.parametrize(
    "bad",
    [
        "* * * *",  # only 4 fields
        "* * * * * *",  # 6 fields
        "30 7 * * *; rm -rf /",  # shell metachar
        "30 7 * * * && curl evil",  # command append
        "$(touch pwned) 7 * * *",  # command substitution
        "30 7 * * `id`",  # backtick
        "",  # empty
    ],
)
def test_cron_rejects_malformed_schedules(bad):
    with pytest.raises(ValueError):
        cron.build_lines("/p", "/p/py", daily=bad)


@pytest.mark.parametrize(
    "good",
    ["30 7 * * *", "0 */2 * * *", "15,45 8-17 * * 1-5", "40 7 * * 0"],
)
def test_cron_accepts_valid_schedules(good):
    out = cron.build_lines("/p", "/p/py", daily=good, with_weekly=False)
    assert out[0][1].startswith(good)


def test_cron_quotes_paths_with_spaces():
    # #14: a project dir / interpreter path with spaces must be shlex-quoted so it
    # cannot word-split into a different binary in the parent dir.
    out = cron.build_lines("/opt/my doch1", "/opt/my doch1/.venv/bin/python")
    daily_cmd = out[0][1]
    assert "cd '/opt/my doch1'" in daily_cmd
    assert "'/opt/my doch1/.venv/bin/python'" in daily_cmd
    assert "'/opt/my doch1/doch1.log'" in daily_cmd
    # No bare unquoted space-containing path remains.
    assert "cd /opt/my doch1 &&" not in daily_cmd


def test_cron_quotes_env_values():
    out = cron.build_lines("/p", "/p/py", env={"FOO": "a b; rm -rf /"}, with_weekly=False)
    cmd = out[0][1]
    assert "'a b; rm -rf /'" in cmd


# ---------- #4 / #10 DOCH1_ENV / DOCH1_STATE path traversal & symlink ----------


def test_safe_override_rejects_dotdot():
    with pytest.raises(Doch1Error):
        api.safe_override_path("../../etc/passwd", var="DOCH1_ENV")


def test_safe_override_rejects_absolute_dotdot():
    with pytest.raises(Doch1Error):
        api.safe_override_path("/var/tmp/../../etc/passwd", var="DOCH1_STATE")


def test_safe_override_rejects_symlink(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(Doch1Error):
        api.safe_override_path(str(link / "auth.json"), var="DOCH1_STATE")


def test_safe_override_allows_plain_path(tmp_path):
    p = tmp_path / "sub" / "auth.json"
    out = api.safe_override_path(str(p), var="DOCH1_STATE")
    assert str(out) == os.path.abspath(str(p))


def test_config_path_honours_safe_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCH1_ENV", str(tmp_path / ".env"))
    assert api.config_path() == tmp_path / ".env"


def test_config_path_rejects_traversal(monkeypatch):
    monkeypatch.setenv("DOCH1_ENV", "../../../../etc/passwd")
    with pytest.raises(Doch1Error):
        api.config_path()


def test_state_path_rejects_traversal(monkeypatch):
    from doch1 import session

    monkeypatch.setenv("DOCH1_STATE", "../../secret/auth.json")
    with pytest.raises(Doch1Error):
        session.state_path()


# ---------- #7 / #8 / #9 non-dict server JSON must not crash ----------


class _Client:
    def __init__(self, reply):
        self._reply = reply

    def post_json(self, path, body):
        return self._reply


@pytest.mark.parametrize(
    "reply",
    [
        {"days": "a string of chars"},  # iterating yields chars -> .get() crash
        {"days": [1, 2, 3]},  # int elements
        {"days": ["x", None, {"date": "2026-05-10"}]},  # mixed; good one survives
        {"days": 123},  # not iterable-as-dicts
        ["not", "a", "dict"],  # top-level non-dict
        "totally wrong",
    ],
)
def test_member_history_survives_nondict_json(reply):
    out = api.member_history(_Client(reply), 5, 2026)
    assert isinstance(out, list)


def test_member_history_keeps_good_among_garbage():
    out = api.member_history(
        _Client({"days": ["x", 7, {"date": "2026-05-10", "reportedMainName": "At base"}]}),
        5,
        2026,
    )
    assert [d.date.isoformat() for d in out] == ["2026-05-10"]


@pytest.mark.parametrize(
    "reply",
    [
        {"days": "string"},
        {"days": [1, 2]},
        {"days": 99},
        ["wrong"],
        "wrong",
    ],
)
def test_scheduled_window_survives_nondict_json(reply):
    filled, _, _ = api.scheduled_window(_Client(reply), {(5, 2026)})
    assert filled == set()


def test_tui_scheduled_adapter_survives_nondict():
    from doch1.tui import data as tui_data

    assert tui_data._scheduled_to_history_day("not a dict") is None
    assert tui_data._scheduled_to_history_day(123) is None
    assert tui_data._scheduled_to_history_day(None) is None


# ---------- #11 / #19 doch1.log pre-created 0o600, symlink refused ----------


def test_precreate_cron_log_is_0600(tmp_path):
    from doch1 import cli

    log = tmp_path / "doch1.log"
    cli._precreate_cron_log(str(log))
    assert log.exists()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_precreate_cron_log_refuses_symlink(tmp_path):
    from doch1 import cli

    victim = tmp_path / "victim"
    victim.write_text("secret")
    link = tmp_path / "doch1.log"
    link.symlink_to(victim)
    with pytest.raises(api.Doch1Error):
        cli._precreate_cron_log(str(link))
    # The victim file must be untouched.
    assert victim.read_text() == "secret"


# ---------- #16 URL origin binding (substring -> prefix) ----------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://one.prat.idf.il/hp", True),
        ("https://one.prat.idf.il/", True),
        ("https://one.prat.idf.il", True),
        ("https://evil.com/one.prat.idf.il", False),  # the substring bypass
        ("https://one.prat.idf.il.evil.com/", False),
        ("https://one.prat.idf.il/login", False),
        ("https://login.microsoftonline.com/one.prat.idf.il", False),
    ],
)
def test_is_app_url_binds_to_origin(url, expected):
    from doch1 import session

    assert session._is_app_url(url) is expected

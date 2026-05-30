"""Pure tests for doch1.cron — crontab line generation / merge / remove / status.

No real crontab is ever touched; these are pure-function tests (CI-safe).
"""

from __future__ import annotations

from doch1 import cron

PROJ = "/home/me/doch1"
PY = "/home/me/doch1/.venv/bin/python"


def _lines(**kw):
    return cron.build_lines(PROJ, PY, **kw)


def test_build_lines_emits_both_tags():
    out = _lines()
    tags = [t for t, _ in out]
    assert cron.TAG_DAILY in tags
    assert cron.TAG_WEEKLY in tags
    assert len(out) == 2


def test_daily_line_is_today_not_week():
    out = dict(_lines())
    daily = out[cron.TAG_DAILY]
    assert "-m doch1.main" in daily
    assert "week" not in daily


def test_weekly_line_runs_week():
    out = dict(_lines())
    weekly = out[cron.TAG_WEEKLY]
    assert "week" in weekly
    assert "-m doch1.main" in weekly


def test_every_line_carries_noninteractive_guard():
    for _tag, line in _lines():
        assert "DOCH1_NONINTERACTIVE=1" in line
        assert "DOCH1_CRON=1" in line


def test_lines_cd_and_log():
    for _tag, line in _lines():
        assert f"cd {PROJ}" in line
        assert f">> {PROJ}/doch1.log 2>&1" in line


def test_schedules_appear():
    out = dict(_lines(daily="30 7 * * *", weekly="40 7 * * 0"))
    assert out[cron.TAG_DAILY].startswith("30 7 * * *")
    assert out[cron.TAG_WEEKLY].startswith("40 7 * * 0")


def test_with_weekly_false_omits_weekly():
    out = _lines(with_weekly=False)
    tags = [t for t, _ in out]
    assert cron.TAG_DAILY in tags
    assert cron.TAG_WEEKLY not in tags
    assert len(out) == 1


def test_env_dict_appears_in_lines():
    out = dict(_lines(env={"DOCH1_MAIN_CODE": "02"}))
    assert "DOCH1_MAIN_CODE=02" in out[cron.TAG_DAILY]
    assert "DOCH1_MAIN_CODE=02" in out[cron.TAG_WEEKLY]


def test_merge_into_empty():
    merged = cron.merge("", _lines())
    assert cron.TAG_DAILY in merged
    assert cron.TAG_WEEKLY in merged


def test_merge_is_idempotent():
    once = cron.merge("", _lines())
    twice = cron.merge(once, _lines())
    assert once == twice
    # no duplicate tags
    assert twice.count(cron.TAG_DAILY) == 1
    assert twice.count(cron.TAG_WEEKLY) == 1


def test_merge_replaces_stale_line():
    # Old install.sh style line under the daily tag (no guard).
    stale = (
        f"{cron.TAG_DAILY}\n30 7 * * * cd {PROJ} && {PY} -m doch1.main >> {PROJ}/doch1.log 2>&1\n"
    )
    merged = cron.merge(stale, _lines())
    # tag appears exactly once (replaced, not appended)
    assert merged.count(cron.TAG_DAILY) == 1
    # the new line has the guard the stale one lacked
    assert "DOCH1_NONINTERACTIVE=1" in merged


def test_merge_preserves_unrelated_lines():
    existing = "0 0 * * * /usr/bin/backup\n"
    merged = cron.merge(existing, _lines())
    assert "/usr/bin/backup" in merged


def test_remove_strips_tags_and_commands():
    merged = cron.merge("0 0 * * * /usr/bin/backup\n", _lines())
    stripped = cron.remove(merged, [cron.TAG_DAILY, cron.TAG_WEEKLY])
    assert cron.TAG_DAILY not in stripped
    assert cron.TAG_WEEKLY not in stripped
    assert "-m doch1.main" not in stripped
    # unrelated line survives
    assert "/usr/bin/backup" in stripped


def test_remove_on_empty_is_noop():
    assert cron.remove("", [cron.TAG_DAILY]).strip() == ""


def test_status_empty():
    st = cron.status("")
    assert st[cron.TAG_DAILY]["present"] is False
    assert st[cron.TAG_WEEKLY]["present"] is False


def test_status_populated():
    merged = cron.merge("", _lines())
    st = cron.status(merged)
    assert st[cron.TAG_DAILY]["present"] is True
    assert st[cron.TAG_WEEKLY]["present"] is True
    assert "-m doch1.main" in st[cron.TAG_DAILY]["line"]


def test_status_partial():
    merged = cron.merge("", _lines(with_weekly=False))
    st = cron.status(merged)
    assert st[cron.TAG_DAILY]["present"] is True
    assert st[cron.TAG_WEEKLY]["present"] is False


# ---------- thin I/O shim test (subprocess mocked, no real crontab) ----------


def test_cli_install_writes_merged_crontab(monkeypatch):
    from typer.testing import CliRunner

    from doch1 import cli

    captured = {}
    monkeypatch.setattr(cli, "_crontab_read", lambda: "")
    monkeypatch.setattr(cli, "_crontab_write", lambda text: captured.update(text=text))
    monkeypatch.setattr(cli, "_cfg", lambda: {})

    result = CliRunner().invoke(cli.app, ["cron", "install"])
    assert result.exit_code == 0, result.output
    written = captured["text"]
    assert cron.TAG_DAILY in written
    assert cron.TAG_WEEKLY in written
    assert "DOCH1_NONINTERACTIVE=1" in written


def test_cli_status_json_read_only(monkeypatch):
    from typer.testing import CliRunner

    from doch1 import cli

    monkeypatch.setattr(cli, "_crontab_read", lambda: "")
    monkeypatch.setattr(cli, "_cfg", lambda: {})
    result = CliRunner().invoke(cli.app, ["cron", "status", "--json"])
    assert result.exit_code == 0, result.output
    assert '"cron status"' in result.output


def test_cli_remove_writes(monkeypatch):
    from typer.testing import CliRunner

    from doch1 import cli

    captured = {}
    seeded = cron.merge("", cron.build_lines(PROJ, PY))
    monkeypatch.setattr(cli, "_crontab_read", lambda: seeded)
    monkeypatch.setattr(cli, "_crontab_write", lambda text: captured.update(text=text))
    monkeypatch.setattr(cli, "_cfg", lambda: {})
    result = CliRunner().invoke(cli.app, ["cron", "remove"])
    assert result.exit_code == 0, result.output
    assert cron.TAG_DAILY not in captured["text"]

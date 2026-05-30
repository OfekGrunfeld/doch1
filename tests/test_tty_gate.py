"""_should_launch_ui() truth table + _default routing (cron vs UI)."""

from __future__ import annotations

from doch1 import cli


def _set_tty(monkeypatch, stdin: bool, stdout: bool) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: stdin, raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: stdout, raising=False)


def _clear_env(monkeypatch) -> None:
    for k in ("DOCH1_FORCE_UI", "DOCH1_NONINTERACTIVE", "CI", "DOCH1_CRON"):
        monkeypatch.delenv(k, raising=False)


def test_interactive_tty_launches_ui(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, True)
    assert cli._should_launch_ui() is True


def test_no_tty_does_not_launch(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, False, False)
    assert cli._should_launch_ui() is False


def test_stdout_not_tty_does_not_launch(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, False)
    assert cli._should_launch_ui() is False


def test_noninteractive_killswitch(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, True)
    monkeypatch.setenv("DOCH1_NONINTERACTIVE", "1")
    assert cli._should_launch_ui() is False


def test_killswitch_off_values_do_not_disable(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, True)
    for v in ("", "0", "false"):
        monkeypatch.setenv("DOCH1_NONINTERACTIVE", v)
        assert cli._should_launch_ui() is True


def test_force_ui_overrides_no_tty(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, False, False)
    monkeypatch.setenv("DOCH1_FORCE_UI", "1")
    assert cli._should_launch_ui() is True


def test_force_ui_overrides_killswitch(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, True)
    monkeypatch.setenv("DOCH1_NONINTERACTIVE", "1")
    monkeypatch.setenv("DOCH1_FORCE_UI", "1")
    assert cli._should_launch_ui() is True


def test_ci_does_not_launch(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, True)
    monkeypatch.setenv("CI", "true")
    assert cli._should_launch_ui() is False


class _Ctx:
    def __init__(self, sub):
        self.invoked_subcommand = sub


def test_default_cron_path_runs_today_not_ui(monkeypatch):
    """No TTY -> _default must call today(), never run_app."""
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, False, False)
    called = {"today": False, "ui": False}
    monkeypatch.setattr(cli, "today", lambda json_out=False: called.__setitem__("today", True))
    import doch1.tui.app as tapp

    monkeypatch.setattr(tapp, "run_app", lambda: called.__setitem__("ui", True))
    cli._default(_Ctx(None))
    assert called == {"today": True, "ui": False}


def test_default_interactive_path_runs_ui_not_today(monkeypatch):
    _clear_env(monkeypatch)
    _set_tty(monkeypatch, True, True)
    called = {"today": False, "ui": False}
    monkeypatch.setattr(cli, "today", lambda json_out=False: called.__setitem__("today", True))
    import doch1.tui.app as tapp

    monkeypatch.setattr(tapp, "run_app", lambda: called.__setitem__("ui", True))
    cli._default(_Ctx(None))
    assert called == {"today": False, "ui": True}


def test_default_with_subcommand_does_nothing(monkeypatch):
    called = {"today": False, "ui": False}
    monkeypatch.setattr(cli, "today", lambda json_out=False: called.__setitem__("today", True))
    cli._default(_Ctx("history"))
    assert called == {"today": False, "ui": False}

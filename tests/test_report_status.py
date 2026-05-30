"""Action-plumbing tests: the SELECTED status reaches the wire (W5).

Uses the FakeClient from conftest — no network, no browser.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import FakeClient

from doch1 import api, statuses
from doch1.api import Doch1Error
from doch1.statuses import Status
from doch1.tui.data import fill_week_plan

# ---------- report_today ----------


def test_report_today_defaults_to_at_base(fake_client):
    assert api.report_today(fake_client) is True
    path, fields = fake_client.multipart_calls[0]
    assert path == api.P_TODAY
    assert fields["MainCode"] == "01"
    assert fields["SecondaryCode"] == "01"


def test_report_today_sends_selected_codes(fake_client):
    sel = Status("04", "02", "he", "Off base")
    api.report_today(fake_client, sel)
    _, fields = fake_client.multipart_calls[0]
    assert fields["MainCode"] == "04"
    assert fields["SecondaryCode"] == "02"


def test_report_today_rejected_non_true():
    c = FakeClient(multipart_reply="false")
    assert api.report_today(c) is False


def test_report_today_auth_expired_propagates():
    c = FakeClient(fail_status=401)
    with pytest.raises(Doch1Error) as exc:
        api.report_today(c)
    assert exc.value.auth_expired is True


def test_report_today_waf_wall():
    c = FakeClient(html_wall=True)
    with pytest.raises(Doch1Error) as exc:
        api.report_today(c)
    assert exc.value.auth_expired is True


# ---------- report_future ----------


def test_report_future_sends_selected_codes_and_date(fake_client):
    sel = Status("05", "03", "he", "Annual leave")
    api.report_future(fake_client, date(2026, 6, 2), note="x", status=sel)
    path, fields = fake_client.multipart_calls[0]
    assert path == api.P_FUTURE
    assert fields["MainCode"] == "05"
    assert fields["SecondaryCode"] == "03"
    assert fields["FutureReportDate"] == "02.06.2026"
    assert fields["Note"] == "x"


def test_report_future_defaults_to_at_base(fake_client):
    api.report_future(fake_client, date(2026, 6, 2))
    _, fields = fake_client.multipart_calls[0]
    assert fields["MainCode"] == "01"
    assert fields["SecondaryCode"] == "01"


# ---------- week-fill threads status ----------


def test_fill_week_plan_threads_status_into_writes():
    # FakeClient returns {} for post_json so scheduled_window sees no filled days
    # and no min/max window -> every future day is written.
    c = FakeClient()
    sel = Status("06", "01", "he", "Abroad")
    days = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    today_d = date(2026, 6, 1)
    results, failures = fill_week_plan(c, days, today_d, status=sel)
    assert not failures
    # today + two future writes, all carrying the selected codes
    for _, fields in c.multipart_calls:
        assert fields["MainCode"] == "06"
        assert fields["SecondaryCode"] == "01"
    actions = [r["action"] for r in results]
    assert actions == ["today", "future", "future"]


def test_fill_week_plan_defaults_to_at_base():
    c = FakeClient()
    days = [date(2026, 6, 1)]
    fill_week_plan(c, days, date(2026, 6, 1))
    _, fields = c.multipart_calls[0]
    assert fields["MainCode"] == "01"


# ---------- env-default plumbing through load_config ----------


def test_load_config_reads_status_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCH1_MAIN_CODE", "04")
    monkeypatch.setenv("DOCH1_SECONDARY_CODE", "02")
    monkeypatch.setenv("DOCH1_ENV", str(tmp_path / "nonexistent.env"))
    cfg = api.load_config()
    assert cfg["DOCH1_MAIN_CODE"] == "04"
    assert cfg["DOCH1_SECONDARY_CODE"] == "02"
    sel = statuses.resolve_selection(key=None, cfg=cfg)
    assert sel.main == "04" and sel.secondary == "02"

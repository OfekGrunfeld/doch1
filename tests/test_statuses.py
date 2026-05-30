"""Pure-logic tests for the report-status registry/resolver (W5).

No network, no browser: statuses.py is import-cheap and fully unit-testable.
"""

from __future__ import annotations

import pytest

from doch1 import statuses

# ---------- DEFAULT / at-base ----------


def test_default_is_at_base_0101():
    assert statuses.DEFAULT.main == "01"
    assert statuses.DEFAULT.secondary == "01"
    assert statuses.DEFAULT.en  # has an English label


def test_registry_contains_default_key():
    assert ("01", "01") in statuses.REGISTRY
    assert statuses.REGISTRY[("01", "01")] == statuses.DEFAULT


# ---------- resolve(main, secondary) ----------


def test_resolve_known_codes_returns_registry_status():
    s = statuses.resolve("01", "01")
    assert s is statuses.DEFAULT
    assert s.en == statuses.DEFAULT.en


def test_resolve_unknown_codes_returns_synthetic_label():
    s = statuses.resolve("07", "03")
    assert s.main == "07"
    assert s.secondary == "03"
    # synthetic label is clearly marked as unknown and carries the codes
    assert "07" in s.en and "03" in s.en
    assert "unknown" in s.en.lower()


# ---------- by_key(friendly alias) ----------


def test_by_key_at_base_aliases():
    for key in ("at-base", "at_base", "atbase", "base"):
        assert statuses.by_key(key) is statuses.DEFAULT


def test_by_key_is_case_insensitive():
    assert statuses.by_key("At-Base") is statuses.DEFAULT


def test_by_key_unknown_rejected_with_refresh_hint():
    with pytest.raises(statuses.UnknownStatusError) as exc:
        statuses.by_key("leave")
    msg = str(exc.value)
    assert "statuses --refresh" in msg


def test_by_key_unknown_garbage_rejected():
    with pytest.raises(statuses.UnknownStatusError):
        statuses.by_key("totally-bogus")


# ---------- label_en ----------


def test_label_en_known():
    assert statuses.label_en("01", "01") == statuses.DEFAULT.en


def test_label_en_unknown_is_synthetic():
    lbl = statuses.label_en("09", "09")
    assert "09" in lbl


# ---------- Hebrew gloss home (no drift with cli) ----------


def test_translate_table_lives_in_statuses():
    # The Hebrew->English gloss has a single home here.
    assert statuses.translate("נמצא/ת ביחידה") == "At base"
    assert statuses.translate('חו"ל') == "Abroad"


def test_translate_joins_on_spaced_separator():
    assert statuses.translate("נמצא/ת ביחידה / נוכח/ת") == "At base / Present"


def test_translate_passthrough_unknown():
    assert statuses.translate("something else") == "something else"


# ---------- resolution order: explicit > env > default ----------


def test_resolve_selection_explicit_key_wins(monkeypatch):
    monkeypatch.setenv("DOCH1_MAIN_CODE", "05")
    monkeypatch.setenv("DOCH1_SECONDARY_CODE", "05")
    cfg = {"DOCH1_MAIN_CODE": "05", "DOCH1_SECONDARY_CODE": "05"}
    # explicit at-base key beats the env override
    s = statuses.resolve_selection(key="at-base", cfg=cfg)
    assert s is statuses.DEFAULT


def test_resolve_selection_env_codes_when_no_key():
    cfg = {"DOCH1_MAIN_CODE": "04", "DOCH1_SECONDARY_CODE": "02"}
    s = statuses.resolve_selection(key=None, cfg=cfg)
    assert s.main == "04"
    assert s.secondary == "02"


def test_resolve_selection_default_when_nothing():
    s = statuses.resolve_selection(key=None, cfg={})
    assert s is statuses.DEFAULT


def test_resolve_selection_unknown_key_raises():
    with pytest.raises(statuses.UnknownStatusError):
        statuses.resolve_selection(key="leave", cfg={})

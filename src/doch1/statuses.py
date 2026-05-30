"""Report-status registry, resolver and Hebrew->English gloss (W5).

The DOCH1 write endpoints take a MainCode/SecondaryCode pair that names *which*
presence status is being reported. Historically the code baked in the at-base
constants ``01``/``01``; this module makes the code <-> label mapping a single,
import-cheap, fully-unit-testable home so the CLI/TUI can SHOW the status being
sent and (eventually) SELECT a non-default one.

Design:
  * ``Status(main, secondary, he, en)`` — one selectable status.
  * ``DEFAULT`` — at base / present (``01``/``01``), the only code pair known
    offline today.
  * ``REGISTRY`` — ``{(main, secondary): Status}``, seeded with ``DEFAULT`` only.
    The remaining holiday/leave/off-base codes are NOT known offline; they must
    be captured from the live ``/primaries`` + ``/secondaries`` endpoints (see
    the W5 TODO below) before they can be selected. This unblocks roadmap S6's
    ``h`` (holiday) quick-fill key.
  * ``resolve`` / ``label_en`` — look up a code pair, with a clearly-marked
    synthetic "unknown" label fallback so live data renders even before the
    registry is populated.
  * ``by_key`` / ``resolve_selection`` — the friendly-alias + resolution-order
    surface the CLI uses (explicit ``--status`` > env codes > ``DEFAULT``).
  * ``translate`` / ``TRANSLATE`` — the Hebrew->English gloss, moved here so it
    has ONE home shared by history rendering and status labels (no drift).

# TODO(W5 / S6 live discovery): REGISTRY only holds the at-base default. The
# real holiday/leave/sick/abroad MainCode/SecondaryCode pairs are UNKNOWN and
# cannot be determined offline. They must be captured from a HEADED, authenticated
# Playwright session against one.prat.idf.il by observing the /primaries and
# /secondaries network requests in the UI status picker (the exact endpoint paths
# and response field names are not in the codebase). Run `doch1 login` first, then
# the maintainer discovery ritual, and paste the discovered rows into REGISTRY
# below. Until then `by_key` accepts only the at-base aliases and rejects every
# other key with the documented "run `doch1 statuses --refresh`" hint.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------- Hebrew -> English gloss (single home; shared by cli/history) ----------

# Unknown values pass through unchanged.
TRANSLATE = {
    "נמצא/ת ביחידה": "At base",
    "נוכח/ת": "Present",
    "מחוץ ליחידה": "Off base",
    "בתפקיד מחוץ ליחידה": "On duty off-base",
    "חופשה שנתית": "Annual leave",
    "חופשת מחלה": "Sick leave",
    "חופשת מחלה (גימלים)": "Sick leave (medical)",
    'חו"ל': "Abroad",
}


def translate(s: str) -> str:
    """Hebrew status -> English gloss. Unknown values pass through unchanged.

    Fields are joined with " / "; values themselves contain a gender slash
    (e.g. "נמצא/ת"), so split on the spaced separator, not a bare slash.
    """
    parts = [TRANSLATE.get(p.strip(), p.strip()) for p in s.split(" / ") if p.strip()]
    return " / ".join(parts)


# ---------- status registry ----------


@dataclass(frozen=True)
class Status:
    """One selectable report status (a MainCode/SecondaryCode pair + labels)."""

    main: str
    secondary: str
    he: str
    en: str

    @property
    def codes(self) -> str:
        return f"{self.main}/{self.secondary}"


# At base / present — the only code pair known offline (cron default, S6 `b` key).
DEFAULT = Status("01", "01", "נמצא/ת ביחידה / נוכח/ת", "At base / Present")

# (main, secondary) -> Status. Seeded with DEFAULT only; see the module TODO for
# the live-discovery step that populates the rest.
REGISTRY: dict[tuple[str, str], Status] = {
    (DEFAULT.main, DEFAULT.secondary): DEFAULT,
}

# Friendly --status aliases -> code pair. Only at-base is known offline.
_ALIASES: dict[str, tuple[str, str]] = {
    "at-base": ("01", "01"),
    "at_base": ("01", "01"),
    "atbase": ("01", "01"),
    "base": ("01", "01"),
    "present": ("01", "01"),
    "default": ("01", "01"),
}

_REFRESH_HINT = "run `doch1 statuses --refresh` to discover codes"


class UnknownStatusError(ValueError):
    """Raised when a --status KEY is not a known/selectable status."""


def resolve(main: str, secondary: str) -> Status:
    """Status for a code pair; synthetic "unknown" label fallback if not in REGISTRY.

    The fallback keeps live/unmapped codes renderable (and clearly flagged) until
    the registry is populated from live discovery.
    """
    hit = REGISTRY.get((main, secondary))
    if hit is not None:
        return hit
    label = f"Unknown status ({main}/{secondary})"
    return Status(main, secondary, label, label)


def label_en(main: str, secondary: str) -> str:
    """English label for a code pair (synthetic if unknown)."""
    return resolve(main, secondary).en


def by_key(key: str) -> Status:
    """Resolve a friendly --status alias to a Status.

    Only the at-base aliases are accepted offline; every other key is rejected
    with the documented refresh hint (the real codes need live discovery).
    """
    norm = (key or "").strip().lower()
    codes = _ALIASES.get(norm)
    if codes is None:
        raise UnknownStatusError(f"unknown status '{key}'. Known: at-base. {_REFRESH_HINT}.")
    return resolve(*codes)


def resolve_selection(key: str | None, cfg: dict[str, str] | None = None) -> Status:
    """The CLI resolution order: explicit --status KEY > env codes > DEFAULT.

    `cfg` carries DOCH1_MAIN_CODE / DOCH1_SECONDARY_CODE (loaded from .env /
    environment by api.load_config). An explicit `key` always wins.
    """
    if key:
        return by_key(key)
    cfg = cfg or {}
    main = (cfg.get("DOCH1_MAIN_CODE") or "").strip()
    secondary = (cfg.get("DOCH1_SECONDARY_CODE") or "").strip()
    if main and secondary:
        return resolve(main, secondary)
    return DEFAULT

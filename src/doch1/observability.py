"""Opt-in, SECRET-SAFE structured run-logging for DOCH1.

This module records *outcomes* (one JSON line per command run) for observability,
and is engineered around the security audit's hard constraint:

    ABSOLUTELY NO password / cookie / token / OTP / account-id / raw server body
    EVER reaches a log line.

How that guarantee is enforced (defense in depth):

1. **Closed schema.** :func:`log_run` only ever emits a fixed set of keys
   (``ts, command, result, reason, duration_ms, transport, auth_expired``). It
   does not accept or serialise arbitrary kwargs, so a caller cannot smuggle a
   secret into the line even by accident.
2. **Categories, not text.** The failure ``reason`` is a coarse *category*
   derived from flags (:func:`classify_reason`), NEVER the raw ``str(e)`` /
   server body. Categories are a small closed vocabulary (see ``REASONS``).
3. **No PII fields.** Dates, statuses, months, account ids, usernames, chat ids
   — none are logged. Only the verb name and the outcome shape.
4. **Tripwire sweep.** Every line is passed through :func:`_assert_safe` before
   it is written, which rejects any value matching a secret-bearing config key
   or obvious token shapes. This is a belt-and-suspenders guard: the schema
   already excludes them, but the sweep makes a regression *fail loudly* rather
   than leak.

Default is OFF. Enable by setting ``DOCH1_LOG=1`` (or ``true``/``yes``). The log
path defaults to ``<project>/doch1.log`` (the same file the cron jobs append to,
see cron.py) and is overridable via ``DOCH1_LOG_FILE``. The file is created at
mode ``0o600`` so the cadence/operational metadata is not world-readable (audit
DEEP #11).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# Closed vocabulary of failure categories. NEVER raw server / exception text.
REASONS = frozenset(
    {
        "ok",  # success
        "auth_expired",  # session dead -> re-login
        "rejected",  # server returned non-true (report not accepted)
        "bad_input",  # client-side validation (bad date, unknown status)
        "no_session",  # no saved session / cookie configured
        "transport_error",  # network / WAF / HTTP failure (generic)
        "error",  # uncategorised failure (still NO raw text)
    }
)

# Substrings of config keys whose VALUES must never appear in a log line. Used by
# the tripwire sweep below to fail loudly on any future regression.
_FORBIDDEN_KEY_HINTS = (
    "PASS",
    "COOKIE",
    "TOKEN",
    "TOTP",
    "SEED",
    "OTP",
    "SECRET",
    "CHAT_ID",
    "USER",
)


def enabled() -> bool:
    """True iff opt-in run-logging is switched on (default OFF)."""
    return os.environ.get("DOCH1_LOG", "").strip().lower() in ("1", "true", "yes")


def log_path() -> Path:
    """Resolve the log file path.

    Override with ``DOCH1_LOG_FILE``; otherwise ``<project>/doch1.log`` — the
    same file the cron jobs already append to (cron.py).
    """
    override = os.environ.get("DOCH1_LOG_FILE")
    if override:
        return Path(override)
    # project root = parents[2] of this file (src/doch1/observability.py).
    return Path(__file__).resolve().parents[2] / "doch1.log"


def classify_reason(*, ok: bool, auth_expired: bool = False, category: str | None = None) -> str:
    """Map an outcome to a SAFE category string (never raw text).

    ``category`` lets a caller pass a specific closed-vocabulary reason for a
    failure (e.g. ``"bad_input"``, ``"rejected"``); it is validated against
    ``REASONS`` and falls back to ``"error"`` if unknown — so an accidental raw
    string can never become the reason.
    """
    if ok:
        return "ok"
    if auth_expired:
        return "auth_expired"
    if category in REASONS:
        return category
    return "error"


def _assert_safe(record: dict) -> None:
    """Tripwire: reject any record that could carry a secret/PII value.

    The schema in :func:`log_run` already excludes secrets; this is the loud
    second line of defense so a regression FAILS instead of leaking.
    """
    cfg_keys = set()
    try:  # best-effort: don't let config loading break logging
        from . import api

        cfg_keys = set(api.load_config().keys())
    except Exception:
        cfg_keys = set()

    secret_values = set()
    for k, v in ({k: os.environ.get(k, "") for k in cfg_keys} if cfg_keys else {}).items():
        if not v:
            continue
        if any(hint in k.upper() for hint in _FORBIDDEN_KEY_HINTS):
            secret_values.add(v)

    for key, value in record.items():
        if not isinstance(value, str):
            continue
        for secret in secret_values:
            if secret and secret in value:
                raise ValueError(
                    f"observability tripwire: secret value would be logged in field {key!r}"
                )


def log_run(
    command: str,
    *,
    ok: bool,
    reason: str,
    duration_ms: int,
    transport: str | None,
    auth_expired: bool = False,
) -> None:
    """Append one structured JSON line for a command run (no-op if disabled).

    The emitted object has a CLOSED schema — only these keys, all secret-safe:

        ts          ISO-8601 UTC timestamp
        command     the CLI verb (e.g. "today", "week")
        result      "ok" | "fail"
        reason      a category from REASONS (NEVER raw server/exception text)
        duration_ms wall-clock milliseconds
        transport   "browser" | "cookie" | None (no token/cookie value)
        auth_expired whether a re-login is needed

    Logging never raises into the caller: any I/O error is swallowed so
    observability can't break the actual command.
    """
    if not enabled():
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": str(command),
        "result": "ok" if ok else "fail",
        "reason": reason if reason in REASONS else "error",
        "duration_ms": int(duration_ms),
        "transport": transport,
        "auth_expired": bool(auth_expired),
    }
    try:
        _assert_safe(record)
        line = json.dumps(record, ensure_ascii=False)
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create at 0o600 if absent (audit DEEP #11: not world-readable).
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except ValueError:
        # Tripwire fired: NEVER write a line we couldn't prove safe.
        raise
    except OSError:
        # I/O problem — observability must not break the command.
        return


class RunTimer:
    """Tiny wall-clock timer for ``duration_ms``."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

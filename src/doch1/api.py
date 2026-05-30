"""HTTP + data layer for the DOCH1 reporter.

Transport is pluggable via a *client* object exposing two methods:
    post_multipart(path, fields) -> str    (response body, stripped)
    post_json(path, body)        -> dict
Two clients implement it: RequestsClient (plain HTTP, fast but the Imperva WAF
expires its session quickly) and doch1.session.BrowserClient (drives a real
Chromium context, satisfies the WAF, auto-refreshes cookies). Action functions
below are transport-agnostic and take a client.

Endpoints (reverse-engineered from one.prat.idf.il):
  InsertPersonalReport  today's report           -> "true"
  InsertFutureReport    schedule a future day    -> "true"
  getFutureReport       list scheduled future    -> {days[], minDate, maxDate}
  memberHistory         past reports history     -> {days[], minDate, maxDate}
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from .statuses import DEFAULT as DEFAULT_STATUS
from .statuses import Status

BASE = "https://one.prat.idf.il"
P_TODAY = "/api/Attendance/InsertPersonalReport"
P_FUTURE = "/api/Attendance/InsertFutureReport"
P_FUTURE_LIST = "/api/Attendance/getFutureReport"
P_HISTORY = "/api/Attendance/memberHistory"
# Back-compat constants — kept equal to DEFAULT_STATUS so nothing else breaks.
MAIN_CODE = DEFAULT_STATUS.main
SECONDARY_CODE = DEFAULT_STATUS.secondary


class Doch1Error(RuntimeError):
    """Auth/transport/server failure. .auth_expired flags re-login needed."""

    def __init__(self, message: str, *, auth_expired: bool = False):
        super().__init__(message)
        self.auth_expired = auth_expired


# ---------- config ----------


def config_path() -> Path:
    override = os.environ.get("DOCH1_ENV")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".env"


def load_config(path: Path | None = None) -> dict[str, str]:
    path = path or config_path()
    cfg: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            cfg[key.strip()] = val.strip().strip("'\"")
    for k in (
        "DOCH1_COOKIE",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DOCH1_TOTP_SEED",
        "DOCH1_USER",
        "DOCH1_PASS",
        "DOCH1_MAIN_CODE",
        "DOCH1_SECONDARY_CODE",
    ):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


# ---------- requests transport (fallback) ----------


class RequestsClient:
    """Plain-HTTP client using a pasted cookie. Context-manager for parity with
    BrowserClient. Subject to WAF session expiry — prefer BrowserClient."""

    def __init__(self, cookie: str):
        self.cookie = cookie

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        h = {
            "Cookie": self.cookie,
            "Accept": "application/json, text/plain, */*",
            "Origin": BASE,
            "Referer": f"{BASE}/calendar",
            "User-Agent": "doch1-cli/2.0",
        }
        if json_body:
            h["Content-Type"] = "application/json;charset=UTF-8"
        return h

    @staticmethod
    def _guard(status: int, ctype: str, text: str) -> None:
        if status in (401, 403):
            raise Doch1Error(f"Auth expired (HTTP {status})", auth_expired=True)
        if "text/html" in ctype:
            raise Doch1Error("Got HTML not JSON — login/WAF wall", auth_expired=True)
        if not (200 <= status < 300):
            # Do NOT embed the raw server body in the exception — it may be
            # forwarded to Telegram. Keep the generic status only.
            raise Doch1Error(f"HTTP {status} error")

    def post_multipart(self, path: str, fields: dict[str, str]) -> str:
        files = {k: (None, v) for k, v in fields.items()}
        try:
            r = requests.post(BASE + path, headers=self._headers(), files=files, timeout=30)
        except requests.RequestException as exc:
            raise Doch1Error(f"Request error: {exc}") from exc
        self._guard(r.status_code, r.headers.get("Content-Type", ""), r.text)
        return r.text.strip().strip('"')

    def post_json(self, path: str, body: dict) -> dict:
        try:
            r = requests.post(BASE + path, headers=self._headers(True), json=body, timeout=30)
        except requests.RequestException as exc:
            raise Doch1Error(f"Request error: {exc}") from exc
        self._guard(r.status_code, r.headers.get("Content-Type", ""), r.text)
        try:
            return r.json()
        except ValueError as exc:
            raise Doch1Error("Bad JSON response") from exc


# ---------- actions (transport-agnostic) ----------


def report_today(client, status: Status = DEFAULT_STATUS) -> bool:
    return (
        client.post_multipart(P_TODAY, {"MainCode": status.main, "SecondaryCode": status.secondary})
        == "true"
    )


def report_future(client, d: date, note: str = "", status: Status = DEFAULT_STATUS) -> bool:
    return (
        client.post_multipart(
            P_FUTURE,
            {
                "MainCode": status.main,
                "SecondaryCode": status.secondary,
                "Note": note,
                "FutureReportDate": d.strftime("%d.%m.%Y"),
            },
        )
        == "true"
    )


def list_scheduled(client, month: int, year: int) -> dict:
    return client.post_json(P_FUTURE_LIST, {"month": month, "year": year})


def scheduled_window(client, months: set[tuple[int, int]]):
    """(set of already-scheduled dates, minDate, maxDate) across months."""
    filled: set[date] = set()
    min_d = max_d = None
    for month, year in months:
        try:
            data = list_scheduled(client, month, year)
        except Doch1Error as exc:
            if exc.auth_expired:
                raise  # don't mask a dead session as "no scheduled days"
            continue
        for day in data.get("days", []):
            d_parsed = _parse_server_date(day.get("date"))
            if d_parsed is None:
                continue
            filled.add(d_parsed)
        md = _parse_server_date(data.get("minDate"))
        if md is not None:
            min_d = md if min_d is None else min(min_d, md)
        xd = _parse_server_date(data.get("maxDate"))
        if xd is not None:
            max_d = xd if max_d is None else max(max_d, xd)
    return filled, min_d, max_d


@dataclass
class HistoryDay:
    date: date
    reported: str
    determined: str
    approved: str
    in_base: bool
    conflict: bool
    note: str

    @property
    def effective(self) -> str:
        """Best-known truth: approved > determined > reported."""
        return self.approved or self.determined or self.reported


def member_history(client, month: int, year: int) -> list[HistoryDay]:
    data = client.post_json(P_HISTORY, {"month": month, "year": year})
    out: list[HistoryDay] = []
    for d in data.get("days", []):
        d_parsed = _parse_server_date(d.get("date"))
        if d_parsed is None:
            continue
        out.append(
            HistoryDay(
                date=d_parsed,
                reported=_join(d.get("reportedMainName"), d.get("secondaryStatusReported")),
                determined=_join(
                    d.get("mainStatusDeterminedName"), d.get("secondaryStatusDeterminedName")
                ),
                approved=_join(d.get("approvedMainName"), d.get("approvedSecondaryName")),
                in_base=bool(d.get("inBase")),
                conflict=bool(d.get("conflict")),
                note=(d.get("note") or "").strip(),
            )
        )
    return out


def _join(main: str | None, secondary: str | None) -> str:
    parts = [p.strip() for p in (main, secondary) if p and p.strip()]
    return " / ".join(parts)


def _parse_server_date(value) -> date | None:
    """Parse an ISO date/datetime string from UNTRUSTED server JSON.

    Returns None on anything malformed/missing/garbage instead of letting an
    unhandled ValueError/TypeError crash the app (DoS-hardening). Callers skip
    the record or treat the field as absent.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except (ValueError, TypeError):
        return None

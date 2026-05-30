"""Shared test fixtures (W5/W6).

A FakeClient implementing the transport contract used by api.* actions:
    post_multipart(path, fields) -> str
    post_json(path, body)        -> dict
plus failure injection (401/403 -> auth_expired; HTML body -> WAF wall; 429).
Mirrors the FakePage/FakeBox philosophy in test_login_flow.py — no network,
no browser. Lets us assert the SELECTED MainCode/SecondaryCode actually reach
the wire.
"""

from __future__ import annotations

import pytest

from doch1.api import Doch1Error


@pytest.fixture(autouse=True)
def _hermetic_guard(tmp_path, monkeypatch):
    """Hermetic CI guard (W6).

    1. Point DOCH1_STATE / DOCH1_ENV at throwaway tmp paths so no test reads
       the maintainer's real auth.json / .env.
    2. Monkeypatch requests.post / requests.get to RAISE, so any test that
       accidentally touches the network (RequestsClient path, cli._alert
       Telegram POST) fails LOUDLY instead of hanging or hitting a live
       server. Tests that need a transport use FakeClient / FakePage doubles.
    """
    monkeypatch.setenv("DOCH1_STATE", str(tmp_path / "auth.json"))
    monkeypatch.setenv("DOCH1_ENV", str(tmp_path / ".env"))

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "Network access is blocked in tests (hermetic CI guard). "
            "Use FakeClient / FakePage doubles instead of real requests."
        )

    import requests

    monkeypatch.setattr(requests, "post", _blocked)
    monkeypatch.setattr(requests, "get", _blocked)


class FakeClient:
    """In-memory transport double.

    Records every post_multipart call as (path, fields) in ``.multipart_calls``
    and every post_json call as (path, body) in ``.json_calls``.

    Failure injection:
      * ``fail_status`` 401/403 -> Doch1Error(auth_expired=True)
      * ``fail_status`` 429     -> Doch1Error("WAF 429")
      * ``html_wall=True``      -> Doch1Error("Got HTML not JSON", auth_expired=True)
    ``multipart_reply`` is what post_multipart returns ("true" => report accepted).
    ``json_reply`` is what post_json returns.
    """

    def __init__(
        self,
        *,
        multipart_reply: str = "true",
        json_reply: dict | None = None,
        fail_status: int | None = None,
        html_wall: bool = False,
    ):
        self.multipart_reply = multipart_reply
        self.json_reply = json_reply if json_reply is not None else {}
        self.fail_status = fail_status
        self.html_wall = html_wall
        self.multipart_calls: list[tuple[str, dict]] = []
        self.json_calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _maybe_fail(self) -> None:
        if self.html_wall:
            raise Doch1Error("Got HTML not JSON — login/WAF wall", auth_expired=True)
        if self.fail_status in (401, 403):
            raise Doch1Error(f"Auth expired (HTTP {self.fail_status})", auth_expired=True)
        if self.fail_status == 429:
            raise Doch1Error("HTTP 429: WAF rate limit")
        if self.fail_status is not None:
            raise Doch1Error(f"HTTP {self.fail_status}")

    def post_multipart(self, path: str, fields: dict) -> str:
        self.multipart_calls.append((path, dict(fields)))
        self._maybe_fail()
        return self.multipart_reply

    def post_json(self, path: str, body: dict) -> dict:
        self.json_calls.append((path, dict(body)))
        self._maybe_fail()
        return self.json_reply


@pytest.fixture
def fake_client():
    return FakeClient()

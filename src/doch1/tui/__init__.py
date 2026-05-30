"""Interactive Textual UI for doch1. Lazy-imported only on the UI launch path."""

from __future__ import annotations


def run_app() -> None:
    from .app import run_app as _run

    _run()

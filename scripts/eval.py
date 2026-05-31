#!/usr/bin/env python3
"""Feature-eval RUNNER — runs the eval suite and emits a pass/fail-per-feature report.

This is the regression safety net's entry point, usable by CI and by an agent:

    uv run --with pytest --with pytest-asyncio python scripts/eval.py
    # writes eval_report.json (and prints a summary); exit 0 iff every feature passed.

What it does:
  1. Runs tests/eval/test_features.py (the `eval` marker) via an in-process pytest
     with a tiny result-collecting plugin — no network/browser (hermetic).
  2. Maps each test node to the manifest feature id(s) it exercises (by the
     feature-id token appearing in the test name / parametrize id).
  3. Writes eval_report.json: per-feature pass/fail, the covering tests, and a
     totals block. Any feature with a failing test (or no covering test) is fail.

Exit code: 0 if all manifest features pass and are covered; 1 otherwise — so CI
and an agent loop can branch on the return code alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "eval"))

import pytest  # noqa: E402
from feature_manifest import (  # noqa: E402
    ALL_FEATURE_IDS,
    CLI_FEATURES,
    TUI_JOURNEYS,
    TUI_SCREENS,
)

EVAL_FILE = ROOT / "tests" / "eval" / "test_features.py"
REPORT = ROOT / "eval_report.json"


class _Collector:
    """Pytest plugin: record (nodeid -> outcome) for every test call phase."""

    def __init__(self) -> None:
        self.results: dict[str, str] = {}

    def pytest_runtest_logreport(self, report) -> None:
        # Count the 'call' phase (or a setup/collection failure) as the verdict.
        if report.when == "call" or (report.when == "setup" and report.outcome == "failed"):
            prev = self.results.get(report.nodeid)
            # failed/error wins over a prior pass; don't downgrade a failure.
            if prev != "failed":
                self.results[report.nodeid] = report.outcome


# Map every feature id to the tokens that, if present in a test nodeid, mean the
# test covers that feature. Screen ids and journey ids appear verbatim (via
# parametrize ids / test names); CLI ids appear as a word token in the name.
def _feature_tokens() -> dict[str, tuple[str, ...]]:
    tokens: dict[str, tuple[str, ...]] = {}
    for f in CLI_FEATURES:
        # e.g. "today" -> test_cli_..._today..., [today]
        tokens[f.id] = (f.id,)
    for s in TUI_SCREENS:
        # screen ids show up via the parametrize id "[screen-today]" and as the
        # journey tokens; also accept the trailing word ("today"/"thisweek"...).
        tail = s.screen_id.removeprefix("screen-")
        tokens[s.screen_id] = (s.screen_id, tail)
    for j in TUI_JOURNEYS:
        # journey id like "week-fill-confirm" -> nodeid contains its
        # underscored form "week_fill_confirm".
        tokens[j.id] = (j.id, j.id.replace("-", "_"))
    return tokens


def _covers(nodeid: str, toks: tuple[str, ...]) -> bool:
    """A test covers a feature if one of its tokens appears as a WHOLE word in
    the test name. Word boundaries avoid false hits like 'tui' matching 'ui' or
    'today-report' matching the 'today' CLI verb's substring."""
    import re

    name = nodeid.split("::", 1)[-1].lower()
    for t in toks:
        tok = re.escape(t.replace("-", "_").lower())
        if re.search(rf"(?<![a-z0-9]){tok}(?![a-z0-9])", name):
            return True
    return False


def main() -> int:
    collector = _Collector()
    # -p no:cacheprovider keeps the run side-effect-free for CI.
    code = pytest.main(
        ["-q", "-p", "no:cacheprovider", str(EVAL_FILE)],
        plugins=[collector],
    )

    tokens = _feature_tokens()
    features: dict[str, dict] = {}
    for fid in ALL_FEATURE_IDS:
        toks = tokens.get(fid, (fid,))
        covering = {
            nid: outcome for nid, outcome in collector.results.items() if _covers(nid, toks)
        }
        if not covering:
            verdict = "uncovered"
        elif all(o == "passed" for o in covering.values()):
            verdict = "pass"
        else:
            verdict = "fail"
        features[fid] = {
            "status": verdict,
            "tests": sorted(covering),
            "passed": sum(1 for o in covering.values() if o == "passed"),
            "failed": sum(1 for o in covering.values() if o != "passed"),
        }

    n_pass = sum(1 for f in features.values() if f["status"] == "pass")
    n_fail = sum(1 for f in features.values() if f["status"] == "fail")
    n_uncovered = sum(1 for f in features.values() if f["status"] == "uncovered")
    report = {
        "ok": code == 0 and n_fail == 0 and n_uncovered == 0,
        "pytest_exit_code": int(code),
        "totals": {
            "features": len(features),
            "pass": n_pass,
            "fail": n_fail,
            "uncovered": n_uncovered,
            "tests_run": len(collector.results),
        },
        "features": features,
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\neval report -> {REPORT}")
    print(
        f"features: {n_pass} pass / {n_fail} fail / {n_uncovered} uncovered "
        f"(of {len(features)}); ok={report['ok']}"
    )
    for fid, info in features.items():
        if info["status"] != "pass":
            print(f"  {info['status'].upper():9} {fid}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

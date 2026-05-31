"""Pure cron-line generation / merge / remove / status for DOCH1 auto-fill.

This module is deliberately I/O-free: every function takes the *existing*
crontab text (a string) and returns new text or a structured dict. The thin
``crontab -l`` / ``crontab -`` subprocess shim lives in cli.py, not here, so the
whole core is unit-testable without ever touching a real user crontab.

Tagged-cron approach (mirrors install.sh): each managed job is a pair of lines:

    # doch1-auto-report          <- the tag
    30 7 * * * cd ... && ...     <- the command

``merge`` REPLACES the command line under each tag (fixing the install.sh
stale-line bug where an existing tag was never updated), and is idempotent.
Every generated command line carries DOCH1_NONINTERACTIVE=1 (and DOCH1_CRON=1)
so cron can never launch the interactive TUI — belt and suspenders, especially
for the weekly ``doch1 week`` line which is not ``python -m doch1.main``.
"""

from __future__ import annotations

import re
import shlex

# Keep the daily tag identical to install.sh for continuity with any crontab
# that the old installer already wrote.
TAG_DAILY = "# doch1-auto-report"
TAG_WEEKLY = "# doch1-auto-week"

DEFAULT_DAILY = "30 7 * * *"
DEFAULT_WEEKLY = "40 7 * * 0"  # Sunday 07:40 — see dates.default_week_anchor

# A cron schedule is 5 whitespace-separated fields, each built only from
# digits and the cron metacharacters ``* , / -``. This rejects newlines (the
# crontab line-injection vector), shell metacharacters, and any command text.
_CRON_FIELD = r"[0-9*]+(?:[-,/][0-9*]+)*"
_CRON_SCHEDULE_RE = re.compile(r"^\s*" + r"\s+".join([_CRON_FIELD] * 5) + r"\s*$")


def _validate_schedule(expr: str, *, field: str) -> str:
    """Return a normalized cron schedule or raise ValueError.

    SECURITY: ``--daily``/``--weekly`` flow straight into a crontab line. A value
    like ``$'30 7 * * *\\n* * * * * /bin/sh -i'`` would otherwise inject an extra
    crontab line -> arbitrary command execution. We reject anything that is not
    exactly five clean cron fields (no newlines, no shell metacharacters).
    """
    if not isinstance(expr, str) or not _CRON_SCHEDULE_RE.match(expr):
        raise ValueError(
            f"Invalid cron schedule for {field}: {expr!r}. "
            "Expected five fields of digits and * , / - only."
        )
    return " ".join(expr.split())


def _command(proj_dir: str, py: str, *, env: dict[str, str], run: str) -> str:
    """Build the shell command (without the leading cron schedule).

    Exports the non-interactive guards (plus any caller env) then cd's into the
    project, runs the module invocation, and appends to the log.

    SECURITY: ``proj_dir``, ``py``, the log path and every env value are
    ``shlex.quote``-d so a path containing spaces or shell metacharacters (e.g.
    ``/opt/my doch1/``) cannot word-split or inject commands into the cron line.
    """
    exports = {"DOCH1_NONINTERACTIVE": "1", "DOCH1_CRON": "1"}
    exports.update(env or {})
    env_str = " ".join(f"{shlex.quote(k)}={shlex.quote(v)}" for k, v in exports.items())
    q_proj = shlex.quote(proj_dir)
    q_py = shlex.quote(py)
    q_log = shlex.quote(f"{proj_dir}/doch1.log")
    return f"cd {q_proj} && {env_str} {q_py} {run} >> {q_log} 2>&1"


def build_lines(
    proj_dir: str,
    py: str,
    *,
    daily: str = DEFAULT_DAILY,
    weekly: str = DEFAULT_WEEKLY,
    env: dict[str, str] | None = None,
    with_weekly: bool = True,
) -> list[tuple[str, str]]:
    """Return ``[(tag, full_crontab_line), ...]`` for the managed jobs.

    - The daily line runs ``<py> -m doch1.main`` (== the ``today`` command).
    - The weekly line runs ``<py> -m doch1.main week --json`` (non-interactive).
    Both lines carry the DOCH1_NONINTERACTIVE / DOCH1_CRON guards.
    """
    env = env or {}
    daily = _validate_schedule(daily, field="--daily")
    weekly = _validate_schedule(weekly, field="--weekly")
    lines: list[tuple[str, str]] = []
    daily_cmd = _command(proj_dir, py, env=env, run="-m doch1.main")
    lines.append((TAG_DAILY, f"{daily} {daily_cmd}"))
    if with_weekly:
        weekly_cmd = _command(proj_dir, py, env=env, run="-m doch1.main week --json")
        lines.append((TAG_WEEKLY, f"{weekly} {weekly_cmd}"))
    return lines


def _split(text: str) -> list[str]:
    return text.splitlines() if text else []


def _strip_tags(lines: list[str], tags: set[str]) -> list[str]:
    """Drop every managed tag line and the single command line that follows it."""
    out: list[str] = []
    skip_next = False
    for ln in lines:
        if skip_next:
            skip_next = False
            continue
        if ln.strip() in tags:
            skip_next = True  # also drop the command line under this tag
            continue
        out.append(ln)
    return out


def merge(existing_crontab: str, lines: list[tuple[str, str]]) -> str:
    """Idempotently insert/replace the managed tagged lines.

    Any pre-existing block for a managed tag is removed first (so a stale command
    line is REPLACED, never duplicated), then the fresh blocks are appended.
    Unrelated crontab lines are preserved in order.
    """
    tags = {tag for tag, _ in lines}
    kept = _strip_tags(_split(existing_crontab), tags)
    # Drop trailing blank lines from the kept region for a clean append.
    while kept and not kept[-1].strip():
        kept.pop()
    block: list[str] = []
    for tag, line in lines:
        block.append(tag)
        block.append(line)
    result = kept + block
    return "\n".join(result) + "\n"


def remove(existing_crontab: str, tags) -> str:
    """Strip the given managed tags and their command lines; keep everything else."""
    tagset = {t.strip() for t in tags}
    kept = _strip_tags(_split(existing_crontab), tagset)
    if not any(ln.strip() for ln in kept):
        return ""
    return "\n".join(kept) + "\n"


def status(existing_crontab: str) -> dict:
    """Report which managed tags are present and their resolved command lines."""
    lines = _split(existing_crontab)
    result: dict[str, dict] = {
        TAG_DAILY: {"present": False, "line": None},
        TAG_WEEKLY: {"present": False, "line": None},
    }
    for i, ln in enumerate(lines):
        tag = ln.strip()
        if tag in result:
            cmd = lines[i + 1] if i + 1 < len(lines) else None
            result[tag] = {"present": True, "line": cmd}
    return result

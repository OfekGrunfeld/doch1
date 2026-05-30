#!/usr/bin/env bash
# DOCH1 installer — sets up the uv project and a daily cron job. Idempotent.
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ_DIR"

echo "==> Project: $PROJ_DIR"

# 1. uv present?
if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv not found; installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "==> uv $(uv --version)"

# 2. Sync deps + create venv (locked, reproducible).
echo "==> uv sync"
uv sync

# 2b. Playwright Chromium (browser-session transport passes the Imperva WAF).
echo "==> installing Chromium for Playwright"
uv run playwright install chromium

# 3. .env scaffold.
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "==> Created .env (chmod 600). EDIT IT: add DOCH1_COOKIE + optional Telegram creds."
else
  echo "==> .env already exists; leaving untouched."
fi

# 4. Install cron jobs (daily 07:30 today + weekly Sun 07:40 week fill).
#    Single source of truth: `doch1 cron install` builds/merges the crontab,
#    carries the DOCH1_NONINTERACTIVE guard, and idempotently REPLACES stale
#    tagged lines (fixes the old skip-if-tag-present staleness).
if [[ "${1:-}" == "--no-cron" ]]; then
  echo "==> --no-cron: skipping cron install. Install manually later with:"
  echo "    uv run doch1 cron install"
else
  echo "==> Installing cron jobs via 'doch1 cron install'"
  uv run doch1 cron install
fi

echo
echo "Done. Next:"
echo "  1. Edit $PROJ_DIR/.env  (DOCH1_COOKIE = full Cookie header from browser)"
echo "  2. Log in once:        uv run doch1 login"
echo "  3. Interactive UI:     uv run doch1        (in a terminal; 'doch1 ui' forces it)"
echo "  4. Cron / scripted:    uv run doch1 today  (or: $PY -m doch1.main)"
echo "  5. Logs:               $PROJ_DIR/doch1.log"

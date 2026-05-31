#!/usr/bin/env bash
# DOCH1 installer — sets up the uv project and a daily cron job. Idempotent.
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ_DIR"

echo "==> Project: $PROJ_DIR"

# Pin the uv installer to a known version (matches CI / release workflows) so a
# compromised/MITM'd astral.sh CDN can't silently ship a different uv. Override
# with UV_INSTALL_VERSION=... only if you know what you are doing.
UV_INSTALL_VERSION="${UV_INSTALL_VERSION:-0.9.21}"

# 1. uv present?
if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv not found; installing pinned version ${UV_INSTALL_VERSION}..."
  # SECURITY: download the installer to a temp file FIRST (no blind curl|sh),
  # so a human/CI can inspect or hash-pin it, then run the pinned version.
  _uv_installer="$(mktemp)"
  curl -LsSf "https://astral.sh/uv/${UV_INSTALL_VERSION}/install.sh" -o "$_uv_installer"
  sh "$_uv_installer"
  rm -f "$_uv_installer"
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "==> uv $(uv --version)"

# 2. Sync deps + create venv (locked, reproducible).
#    --frozen: never re-resolve; fail if uv.lock drifted (preserves hash pinning).
echo "==> uv sync --frozen"
uv sync --frozen

# 2b. Playwright Chromium (browser-session transport passes the Imperva WAF).
echo "==> installing Chromium for Playwright"
uv run playwright install chromium
# Tighten perms on the downloaded browser binaries (default 0755 is world-readable).
chmod -R go-rwx "$HOME/.cache/ms-playwright" 2>/dev/null || true

# 3. .env scaffold.
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "==> Created .env (chmod 600). EDIT IT: add DOCH1_COOKIE + optional Telegram creds."
else
  echo "==> .env already exists; leaving untouched."
fi

# 4. Cron jobs are OPT-IN (daily 07:30 today + weekly Sun 07:40 week fill).
#    SECURITY/ETHICS: the daily job files "present at base" on days the human
#    has not confirmed (see LEGAL.md). It must therefore be an explicit choice,
#    not a silent default. Pass --with-cron to install; otherwise we only PRINT
#    the lines so you can review before opting in.
if [[ "${1:-}" == "--with-cron" ]]; then
  echo "==> --with-cron: installing cron jobs via 'doch1 cron install'"
  uv run doch1 cron install
else
  echo "==> Cron NOT installed (opt-in). The daily job reports presence"
  echo "    automatically — review LEGAL.md, then enable with EITHER:"
  echo "        ./install.sh --with-cron"
  echo "        uv run doch1 cron install"
fi

echo
echo "Done. Next:"
echo "  1. Edit $PROJ_DIR/.env  (DOCH1_COOKIE = full Cookie header from browser)"
echo "  2. Log in once:        uv run doch1 login"
echo "  3. Interactive UI:     uv run doch1        (in a terminal; 'doch1 ui' forces it)"
echo "  4. Cron / scripted:    uv run doch1 today  (or: $PY -m doch1.main)"
echo "  5. Logs:               $PROJ_DIR/doch1.log"

# DOCH1 auto-reporter

A personal, set-and-forget IDF presence reporter (דו"ח 1) for `one.prat.idf.il`.
The daily report lands automatically every workday; the only human touch is a
painless SMS login once every few weeks. Built on a real browser session so the
Imperva WAF and Microsoft Entra login are handled for you.

> ⚠️ **Legal & ethical notice — read before use.**
> **Do not use doch1 to falsify presence.** A דו"ח 1 / DOCH1 is an official military declaration; submitting a false one can constitute fraud and may expose you to disciplinary action, criminal prosecution, and imprisonment. Use it only on days you are genuinely present. doch1 is **unofficial** and **not affiliated with or endorsed by** the IDF, the Israeli Ministry of Defense, Microsoft, or the operators of `one.prat.idf.il`. Provided **"AS IS"**, with no warranty, and automating the site may violate its Terms of Use. You are solely responsible for the truth of every report and for your own credentials. This is not legal advice.
> See [`./LEGAL.md`](./LEGAL.md) for the full terms — by using doch1 you accept them.

It is also a clean, exit-code-stable backend: every command takes `--json` and
returns stable exit codes (`0` ok, `1` fail) so an agent can drive it.

Reference behavior: <https://github.com/y-golde/doch1>

---

## 🤖 AI-NATIVE — BUILT FOR AGENTS, NOT JUST HUMANS

**doch1 is a machine-first tool.** It is designed from the ground up to be driven
by AI agents — the **Hermes agent** and **Claude Code** — through its
**NON-INTERACTIVE CLI**. The pretty Textual TUI is for humans; the clean JSON is
for machines, and the two never drift.

- **Every command speaks `--json`** — machine-readable output on every verb, no
  scraping a human UI.
- **STABLE EXIT CODES** — `0` = ok, `1` = fail, on every command, so an agent can
  branch on the return code without parsing text.
- **`auth_expired` flag** — when the session dies, the JSON error body carries an
  explicit `auth_expired` flag so an agent knows *re-login*, not *retry*.
- **HEAD-LESS under cron** — bare `doch1` auto-detects non-TTY / cron / CI and
  runs `today` with no UI, no prompts, no hang. The same binary serves humans and
  daemons.

**Concrete agent-style usage:**

```bash
doch1 status --json              # is the saved session still valid? (check before acting)
doch1 today --json               # report TODAY at base (01/01); exit 0 ok / 1 fail
doch1 week --json                # fill the rest of this Sun–Sat week
doch1 history 5 2026 --json      # machine-readable May 2026 history (raw Hebrew preserved)
```

**Wiring it into an agent loop / Claude Code:** poll `doch1 status --json`; on a
non-zero exit with `"auth_expired": true`, surface a re-login to the human (or
fire the Telegram alert); otherwise call `doch1 today --json` / `doch1 week
--json` and branch purely on the exit code and JSON body — no screen-scraping
required.

---

## Install

```bash
cd ~/doch1
./install.sh          # uv + deps, Chromium for Playwright, .env scaffold, cron
```

`install.sh` runs `uv sync`, installs Chromium (`uv run playwright install
chromium`), scaffolds `.env` (chmod 600), and installs the cron jobs via
`doch1 cron install`. Pass `--no-cron` to skip cron and just print the command to
run it later.

Manual equivalent:

```bash
uv sync
uv run playwright install chromium
cp .env.example .env && chmod 600 .env   # then edit it
```

The CLI is exposed as `doch1` (via `uv run doch1 …`, or
`uv tool install --editable .` for a global `doch1`).

---

## Configure

Settings live in `.env` (`KEY=VALUE`). Real environment variables override file
values (handy for secrets managers). Override the file path with
`DOCH1_ENV=/path/to/env`. See `.env.example` for the full template.

| Key | Purpose |
|---|---|
| `DOCH1_USER` | Microsoft `@idf.il` email — drives email pre-fill at login. Optional. |
| `DOCH1_PASS` | Account password — enables password pre-fill / headless auto-login. Optional, never stored. |
| `DOCH1_COOKIE` | Fallback transport: a full `Cookie:` header pasted from the browser Network tab. Fragile (the WAF kills it fast) — `doch1 login` is strongly preferred. |
| `DOCH1_TOTP_SEED` | Base32 TOTP seed for fully-unattended re-login. The IDF tenant blocks this today — see [Fully unattended](#fully-unattended-not-currently-possible). Leave blank. |
| `DOCH1_MAIN_CODE` / `DOCH1_SECONDARY_CODE` | Default status code pair to report (defaults to `01` / `01` = at base). See [Status selection](#status-selection). |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Optional Telegram failure alerts. Omit both to disable. |

Keep `.env` private: `chmod 600 .env`.

---

## Login (the blessed path)

`one.prat.idf.il` delegates login to **Microsoft Entra ID** and sits behind the
**Imperva WAF**, which blocks plain HTTP clients and expires cookies within
minutes. So `doch1 login` drives a real Chromium and saves the resulting
Playwright session (`storageState`) to `~/.config/doch1/auth.json`. Entra
sessions last weeks; every other command reuses that session and auto-refreshes
the cookies on each run.

```bash
uv run doch1 login            # assisted headed popup (recommended)
DOCH1_HEADFUL=1 uv run doch1 login --manual   # force the assisted headed flow
```

### Two modes

- **Assisted / `--manual` (recommended).** A **visible Chromium window** opens
  and you finish the login by hand (MFA + "Stay signed in"). If `DOCH1_USER` is
  set the email is pre-filled and advanced; if `DOCH1_PASS` is *also* set the
  password is pre-filled too (**dual autofill**). The fill order is guarded so a
  missed email box can never cause the password to be typed into the wrong
  field, and no password is ever stored. This is the blessed default.
- **Auto (headless).** With `DOCH1_USER` + `DOCH1_PASS` set and **not**
  `--manual`, the email→password flow runs headless up to the SMS step.

### SMS-only MFA requires a headed window

The IDF tenant is **SMS-only**, and **headless Chromium can never complete the
SMS step** — the one-time-code screen needs a real browser. The login flow
detects the headless + SMS-required mismatch and **fails fast** with a clear
message telling you to re-run with `--manual` (or `DOCH1_HEADFUL=1`) instead of
silently hanging at the OTC timeout.

### The SMS code: terminal vs. browser race

When you reach the SMS step you have **two ways to enter the 6-digit code**, and
either one wins — whichever you do first:

- **Type it in the browser window** (just like the real site), or
- **Type it in this terminal** at the prompt.

Internally these race: a non-blocking terminal reader polls stdin while the flow
also polls whether you've already authenticated in the browser. As soon as
either path succeeds, the other is cancelled cleanly — the terminal reader never
blocks process exit, and on a non-TTY (cron) it degrades immediately to "no
terminal code source" instead of hanging. If you do nothing past the timeout you
get a clean `Auth expired` error, not a wedged process.

### Re-login

Run `doch1 login` again whenever a command reports **`Auth expired`** (every few
weeks). `doch1 status` tells you if the session is still valid; commands also
fire a Telegram alert on `Auth expired` if configured.

### Headless servers

`doch1 login` needs a desktop (it shows a browser). On a headless server, run it
on your own machine and copy `~/.config/doch1/auth.json` over, or run it under
`xvfb` (e.g. `xvfb-run -a uv run doch1 login`). A hidden maintainer harness,
`doch1 login --probe-sms`, pops a headed browser and proves the SMS box is
reached without doing a real login.

---

## Commands

```
doch1                      TTY: launch the interactive UI; cron/pipe: report TODAY
doch1 ui                   force the interactive terminal UI (needs a real terminal)
doch1 login [--manual]     browser login; saves the session (see Login above)
doch1 status [--json]      is the saved session still valid?
doch1 today  [--json] [--status KEY]      report TODAY (default: at base 01/01)
doch1 day DD.MM.YYYY [--json] [--status KEY]   schedule a single future day
doch1 week [date] [--json] [--status KEY]      fill the Sun-Sat week containing date
doch1 history [m] [y] [--json] [--conflicts]   view past reports for a month
doch1 statuses [--json]    list the selectable report statuses
doch1 cron install|status|remove   manage the auto-fill cron jobs (see Automation)
doch1 --help               full introspectable help (per-command --help too)
```

- **`today`** reports today as present at base (`01/01`) via
  `InsertPersonalReport`. This is what cron runs.
- **`day`** schedules one day (`DD.MM.YYYY` or `YYYY-MM-DD`) via
  `InsertFutureReport`. Today is reported as today; past days are unreportable.
- **`week`** reports today + each remaining future day in the Sun–Sat week,
  **skipping** days already scheduled and days outside the server's allowed
  window. Past days are skipped (the app blocks them).
- **`history`** shows, per day, the **effective** status (approved > determined >
  reported), whether you were in base, and a **CONFLICT** flag when what you
  reported differs from what the commander approved. `--conflicts` filters to
  those. Status values are shown in English (`At base / Present`, `Sick leave`,
  …) with the raw Hebrew preserved in `--json`.

Every command takes `--json` for machine consumption and returns stable exit
codes (`0` ok, `1` fail), with an `auth_expired` flag in the JSON error body when
the session has died — built for an agent to drive.

### Status selection

Every reporting command defaults to **at base / present (`01/01`)**. Override it
with `--status KEY` (e.g. `--status at-base`) or the `DOCH1_MAIN_CODE` /
`DOCH1_SECONDARY_CODE` env defaults. Resolution order: `--status` > env codes >
default.

`doch1 statuses` lists the selectable statuses. **Today only the at-base default
is known offline.** The holiday / leave / sick / off-base / abroad code pairs are
**not** in the codebase — they must be captured from a live, authenticated
session by observing the site's status-picker network requests. `doch1 statuses
--refresh` is the placeholder for that maintainer discovery ritual and is **not
yet wired**; until it lands, any non-at-base `--status` key is rejected with that
hint.

---

## Interactive mode (TUI)

Run bare **`doch1`** in a real terminal and it launches a
[Textual](https://textual.textualize.io/) terminal UI; `doch1 ui` forces it
regardless of context. **The TUI is fully keyboard-operable** — every action has
a key binding and nothing requires a mouse. Screens are reachable by number keys
or the left nav rail.

```
1 Today       today's effective status; r report "at base", enter refresh
2 This week    Sun-Sat table for the current week; f fill, enter refresh
3 Next week    Sun-Sat table for next week; f fill, enter refresh
4 Calendar     month grid (Sun-Sat, one row per week); arrow keys move the day
               cursor, enter opens day detail; < / > (or , / . or PgUp/PgDn)
               change month; an arrow key past the grid edge rolls into the
               adjacent month; t jumps to today's month, c conflicts-only
5 Status       auth probe + transport; l auto-(re)login, m manual login — both
               suspend the UI to show the browser and take the SMS code
6 / q          Quit          ?  Help overlay
```

### Keymap

```
Global (any screen)
  1 2 3 4 5    switch screen (Today / This week / Next week / Calendar / Status)
  6  q  Ctrl+C quit
  esc          focus the left nav rail (menu)
  tab          cycle focus (rail -> screen widget -> ...)
  ?            help overlay

Nav rail (after esc)
  ↑ / ↓        browse entries (highlight only)    enter  open the highlighted screen

Today (1)            r report "at base"        enter refresh
This / Next week     f fill the week (y/n confirm modal)   enter refresh
Calendar (4)
  ← → ↑ ↓      move the day cursor (rolls into the adjacent month past an edge)
  Home / End   first / last column of the week
  enter        open the day-detail modal        < / >  prev / next month
  t            jump to today's month             c  toggle conflicts-only
  Day detail modal:  r request report   esc / enter / q  close
Status (5)
  l            auto login (uses DOCH1_PASS if set; headless until SMS)
  m            manual / assisted login (always headed; pre-fills email/password)
  enter        re-probe auth + transport
  Both l and m suspend the TUI (drop the alt-screen) so the real browser window
  and SMS prompt are visible; the UI restores and re-probes on return.

Confirm modal   y / enter confirm     n / esc cancel
Help overlay    ? / esc / q close
```

The calendar grid is always a full 6×7 rectangle: leading/trailing cells from the
**adjacent month** are shown dimmed (legended `▒ other month`) with their real
status glyphs — highlighting one rolls the month over.

### The Saturday rule

Weeks are Sun–Sat (the Israeli work week). On **Saturday** the current IDF week
has already ended, so both the TUI default and `doch1 week` anchor to the
**following** week (`dates.py::default_week_anchor`). Sun–Fri they anchor to the
current week.

The TUI shares the exact **rich** renderings used by the plain commands
(SIMPLE_HEAVY box, colored status badges, `→` for `reported → approved` conflict
transitions, `⚠` conflict flag, `✓`/`·` base markers) — interactive and
non-interactive output never drift, both via `tui/data.py::fill_week_plan` and
`render.py`. All Playwright work runs on worker threads, so the UI never blocks.

---

## Automation (cron)

Manage the auto-fill cron jobs from inside the tool:

```bash
uv run doch1 cron install   # idempotently install/update the jobs
uv run doch1 cron status    # show what's installed (read-only; also `cron list`)
uv run doch1 cron remove    # remove both jobs
```

`cron install` writes two tagged crontab lines (idempotent — it **replaces**
stale tagged lines rather than duplicating them):

- **Daily** (`# doch1-auto-report`, default `30 7 * * *`): runs
  `python -m doch1.main` → the `today` report.
- **Weekly** (`# doch1-auto-week`, default `40 7 * * 0`, Sunday): runs
  `python -m doch1.main week --json` to fill the upcoming Sun–Sat week.

Flags: `--daily "<expr>"`, `--weekly "<expr>"`, `--no-weekly` (daily only),
`--json`. Edit times further with `crontab -e` (e.g. `30 7 * * 0-4` for Sun–Thu).

**Every generated line carries `DOCH1_NONINTERACTIVE=1` and `DOCH1_CRON=1`** so
cron can never launch the TUI. Cron runs headless Chromium against the saved
session — **run `doch1 login` once first.** When the session eventually expires
the run exits non-zero and (if configured) Telegram-pings you to re-login. A
dead session is never silent.

### Non-interactive / cron safety

A bare `doch1` launches the UI **only** on a real interactive terminal. It falls
back to running `today` (no UI, no hang, exit 0) whenever:

- **stdin or stdout is not a TTY** (cron, pipes, `nohup`, CI logs), or
- **`DOCH1_NONINTERACTIVE=1`** is set (explicit kill-switch), or
- **`CI`** or **`DOCH1_CRON`** is set.

`DOCH1_FORCE_UI=1` forces the UI on even without a TTY (debugging only). The cron
daily line invokes `python -m doch1.main`, which always runs `today` and never
touches the UI path. rich also auto-disables color/box-drawing off a TTY, so cron
logs stay plain text.

### Telegram alerts (optional)

1. Message `@BotFather` → `/newbot` → copy the token.
2. Message your new bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → read `chat.id`.
3. Put `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`.

Omit both → alerts disabled (failures still exit non-zero and log).

---

## Status check & troubleshooting

| Symptom | Fix |
|---|---|
| Command prints **`Auth expired`** / `status` says not authenticated | Re-run `doch1 login` (a headed popup). The session lapses every few weeks. |
| Login window opens but **never reaches / never accepts the SMS code** | Use the headed flow: `DOCH1_HEADFUL=1 doch1 login --manual`. Headless SMS does not work. |
| **"SMS MFA needs a headed window"** error | You ran auto (headless) mode against the SMS-only tenant. Re-run with `--manual` or `DOCH1_HEADFUL=1`. |
| `No session. Run \`doch1 login\`` | No `auth.json` and no `DOCH1_COOKIE`. Run `doch1 login`. |
| Cron reports nothing / silent | `doch1 cron status` to confirm the jobs; check `doch1.log`; ensure `doch1 login` was run on the same machine. |
| `crontab not available` | No `crontab` binary on this host; install cron or add the line manually. |
| Bare `doch1` launches the UI in a script | Set `DOCH1_NONINTERACTIVE=1` (or it auto-detects non-TTY / `CI` / `DOCH1_CRON`). |
| `doch1 statuses --refresh` says "not yet wired" | Expected — only at-base `01/01` is known offline; see [Status selection](#status-selection). |

`doch1 status` checks the saved session against the live endpoint and reports the
transport (`browser-session` or `cookie`).

---

## Fully unattended (not currently possible)

The goal of zero-touch login (a `pyotp` TOTP seed in `DOCH1_TOTP_SEED` so `doch1
login` auto-answers MFA) was **researched and confirmed not cleanly possible on
the IDF Entra tenant**:

- The tenant **blocks generic third-party authenticator apps** — the
  security-info "add method" flow only offers Microsoft Authenticator, never the
  "different app" option that yields a base32 TOTP seed.
- **Microsoft Graph** can't mint one: `softwareOathMethods` has no create
  endpoint and `secretKey` always returns `null`.
- Microsoft Authenticator's `activatev2` registration protocol is **not publicly
  reverse-engineered**, so you can't forge a registration to generate a seed.

The only theoretical route is registering Microsoft Authenticator on a **rooted
Android emulator** and extracting `oath_secret_key` from its SQLite DB — fragile,
broken by Microsoft's Feb-2026 root detection, and a high-maintenance dead end.

**The accepted MVP is the headed SMS login every few weeks; everything in
between is automated.** This is a known dead end, not a roadmap item. (The
`DOCH1_TOTP_SEED` hook stays dormant: if you ever obtain a real seed, set it and
`doch1 login` auto-answers the code.)

---

## Development

Hermetic, no-secret, no-browser test/lint loop (this is also what CI runs):

```bash
uv sync --extra dev
uv run pytest -q                 # hermetic unit tests (no network, no browser)
uv run ruff check .              # lint
uv run ruff format --check .     # format
uv run mypy src/doch1            # typecheck
```

CI (`.github/workflows/ci.yml`) runs lint + format + mypy + pytest on push/PR;
`.github/workflows/release.yml` builds and publishes a GitHub Release on a `v*`
tag. Neither installs Chromium or touches secrets — Playwright is lazy, and
`tests/conftest.py` blocks accidental network and points state/env at tmp paths.

### Manual live login ritual

CI cannot exercise the real browser login, so the login MUSTs are validated by
hand:

```bash
DOCH1_HEADFUL=1 uv run doch1 login --manual
```

Confirm: a real Chromium window pops, email **and** password autofill, you enter
the SMS code (browser or terminal), it prints `OK session saved`. Then prove the
session works: `uv run doch1 status` and `uv run doch1 today` (exit 0).

---

## Layout

```
src/doch1/api.py      data layer + transport interface — Doch1Error, HistoryDay,
                      RequestsClient (cookie fallback), action functions, config
src/doch1/session.py  BrowserClient (Playwright, WAF-priming, auto-refresh) +
                      login() (assisted/auto, SMS terminal/browser race, fail-fast)
src/doch1/statuses.py status registry + --status resolver + Hebrew->English gloss
src/doch1/cron.py     pure crontab-line builder / merge / remove / status
src/doch1/cli.py      Typer CLI + client selection + the TTY gate (_should_launch_ui)
src/doch1/render.py   rich tables / badges shared by CLI and TUI
src/doch1/dates.py    pure week-anchor logic (Saturday -> next week)
src/doch1/tui/        Textual UI: app.py (shell + DataService), screens.py,
                      modals.py, data.py (textual-free fetch/fill shared with `week`)
src/doch1/main.py     thin shim so cron's `python -m doch1.main` => `today`
```

`plans/` holds internal working notes (roadmap, design notes) — this README is
the canonical user-facing doc.

---

## API (reverse-engineered via the app)

Browser-session auth (cookie fallback). Reports are `multipart/form-data` (body
`true` on success); queries are JSON.

| Action | Endpoint | Fields |
|---|---|---|
| Today | `POST /api/Attendance/InsertPersonalReport` | multipart `MainCode`, `SecondaryCode` |
| Future day | `POST /api/Attendance/InsertFutureReport` | multipart `MainCode`, `SecondaryCode`, `Note`, `FutureReportDate` (DD.MM.YYYY) |
| List scheduled | `POST /api/Attendance/getFutureReport` | JSON `{month, year}` → `{days[], minDate, maxDate}` |
| Past history | `POST /api/Attendance/memberHistory` | JSON `{month, year}` → `{days[]}` with reported/determined/approved, `inBase`, `conflict`, `note` |

`01/01` = "at base / present" (the default). Choose another via `--status` /
`DOCH1_MAIN_CODE` / `DOCH1_SECONDARY_CODE` once the live codes are discovered
(see [Status selection](#status-selection)).

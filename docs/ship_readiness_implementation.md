# Ship Readiness — Implementation Tracking

Findings from [ship_readiness_audit.md](ship_readiness_audit.md), addressed one
by one. Decisions recorded as we go.

---

## Status Key

- 🔴 Not started
- 🟢 Discussed, decision made, not yet implemented
- 🟢 Implemented
- ⬜ Deferred (not blocking go-live)

---

## B1 — No server on master 🟢

**Problem:** `main.py server` crashes on master because `server.py` lives on
`twilio-integration`.

**Decision:** Add a guard in `_run_server()` on master. Try importing `server`;
if it fails, print: "Server mode requires the twilio-integration branch." and
exit. The Twilio config validation that currently runs first should move after
the import check (no point checking Twilio keys if server.py is missing).

**Branch:** master
**File:** `main.py`, `_run_server()` function

---

## B2 — No printer agent 🟢

**Problem:** `printer.py` formats orders but nothing sends them to the kitchen
printer. The server-side printer API exists on `twilio-integration` but there's
no client polling it.

**Decision:** Build a standalone agent script in `printer_agent/` on master.
Imports `printer.py` from the parent repo. Talks to the server via HTTP only —
no code dependency on `server.py`.

**Architecture:**
- **Directory:** `printer_agent/` at repo root
- **Config:** `printer_agent/config.json` — server_url, restaurant_id, api_token,
  printer_ip, poll_interval (default 5s), mode ("escpos" or "file")
- **Main loop:** poll → format → send to printer (TCP port 9100) → mark printed → sleep
- **Error recovery:** printer failures: 3 retries with 30s backoff, leave
  `printed=false`. Server unreachable: log, retry next poll. Crash mid-print:
  order stays unprinted by design (marked only after printer confirms).
- **Dependencies:** `requests` for HTTP, raw TCP socket for printing (no
  python-escpos needed for v1)

**Branch:** master
**Files:** `printer_agent/agent.py`, `printer_agent/config.example.json`,
`printer_agent/requirements.txt`, `printer_agent/README.md`
**Deferred:** `printer_agent/pyinstaller.spec` — build after agent is tested
and working; not needed for go-live (agent runs fine with `python agent.py`)

---

## B3 — `.env.example` is stale 🟢

**Problem:** `.env.example` was missing `DB_PATH`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, and `API_TOKEN`. `server.py` read `API_TOKEN` via
raw `os.environ`.

**Resolution:** Added all four to `.env.example` + `api_token` to `AppConfig`
on master. Switched `server.py` on twilio-integration to use `config.api_token`
(module-level `_api_token` set in lifespan). Removed unused `os` import.
**Branch:** master + twilio-integration
**Files:** `.env.example`, `config.py`, `server.py`

---

## I1 — Stale `.review-approved` file 🟢

**Problem:** `.review-approved` file from previous commit still in working tree.
Gitignored, so not a commit risk, but messy.

**Decision:** Delete it.
**Branch:** master
**Action:** `rm .review-approved`

---

## I2 & I3 — README fixes 🟢

**Problem I2:** README says "233 tests" — there are 197.
**Problem I3:** README lists `server.py` and `twilio_client.py` under project
structure without noting they're on the integration branch. Also missing
`db.py`, `payment.py`, and `printer.py` from the listing.

**Decision:** Fix together (same file). Change test count to 197. Mark
integration-branch files clearly. Add missing core files to the listing.
**Branch:** master
**File:** `README.md`

---

## I4 — Dead imports 🟢

**Problem I4a:** `printer.py:14` imports `timezone` but never uses it.
**Problem I4b:** `tools.py:25,27` imports `CartTopping` and `CustomerInfo` but
never directly references either.
**Problem I4c:** `prompts.py:54-55` has `build_tool_prompt` and
`build_response_prompt` compat aliases that nothing imports.

**Decision:** Fix all three together. Remove unused imports/aliases. Update
`docs/prompts.md` to remove the mention of compat aliases.
**Branch:** master
**Files:** `printer.py`, `tools.py`, `prompts.py`, `docs/prompts.md`

---

## I5 & I6 — Stale test artifacts 🟢

**Problem I5:** 673 stale session JSONs in `sessions/` from testing.
**Problem I6:** `order_agent.db` SQLite file in project root from testing.
Both gitignored — no commit risk. Just clutter.

**Decision:** Delete both.
**Branch:** master (filesystem cleanup only, nothing to commit for gitignored files)
**Action:** `rm -rf sessions/` and `rm order_agent.db`

---

## I7 — Stale doc references 🟢

**Problem:** `docs/catalogue.md` references old `menu_manager.py`.
`docs/error_recovery.md` references `server.py` (integration-branch file).
`docs/production_roadmap.md` references `server.py` and `config.json` (fine —
design doc forward-references are correct in context).

**Decision:** Add brief clarification notes to `docs/catalogue.md` and
`docs/error_recovery.md`. Leave `docs/production_roadmap.md` as-is — it's a
design document and the references are accurate.
**Branch:** master
**Files:** `docs/catalogue.md`, `docs/error_recovery.md`

---

## I8 — `.gitignore` duplicate entry 🟢

**Problem:** `order_agent.db` listed twice (lines 8 and 9).

**Decision:** Remove line 9.
**Branch:** master
**File:** `.gitignore`

---

## N1 — Config validation missing restaurants.json check 🟢

**Problem:** Audit said config.py doesn't check if restaurants.json exists.
**Resolution:** The check already exists in `RestaurantRegistry.__init__()` 
(raises `FileNotFoundError` with clear message). Adding it to `config.py` 
would be redundant — config parses env vars; RestaurantRegistry validates 
filesystem. No change needed.
**Branch:** master (no change)

---

## N2 — Remove `_db_handle` global 🟢

**Resolution:** `Database` now owns its connection as `self._db` instance
attribute. No more module-level global. Each instance is self-contained.
**Commit:** `d753eda` (master)

---

## N3 — Remove JSON persistence fallback 🟢

**Resolution:** `session.save()` now requires `_db` to be set. Removed
`OrderSession.load()` classmethod. `SessionRouter` no longer takes `sessions_dir`;
`db` is required in `get_or_create()`. `process_turn()` no longer takes
`sessions_dir`. All persistence goes through SQLite.
**Commit:** `db6120e` (master), `47ef765` (integration follow-up)

---

## N5 — Structured logging 🟢

**Resolution:** Added `log_json` config field (`LOG_JSON` env var). When enabled,
logs emit JSON via `python-json-logger`. Added request ID middleware to server
for log correlation. Defaults to plain text for dev.
**Commit:** `7fd5787` (master), `fa8a578` (integration middleware)

---

## N4 — No health check endpoint 🟢

**Problem:** No `/health` endpoint for monitoring, load balancers, uptime checks.
**Resolution:** Added `GET /health` returning `{"status": "ok"}` to server.py.
**Branch:** twilio-integration
**File:** `server.py`

---

## N6 — Demo menu only ⬜

**Problem:** `menus/marios_pizzeria.json` is a demo. Real menus needed before
shipping. Not a code fix — manual onboarding per production roadmap component 5.

---

## New Finding — Server test gaps 🟢

Discovered during exploration. `tests/test_server.py` only tested the WhatsApp
webhook. Missing coverage for:
- Printer API endpoints (`GET /api/orders`, `POST /api/orders/{id}/printed`)
- Stripe webhook (`POST /payment/webhook`)
- Payment UX pages (`GET /payment/success`, `GET /payment/cancel`)

**Resolution:** Added 11 new tests (5 printer API, 4 Stripe webhook, 2 payment
pages). All 229 tests pass (140 integration).
**Branch:** twilio-integration
**File:** `tests/test_server.py`


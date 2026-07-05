# Roadmap to 100/100

## 1. Remove secrets from disk — env vars only

**Problem:** DeepSeek key, Twilio SID/auth token, API token all live in `.env` on disk.
One `zip` or `git add -f` leaks everything.

**Plan:**
- Make all secrets required at startup with clear error messages, no defaults
- Remove `.env` file loading entirely — use environment variables only
- Add `dotenv` as optional (dev convenience only, never in production)
- `docker-compose.yml` already uses `${VAR:?required}` pattern — verify
- Add `.env.example` with placeholder values and clear instructions

**Files:** `config.py`, `.env.example`, `docker-compose.yml`, `CLAUDE.md`

---

## 2. Idempotency for Twilio webhooks

**Problem:** Twilio can redeliver webhooks. No deduplication means same WhatsApp
message processed twice → duplicate cart items, double orders.

**Plan:**
- Twilio sends `MessageSid` in every webhook — store recently processed IDs
- Before processing, check if this MessageSid was already handled
- Use in-memory set with TTL (or DB table with auto-cleanup)
- Return 200 immediately for duplicates (don't process again)

**Files:** `server.py`, `db.py`

---

## 3. Second working domain

**Problem:** `domain.py` has `requires_catalogue` field and multi-domain design but
only "order" domain exists. The abstraction is unproven.

**Plan:**
- Create a "support" domain — simple FAQ bot with no catalogue
- `tools_by_state`: just `cancel_order` (to exit)
- `system_prompt_template`: helpful support agent
- `requires_catalogue: False`
- Test that `process_turn` works without catalogue/pricing when domain says so

**Files:** `domain.py`, `agent_loop.py`, `prompts.py`, `tests/test_domain.py`

---

## 4. Test fragility — hardcoded counts and weak assertions

**Problem:** Tests break on legitimate changes. `len(TOOLS_BY_STATE[...]) == 7`,
`len(deals) == 3`, substring assertions like `"building" in prompt`.

**Plan:**
- Replace count assertions with name-based checks: `assert tool_name in tool_names`
- Replace substring assertions with structural checks
- Make prompt tests check for field presence, not specific string positions
- Review all test files for brittleness

**Files:** `tests/test_tools.py`, `tests/test_catalogue.py`, `tests/test_prompts.py`,
`tests/test_agent_loop.py`, `tests/test_e2e.py`

---

## 5. Input validation on WhatsApp messages

**Problem:** No length limit, no content filtering. A malicious user sends 100KB of
text → LLM token costs. Prompt injection possible.

**Plan:**
- Limit message body to 2000 characters
- Strip control characters, normalize unicode
- Add basic prompt injection guard: strip common attack patterns
- Log and silently drop clearly malicious messages
- Add validation tests

**Files:** `server.py`, `tests/test_server.py`

---

## 6. Session cleanup automation

**Problem:** `Database.cleanup_old_sessions()` exists but nothing calls it. Terminal
sessions accumulate forever.

**Plan:**
- Add background task to server lifespan that runs cleanup periodically
- Cleanup interval: every 6 hours, delete sessions older than 30 days
- Add to dashboard startup too (only one of the two needs to run it)
- Log cleanup counts

**Files:** `server.py`, `dashboard.py`

---

## 7. Proper database migrations

**Problem:** `ALTER TABLE ... ADD COLUMN` in a try/except in `_create_tables()`.
No versioning, no rollback, no tracking of what migrations have run.

**Plan:**
- Add a `_migrations` table that tracks applied migrations by name
- Each migration is a function with an `up()` method
- `_run_migrations()` checks the table, runs any new ones in order
- Move the `customer_address` column addition to a named migration

**Files:** `db.py`

---

## 8. Idempotent order saving

**Problem:** If `persist_completed_order` is called twice for the same order
(e.g., webhook redelivery), it creates duplicate order records.

**Plan:**
- Check if order with same session_id + COMPLETED state already exists
- If so, skip save and return existing order_id
- Add unique constraint or application-level check

**Files:** `db.py`, `server.py`, `main.py`

---

## 9. Structured metrics

**Problem:** Can't answer "how many orders today?" without manual DB query.
No visibility into LLM latency, error rates, or throughput.

**Plan:**
- Add lightweight metrics collection in-memory
- Track: orders_today, llm_call_count, llm_error_count, avg_latency_ms
- Expose at `/metrics` endpoint (JSON)
- Show on dashboard overview page
- Reset on restart (acceptable for now)

**Files:** `server.py`, `dashboard.py`, `dashboard_templates/overview.html`

---

## 10. Audit logging

**Problem:** No record of who changed what. Menu edits, restaurant adds, order
cancellations — all happen silently with no audit trail.

**Plan:**
- Add `audit_log` table: timestamp, actor, action, target, details
- Log: menu edits, restaurant add/edit, order state changes, dashboard access
- Show recent audit entries on a new `/audit` dashboard page
- Keep 90 days of audit logs

**Files:** `db.py`, `dashboard.py`, `server.py`, `menu_manager.py`

---

## 11. ESC/POS completeness

**Problem:** ESC/POS output uses basic init/bold/cut. Missing: character encoding
setup, line spacing, double-strike for totals, drawer kick.

**Plan:**
- Add `ESC @` init at start
- Add `ESC !` for double-height on order number
- Add `ESC !` bold on total line
- Add `GS V` full cut at end
- Add `ESC d` feed before cut (so paper tears at right spot)
- Verify output against ESC/POS specification

**Files:** `printer.py`, `tests/test_printer.py`

---

## 12. Per-restaurant API tokens

**Problem:** One global `API_TOKEN` for dashboard and printer agent. If compromised,
all restaurants exposed.

**Plan:**
- Add `api_token` field to `RestaurantConfig` (optional, falls back to global)
- Printer agent uses per-restaurant token if set
- Dashboard checks token against restaurant config for scoped access

**Files:** `restaurant.py`, `dashboard.py`, `server.py`, `printer_agent/agent.py`

---

## 13. Deployment documentation

**Problem:** No guide for deploying to production. README assumes local development.

**Plan:**
- Add `docs/deployment.md`: server setup, HTTPS, domain, Twilio webhook config
- Add `docs/operations.md`: monitoring, backups, troubleshooting
- Update README with production quick-start

**Files:** `docs/deployment.md`, `docs/operations.md`, `README.md`

---

## 14. Remove hardcoded config constants

**Problem:** `max_iterations=5`, `_STALE_HOURS=2.0`, `_LOCK_TTL=3600`,
`_RATE_LIMIT_MAX=20`, `_AGENT_TIMEOUT=45` — all hardcoded in modules.
Changing them requires code edits and redeploy.

**Plan:**
- Move all tunables to `AppConfig`
- Read from env vars with sensible defaults
- Document each in `.env.example`

**Files:** `config.py`, `agent_loop.py`, `server.py`, `.env.example`

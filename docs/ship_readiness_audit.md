# Ship Readiness Audit — June 2026

Comprehensive audit of the OrderProcessingAgent codebase to assess whether
it's ready to ship and deploy for a first restaurant.

**Verdict: Not ready. Core is solid, I/O layer is missing. ~2-3 weeks to go-live.**

---

## What's Good

| Area | Assessment |
|---|---|
| **Architecture** | Clean separation. `process_turn()` is the universal interface. Python owns state & math. LLM only suggests. |
| **Core logic** | `agent_loop.py`, `tools.py`, `catalogue.py`, `pricing.py` — tight, readable, well-documented. |
| **State machine** | Correct — BUILDING→REVIEW→PAYMENT_PENDING→COMPLETED, cancel from anywhere, preconditions enforced in both tools AND transition layer. |
| **Error recovery** | Three layers: LLM retry with exponential backoff (1s/3s/9s), tool atomicity rollback via session snapshots, message re-delivery via conversation history. All on master. |
| **Test suite** | 197 tests, all passing. Covers catalogue, pricing, tools, sessions, multi-restaurant, edge cases. Integration tests verify the live call chain. |
| **Multi-restaurant** | Clean design — `restaurants.json` drives everything, isolated sessions/orders per (restaurant_id, phone). |
| **Code style** | Consistent. Typed. Good docstrings. Pydantic v2 throughout. Single-provider LLM config (no confusing provider abstraction). |
| **Stripe payment** | `payment.py` is well-designed — pure stateless functions. `confirm_order(payment_method="link")` already transitions to PAYMENT_PENDING. Webhook verification is clean. PCI SAQ A (lowest burden). |
| **Printer formatting** | `printer.py` produces ESC/POS bytes and HTML preview. Clean layout for 80mm thermal paper. Pluggable transport designed. |
| **Commit workflow** | Robust two-layer gate (pre-commit hook + Claude Code guard). Cannot be bypassed. |

---

## Blockers — Cannot Ship Without These

### B1. No server on master

`main.py server` calls `uvicorn.run("server:app")` but `server.py` does not exist
on master. It lives on the `twilio-integration` branch. Without it, no WhatsApp
orders can be received.

`main.py:358`:
```python
uvicorn.run("server:app", host=args.host, port=args.port, reload=False)
```

**Impact:** The only production channel (WhatsApp) is unavailable. The CLI is
a dev tool, not a customer-facing interface.

### B2. No printer agent

`printer.py` formats orders to ESC/POS bytes. The server (on the integration
branch) exposes a printer API (`GET /api/orders`, `POST /api/orders/{id}/printed`).
But there is nothing connecting them.

The printer agent is designed but not built:
1. Poll `GET /api/orders?restaurant_id=X&token=Y` every 5-10 seconds
2. For each unprinted order, call `format_order()` to get ESC/POS bytes
3. Open a TCP socket to the printer on port 9100, send the bytes
4. Call `POST /api/orders/{id}/printed?token=Y` to mark done

**Impact:** The kitchen never sees orders. This is a business failure — a
restaurant taking orders that never get cooked.

### B3. `.env.example` is stale

Missing three env vars that `config.py` reads:

| Missing var | Default | Added in |
|---|---|---|
| `DB_PATH` | `order_agent.db` | `db.py` (component 1) |
| `STRIPE_SECRET_KEY` | `""` | `payment.py` (component 2) |
| `STRIPE_WEBHOOK_SECRET` | `""` | `payment.py` (component 2) |

**Impact:** New developers following `.env.example` won't know these exist.
Stripe requires both secrets for payment processing.

---

## Important — Should Fix Before Go-Live

### I1. Stale `.review-approved` file

`/mnt/DataDrive/UserData/projects/OrderProcessingAgent/.review-approved` exists
in the working tree (last modified Jun 21). It's in `.gitignore` so it won't be
committed, but it should have been auto-cleaned by the Claude Code commit guard
hook after the last commit. Manual cleanup needed.

### I2. README test count is wrong

README says "233 tests" — there are 197.
`README.md:88`: `├── tests/               # Full pytest suite (233 tests)`

### I3. README lists files that don't exist on master

`README.md:96-97`:
```
├── server.py            # FastAPI Twilio WhatsApp webhook (twilio-integration)
├── twilio_client.py     # Twilio REST API wrapper (twilio-integration)
```

These are listed under "Project Structure" without a clear indication they
only exist on the integration branch. A developer cloning master will be confused.

### I4. Dead imports

Three unused imports:

| File | Line | Import | Note |
|---|---|---|---|
| `printer.py` | 14 | `timezone` (from `datetime`) | Imported, never used |
| `tools.py` | 25 | `CartTopping` | Imported, never directly referenced |
| `tools.py` | 27 | `CustomerInfo` | Imported, never directly referenced |

Also two backward-compatibility aliases in `prompts.py` that nothing imports:

```python
# prompts.py:54-55
build_tool_prompt = build_system_prompt
build_response_prompt = build_system_prompt
```

Only referenced in `docs/prompts.md` as "kept as backward-compatible." No code
imports them. If truly backward-compat, they should be imported somewhere. If not,
they're dead.

### I5. 673 stale session files in `sessions/`

Test artifacts accumulated over development. Gitignored, so they won't be
committed, but they clutter the working tree. No cleanup mechanism.

### I6. `order_agent.db` in project root

A 20KB SQLite database file from testing sits in the project root. Gitignored
but messy.

### I7. Docs reference files that don't exist on master

| Doc | References | Issue |
|---|---|---|
| `docs/catalogue.md` | `menu_manager.py` | Old v1 filename, now `catalogue.py`. Used in historical context ("Evolution of v1's menu_manager.py") — low severity. |
| `docs/error_recovery.md` | `server.py` | Integration-branch file, not on master. |
| `docs/production_roadmap.md` | `server.py` (10 refs), `config.json` (2 refs) | Integration-branch files. The roadmap is a design doc, so these are forward references — but they read as if the files exist. |

### I8. `.gitignore` has duplicate entry

`order_agent.db` appears twice in `.gitignore`. Harmless but sloppy.

---

## Nice-to-Have — Not Blocking

### N1. `config.py` validation is incomplete

`_validate()` checks `llm_api_key`, `llm_model`, and `llm_base_url`, but does
NOT check that `restaurants.json` exists. A missing file gives a confusing
error downstream (`FileNotFoundError` in `RestaurantRegistry`) instead of a
clear "create restaurants.json" message at startup.

### N2. `db.py` uses a global module-level `_db_handle`

```python
# db.py:37
_db_handle: SqliteDatabase | None = None
```

The `Database.__init__` sets this global. `BaseModel.Meta.database` references
it. If two `Database` instances are created (unlikely but possible in tests),
the second overwrites the first. Works fine for single-process use but is a
latent footgun. A cleaner approach: pass the database handle through Peewee's
`bind()` method or use a registry pattern.

### N3. Dual persistence path creates cognitive overhead

`process_turn()` takes `sessions_dir` for JSON fallback. `main.py` creates a
`Database` and attaches it via `session._db`. Every code path carries both
paths. Intentional and documented, but eventually the JSON fallback should be
removed once SQLite is proven.

### N4. No health check endpoint

No `/health` or readiness probe for the server. Needed for production
monitoring and orchestration.

### N5. No structured logging

Logging uses Python's standard `logging` module with format strings. No JSON
logging, no log levels beyond DEBUG/INFO. Fine for a first restaurant, but
will need upgrading for production observability.

### N6. Demo menu only

`menus/marios_pizzeria.json` is a demo menu. Real restaurant menus are needed
before shipping. (Acknowledged — onboarding is manual per component 5 of the
production roadmap.)

---

## Code Quality Summary

| Metric | Score | Notes |
|---|---|---|
| Readability | ⭐⭐⭐⭐⭐ | Excellent docstrings, clear naming, consistent style |
| Maintainability | ⭐⭐⭐⭐ | Clean separation, one concern per file |
| Test coverage | ⭐⭐⭐⭐ | 197 tests, good breadth across all modules |
| Dead code | ⭐⭐⭐⭐ | Very little — 3 unused imports, 2 unused aliases, 1 duplicate gitignore entry |
| Documentation | ⭐⭐⭐ | Generally good but has stale references and wrong test count |
| Production readiness | ⭐⭐ | Core is solid, I/O layer is missing |

---

## Recommended Path to Ship

### Phase 1: Wire the I/O layer (week 1)

- Merge or rebuild `server.py` on master (Twilio webhook)
- Add `server.py`, `twilio_client.py` to master or document that they require
  the integration branch
- Fix `.env.example` (add `DB_PATH`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`)

### Phase 2: Build the printer agent (week 1-2)

- Create the printer agent script (poll loop, TCP socket, error recovery)
- The ESC/POS formatting already exists in `printer.py`
- Package as a standalone `.exe` with PyInstaller

### Phase 3: Cleanup and hardening (week 2)

- Fix dead imports (I4)
- Delete `.review-approved`, `order_agent.db`, and stale sessions (I1, I5, I6)
- Fix README test count and file listing (I2, I3)
- Fix `.gitignore` duplicate (I8)
- Update stale doc references (I7)

### Phase 4: Production hardening (week 2-3)

- Add `/health` endpoint (N4)
- Add startup validation for `restaurants.json` existence (N1)
- End-to-end test with Twilio sandbox + Stripe test mode
- Onboard one real restaurant menu

### Phase 5: Polish (post-go-live)

- Structured logging (N5)
- Remove JSON persistence fallback (N3)
- Refactor `_db_handle` global (N2)

---

## What Does NOT Need a Rewrite

The core is genuinely solid. These files should stay as-is:

- `agent_loop.py` — clean loop pattern, correct error recovery
- `tools.py` — well-designed decorator, correct tool implementations
- `catalogue.py` — excellent fuzzy matching, clean deal expansion
- `pricing.py` — correct math, O(1) lookups
- `models.py` — well-typed Pydantic models, clear field documentation
- `session.py` — clean state model, correct persistence dispatch
- `session_router.py` — correct (restaurant, phone) → session mapping
- `restaurant.py` — clean registry pattern, good validation
- `config.py` — simple, correct (minus N1)
- `payment.py` — clean stateless functions, correct Stripe integration
- `printer.py` — good ESC/POS formatting, reusable across channels
- `prompts.py` — effective prompt design, correct cart/customer formatting
- `llm_client.py` — clean retry logic, correct message conversion

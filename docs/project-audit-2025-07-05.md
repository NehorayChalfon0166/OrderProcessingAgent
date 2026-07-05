# Project Audit & Recommendations — July 5, 2025

> **Scope:** Full codebase review, branching/deployment strategy, dashboard assessment, CLAUDE.md rewrite, and general-purpose bot feasibility.
>
> **Status:** Report — no code changes made. Iterate on this doc before implementing.

---

## Table of Contents

1. [Issues & Inconsistencies Found](#1-issues--inconsistencies-found)
2. [Deploy from Master — What Changes](#2-deploy-from-master--what-changes)
3. [Git Hooks — Removal Plan](#3-git-hooks--removal-plan)
4. [Dashboard Assessment & Improvements](#4-dashboard-assessment--improvements)
5. [CLAUDE.md Rewrite — Research & Recommendations](#5-claudemd-rewrite--research--recommendations)
6. [General Customer Service Bot — Feasibility](#6-general-customer-service-bot--feasibility)
7. [Action Items — Prioritized](#7-action-items--prioritized)

---

## 1. Issues & Inconsistencies Found

### 1.1 Critical (bugs / broken flows)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | **Printer agent calls non-existent API endpoints** — `/api/orders` and `/api/orders/{id}/printed` don't exist. The printer agent is dead code on this branch. | `printer_agent/agent.py:174,203` | Printer agent non-functional |
| 2 | **`/sessions` endpoint conflates sessions and orders** — It calls `get_orders()` and filters by `printed is False`, treating orders as active sessions. These are different entities with different schemas. | `dashboard.py:126-137` | Sessions page shows wrong data |
| 3 | **`order_detail.html` address field always shows "N/A"** — Template looks for `order.address` but the DB row puts address inside a nested `customer` object. | `dashboard.py:110-123`, `order_detail.html:79` | Delivery address never displayed |
| 4 | **Token leaks into browser history and server logs** — `API_TOKEN` is passed as a `?token=xxx` query parameter on every dashboard URL. Appears in browser history, Referer headers, and server access logs. | `dashboard.py:60-63`, all templates via `{{ token_url }}` | Security: token exposed |
| 5 | **Two-layer commit guard not wired** — `commit-guard.sh` exists but `settings.json` has `{"hooks":{}}`. Claude Code never triggers the hook. The manual review gate described in CLAUDE.md doesn't actually run. | `.claude/settings.json:1`, `.claude/hooks/commit-guard.sh` | No review enforcement |
| 6 | **Menu edits don't hot-reload** — The dashboard menu editor writes to the JSON file, but `Catalogue` and `PricingEngine` are loaded once at server startup. Changes only take effect after restart. | `dashboard.py:164-182`, `catalogue.py` | Stale data after edits |

### 1.2 High (design problems / dead code)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 7 | **`payment.py` is dead code on this branch** — It's imported nowhere reachable. Stripe integration lives on integration branches only. | `payment.py` (entire file) | Confusion, maintenance burden |
| 8 | **`expand_deal()` is never called** — The function exists and is tested, but no tool or agent code ever invokes it. Deals are added as flat line items, never expanded into constituent products. | `catalogue.py:expand_deal()` | Dead code |
| 9 | **`requirements.txt` missing core dependencies** — `fastapi`, `uvicorn`, `jinja2`, and `requests` are imported at runtime but not listed. `pip install -r requirements.txt` would fail to run the dashboard. | `requirements.txt` | Broken fresh install |
| 10 | **`browse_menu` tool doesn't exist but tests reference it** — `test_chained_tool_calls` sends a `browse_menu` tool call from the mock LLM. This exercises the "unknown tool" error path, which is valid, but the test name is misleading. Also, the LLM genuinely has no way to browse the menu programmatically — it can only discover items through `add_to_cart` failures. | `tools.py` (missing), `tests/test_agent_loop.py:274` | Missing feature + misleading test |
| 11 | **Integration test uses manual pass/fail tracking** — 703 lines of `PASSED += 1` / `FAILED += 1` instead of pytest assertions. Hard to debug, no fixture support, no parallelization. | `tests/test_integration.py` | Maintainability |
| 12 | **Session/order ID collision** — `session_id` is the sanitized phone number. `order_id` in `main.py:save_order()` is set to `session.session_id`. Two orders from the same phone overwrite each other in the `OrderRow` table (uses `replace`). | `db.py:save_order()`, `main.py:save_order()` | Data loss on repeat orders |
| 13 | **Legacy migration skips all files** — `_migrate_if_needed` expects per-restaurant subdirectories in `sessions/`, but legacy sessions are flat files. The migration finds nothing and skips everything. | `db.py:_migrate_if_needed()` | Dead migration code |

### 1.3 Medium (code quality / maintainability)

| # | Issue | Location |
|---|-------|----------|
| 14 | `save_session()` serializes cart twice — first on line 127 (`model_dump_json`), then overwritten on line 135. First serialization is dead work. | `db.py:127-128` |
| 15 | `_type_to_json_schema` has a dead `if origin is None: pass` clause that does nothing. | `tools.py:93` |
| 16 | `add_to_cart` comment numbering jumps from step 2 to step 4 (no step 3). | `tools.py:234` |
| 17 | `set_customer_info` silently swallows invalid `order_type` values (`except ValueError: pass`). Caller gets no feedback. | `tools.py:284` |
| 18 | `remove_from_cart` returns `success=False` for both "not found" and "ambiguous match" — the LLM can't distinguish without inspecting `matches`. | `tools.py:311-340` |
| 19 | `MAX_ITERATIONS = 5` and `tool_calls[:5]` are hardcoded constants, not configurable. | `agent_loop.py:26,94` |
| 20 | `assert` used for runtime validation throughout `dashboard.py`. If Python runs with `-O`, all asserts are stripped → `UnboundLocalError` crashes. | `dashboard.py:67,81,93,113,129,143,158,167,188,202` |
| 21 | `json` imported inside `_parse_tool_calls` method body instead of at module top. | `llm_client.py:144` |
| 22 | `temperature=0.3` hardcoded, not configurable. | `llm_client.py:113` |
| 23 | `_RETRY_BACKOFF` array length (3) must match `_MAX_RETRIES` (3) but they're independent constants — easy to misalign. | `llm_client.py:34-35` |
| 24 | `Catalogue._fuzzy_best_match` strips apostrophes from queries but not from index keys, making exact match fail for names with apostrophes (like "Mario's"). | `catalogue.py` |
| 25 | Whitespace not stripped from phone numbers — `"  +1111111111  "` returns None from `get_by_twilio_phone()`. | `restaurant.py` |
| 26 | `config.py` validation only checks LLM fields are non-empty. Doesn't validate paths exist (`db_path`, `restaurants_path`, `orders_dir`). | `config.py` |

### 1.4 Low (cosmetic / nice-to-have)

| # | Issue | Location |
|---|-------|----------|
| 27 | `BANNER` contains emoji that may not render in all terminals. | `main.py:67` |
| 28 | `LINE_WIDTH = 42` hardcoded for thermal printer. Some printers use 48 or 32. | `printer.py` |
| 29 | No API versioning or consistent error format — endpoints return HTML, JSON, or plain text depending on the route. | `dashboard.py` |
| 30 | No Docker/compose files for deployment. | (missing) |
| 31 | No logging of token usage in `llm_client.py` — useful for cost tracking. | `llm_client.py` |
| 32 | Prompt tells LLM "Only say an order is confirmed when the state IS 'completed'" but this is a soft instruction with no enforcement. | `prompts.py` |

---

## 2. Deploy from Master — What Changes

### 2.1 What "deploy from master" means

Right now you have a branching strategy where:
- `master` = core logic only (models, agent_loop, tools, etc.)
- `{channel}-integration` branches = I/O layer for each channel (Twilio, dashboard, etc.)
- Integration branches must NOT modify master files (enforced by pre-commit hook)

If you deploy from master instead:

**All code lives on master.** Channel-specific files (`server.py`, `dashboard.py`, `dashboard_static/`, `dashboard_templates/`, `printer_agent/`, `payment.py`) move to master. No more integration branches.

### 2.2 What needs to merge into master

Your current branch is `dashboard-integration`. These files need to go to master:

```
dashboard-integration adds:
  dashboard.py
  dashboard_static/css/style.css
  dashboard_static/js/app.js
  dashboard_templates/base.html
  dashboard_templates/overview.html
  dashboard_templates/orders.html
  dashboard_templates/order_detail.html
  dashboard_templates/sessions.html
  dashboard_templates/session_detail.html
  dashboard_templates/restaurants.html
  dashboard_templates/menu_editor.html

Other integration branches (if they exist):
  twilio-integration → server.py, twilio_client.py, docs/twilio_integration.md
  (any others you may have)
```

### 2.3 What else changes

| What | Current | New |
|------|---------|-----|
| **Branch strategy** | `master` + `{channel}-integration` branches | Single `master` branch. Feature branches for development, merged to master when done. |
| **Pre-commit hook** | Blocks `*-integration` branches from touching master files | Remove this check entirely (no integration branches exist) |
| **CLAUDE.md** | Long section on branching rules, merge strategy, what belongs where | Simplified: "All code lives on master. Feature-branch → PR → merge." |
| **`main.py` server import** | Tries `import server`, tells user to switch branches on failure | `import server` just works — it's on master |
| **`config.py` channel fields** | Described as "dormant defaults" for integration branches | Regular config fields — they're always available |
| **`requirements.txt`** | "Core dependencies only" | All dependencies (FastAPI, uvicorn, Jinja2, etc.) |
| **`.env.example`** | "Additions for that channel" documented separately | Single `.env.example` with everything |
| **Deployment** | Pick which integration branch to deploy | Always deploy master |

### 2.4 Merge strategy (step-by-step)

```bash
# 1. Merge dashboard-integration into master
git checkout master
git merge dashboard-integration

# 2. Merge any other integration branches
git merge twilio-integration   # if it exists

# 3. Update files that reference the old strategy:
#    - CLAUDE.md: simplify branching section
#    - .githooks/pre-commit: remove branch-rule check
#    - requirements.txt: add fastapi, uvicorn, jinja2, requests

# 4. Delete the integration branches (optional — keep for history)
git branch -d dashboard-integration
git branch -d twilio-integration

# 5. Tag a release
git tag v1.0.0
```

### 2.5 Recommendation

**Do it.** The integration-branch strategy adds complexity for zero benefit at your scale. You're one developer with one deployment target. Feature branches + master is simpler and standard. The channel-abstraction (`process_turn()` takes a string, returns a string) is the real architecture win — it works regardless of where the files live.

---

## 3. Git Hooks — Removal Plan

You have two hooks systems. Here's exactly what to remove and how:

### 3.1 `.githooks/pre-commit` (mechanical checks)

**What it does:** Runs pytest, integration tests, smoke test, and branch-rule enforcement before every commit.

**To remove:**
```bash
# 1. Unset the hooks path
git config --unset core.hooksPath

# 2. Delete the hook file
rm .githooks/pre-commit

# 3. Remove the .githooks directory (if empty)
rmdir .githooks 2>/dev/null
```

**What you lose:**
- Automated test runs before commit
- Branch-rule enforcement
- Smoke test

**Mitigation (optional):** You can keep running tests manually (`python -m pytest tests/ -q`) or set up a GitHub Action / CI to run them on push. But if you want zero hooks, delete it.

### 3.2 `.claude/hooks/commit-guard.sh` (manual review gate)

**What it does:** Blocks `git commit` unless `.review-approved` exists. BUT — it's not wired up. `settings.json` has `{"hooks":{}}`, so Claude Code never triggers this script. It's dead infrastructure.

**To remove:**
```bash
# 1. Delete the hook script
rm .claude/hooks/commit-guard.sh

# 2. Remove the hooks directory (if empty)
rmdir .claude/hooks 2>/dev/null

# 3. No changes needed to settings.json — it's already empty
```

### 3.3 After removal

- `git commit` works directly — no gates, no review file, no pre-commit tests
- The commit workflow section in CLAUDE.md becomes obsolete (update it)
- If you later want CI, add a `.github/workflows/test.yml` instead of local hooks

### 3.4 Recommendation

**Remove both.** The pre-commit hook slows down commits (full test suite runs every time). The commit-guard hook is dead code. If you want quality gates, move them to CI where they don't interrupt your flow. A simple GitHub Action that runs `pytest` on push is more standard and doesn't make you wait during commits.

---

## 4. Dashboard Assessment & Improvements

### 4.1 What works well

- **Dark-mode UI is polished.** The 1,921-line CSS is thorough — custom scrollbars, hover effects, staggered animations, toast system, responsive sidebar. It looks premium.
- **Self-contained JS.** No framework dependencies. 361 lines of clean vanilla JS. Good patterns: IIFE wrapper, `'use strict'`, event delegation.
- **All core views exist.** Overview, orders list/detail, sessions list/detail, restaurant management, menu editor — complete coverage for an admin dashboard.
- **Inline menu editing** with atomic writes is a genuinely useful feature.
- **Multi-restaurant support** from day one — good foresight.

### 4.2 What needs fixing (bugs first)

1. **Token in URLs** — Move from query parameter to cookie-based auth or at minimum use `Authorization` header exclusively. The current `?token=xxx` approach leaks credentials.

2. **Sessions/orders conflation** — The `/sessions` endpoint needs to query `SessionRow` (via `load_session`), not `get_orders()`. These are different tables with different schemas.

3. **Address display** — `order_detail.html` needs to read `order.customer.address` (nested), not `order.address` (flat, doesn't exist).

4. **Menu hot-reload** — After a successful menu edit, either reload the `Catalogue`/`PricingEngine` for that restaurant, or return a response that triggers a page reload. Currently the edit succeeds but the displayed data is stale.

### 4.3 What to improve

**Architecture:**

| Issue | Suggestion |
|-------|------------|
| `assert` for runtime validation | Replace with proper `if X is None: raise HTTPException(500, ...)` checks. Assertions are for debugging, not runtime. |
| No consistent API layer | Add a `/api/` prefix with JSON responses. The HTML page endpoints call the same DB queries inline — extract shared query logic. |
| No real-time updates | Polling is fine for now, but consider SSE (Server-Sent Events) for order status changes. A new order placed via Twilio won't appear in the dashboard until manual refresh. |
| No pagination | Orders/sessions lists use `limit` but no offset/cursor. At 500+ orders, the page gets slow. Add pagination. |
| No search | Can't search orders by customer name or phone. Add a search bar. |

**UX:**

| Issue | Suggestion |
|-------|------------|
| No loading states | Between page navigations there's a flash of white. Add a thin top-bar loader (NProgress style) or simple fade transition. |
| No empty states | If there are zero orders, the page shows an empty table. Add an illustrated empty state ("No orders yet" with a suggestion). |
| Filter resets lost on navigation | If I filter orders by restaurant, click into an order, then go back, the filter is lost. Persist filters in URL params. |
| Menu editor lacks undo | Atomic writes are good, but there's no diff/preview before saving. Show a confirmation with the before/after values. |
| No order status update | Can't manually change order state from the dashboard (e.g., mark as delivered). This is read-only by design, but a "mark printed" or "cancel order" action would be useful. |

**CSS (minor):**

| Issue | Suggestion |
|-------|------------|
| 1,921 lines in one file | Split into logical sections: `layout.css`, `components.css`, `utilities.css`. Easier to maintain. |
| `@keyframes fadeIn` is globally declared | Already fine, but consider namespacing animation names if you add more. |
| No print stylesheet | The "print" section (lines ~1880-1921) exists but is minimal. Orders should print nicely for kitchen tickets. |

### 4.4 Features to consider adding

1. **Real-time order notifications** — SSE or WebSocket so new orders appear without refresh
2. **Order search** — by customer name, phone, or order ID
3. **Dashboard analytics** — revenue over time, popular items, peak hours (the data is all in the DB, just not surfaced)
4. **Dark/light mode toggle** — the dark mode is beautiful but some users prefer light
5. **Kitchen display mode** — a full-screen view of pending orders designed for a kitchen tablet (large text, auto-refresh, order aging indicators)

---

## 5. CLAUDE.md Rewrite — Research & Recommendations

### 5.1 What the research says

I researched current (2025-2026) best practices from Anthropic's official guidance, community templates, and real-world open-source projects. Key findings:

**Length:** The consensus is 50-200 lines. Anthropic says adherence drops from ~94% at 50 lines to ~71% at 400 lines. Your current CLAUDE.md is 156 lines — the length is fine, the content needs restructuring.

**What belongs in CLAUDE.md (facts Claude should always hold):**
- Build/test/lint commands
- Architecture overview and directory layout
- Non-obvious conventions
- Project-specific gotchas
- Constraints and rules

**What does NOT belong in CLAUDE.md:**
- Long procedures (→ skills in `.claude/skills/`)
- Deterministic enforcement (→ hooks in `settings.json`)
- File-specific rules (→ `.claude/rules/` with `paths:` frontmatter)
- Personal preferences (→ `~/.claude/CLAUDE.md`)

**Modern patterns:**
- `@import` syntax to keep the main file lean: `See @docs/api-patterns.md for API conventions`
- Path-scoped rules in `.claude/rules/` for cross-cutting concerns
- Subagents in `.claude/agents/` for isolated side tasks
- Skills in `.claude/skills/` for procedural workflows (deploy, review, etc.)

### 5.2 What's wrong with your current CLAUDE.md

| Problem | Why it matters |
|---------|---------------|
| **Branching strategy is half the file** | Once you deploy from master, ~40% of the file is obsolete. The detailed rules about what belongs on which branch, merge strategy, etc. — all gone. |
| **Commit workflow is a 70-line section** | Describes a two-layer gate that isn't actually wired up (commit-guard.sh not in settings.json). Once you remove the hooks, this entire section is obsolete. |
| **"No execution without approval" rule** | This is very restrictive. It means Claude can't even run `pytest` without asking. Consider scoping this: "Ask before commits, deployments, and destructive actions. Tests and linting are always allowed." |
| **"Do not overfit tests" is vague** | Good principle but no actionable guidance. What does overfitting look like in this codebase? |
| **Missing sections** | No commands section (how to run, test, lint). No architecture overview (what each file does). No gotchas section (the issues found in this audit). |
| **Too many hard rules, not enough context** | The file is mostly "don't do X" rules. It should be mostly "here's how the project works" context. |

### 5.3 Recommended structure for your new CLAUDE.md

```markdown
# Project: OrderProcessingAgent

[One-liner: what it is, tech stack]

## Commands
- python main.py cli          # Interactive CLI
- python main.py dashboard    # Admin dashboard (port 8081)
- python -m pytest tests/ -q  # Run all tests
- python tests/test_integration.py  # Integration tests

## Architecture
[5-10 lines: what each key file does. process_turn() is the universal interface.]

## Conventions
- Type hints on all functions (Python 3.10+ syntax: str | None, list[dict])
- Pydantic v2 for all data models
- Tools use @tool decorator — type hints drive JSON Schema generation
- State machine: BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED

## Rules
- Discuss before planning architectural changes
- Ask before commits, deployments, destructive actions
- Tests and linting always allowed without asking
- When a bug escapes tests, add a test that would have caught it
- Clean up plan docs after implementation (delete or merge into component doc)

## Gotchas
- [Key issues from this audit — things Claude would otherwise miss]
- session_id is sanitized phone number — repeat orders from same phone overwrite
- Menu edits via dashboard don't hot-reload — restart required
- printer_agent expects /api/ prefix — dashboard doesn't serve those routes
- Token leaks via ?token= query param in dashboard URLs
- payment.py and expand_deal() are dead code on master

## Key Files
[Quick reference: what each important file does]
```

### 5.4 What to extract from CLAUDE.md

| Move this... | To here... |
|--------------|------------|
| Commit workflow procedure | `.claude/skills/commit.md` (a skill) |
| Branching rules (if any remain) | `.claude/rules/branching.md` with `paths:` for relevant files |
| Review checklist | `.claude/skills/review.md` |

### 5.5 Recommendation

Rewrite CLAUDE.md to be ~80-120 lines: commands, architecture overview, conventions, rules, and gotchas. Extract procedures to skills. Delete the commit workflow section entirely (hooks are being removed). Add a gotchas section sourced from this audit — this is the highest-value part, because it prevents Claude from making mistakes it can't infer from the code.

---

## 6. General Customer Service Bot — Feasibility

### 6.1 Short answer

**Partially feasible, but the architecture is heavily order-shaped.** The system can be adapted, but it's not a drop-in general-purpose bot. You'd need to refactor the core abstraction.

### 6.2 What's generic (good)

| Component | Why it's reusable |
|-----------|-------------------|
| `process_turn(text) -> text` | The universal interface is channel-agnostic. Works for any text-in/text-out bot. |
| `session_router.py` | Maps identity (phone, email, user ID) to persistent sessions. Works for any conversation. |
| `llm_client.py` | Generic DeepSeek/OpenAI wrapper with retry logic. Completely reusable. |
| `db.py` | Generic session/order persistence. The `SessionRow` schema (conversation JSON, state, metadata) works for any bot. |
| `session.py` / `OrderSession` | Could be renamed `ConversationSession` — the state machine, conversation history, and cart are order-specific, but the pattern is generic. |
| `config.py` / `restaurant.py` | Multi-tenant by default. "Restaurant" could become "Business" or "Tenant." |
| `dashboard.py` | The admin dashboard pattern (list entities, view details, edit config) works for any domain. |

### 6.3 What's order-specific (needs refactoring)

| Component | Why it's coupled | Effort to generalize |
|-----------|-----------------|---------------------|
| `models.py` | `OrderState` enum, `CartItem`, `CartTopping`, `OrderType` — all order-domain concepts | **Medium** — make state machine pluggable |
| `tools.py` | All 7 tools are order operations (add_to_cart, confirm_order, etc.) | **High** — tools need to be domain-pluggable |
| `prompts.py` | System prompt hardcodes "Mario's Pizzeria," "order," menu hints | **Low** — make it template-driven per tenant |
| `pricing.py` | Entirely about item pricing, toppings, delivery fees | **High** — domain-specific math |
| `catalogue.py` | Menu/products/deals — restaurant-specific | **High** — domain-specific data model |
| `agent_loop.py` | `process_turn` signature requires `catalogue` and `pricing` even for sessions that don't need them | **Low** — make them optional |
| `payment.py` | Stripe checkout — payment-specific, but could be a generic "action" | **Medium** — already dead code on master |

### 6.4 What a refactor would look like

To make this a general customer service bot, you'd need:

1. **Pluggable domain module** — Instead of hardcoding `catalogue` + `pricing` + order-tools, define a `Domain` protocol:
   ```python
   class Domain(Protocol):
       tools: list[Callable]          # available @tool functions
       system_prompt: str             # domain-specific instructions
       initial_state: str             # e.g., "BUILDING" vs "TRIAGING"
       transitions: dict[str, list]   # valid state transitions
   ```
   Then `OrderDomain`, `SupportDomain`, `FAQDomain` implement it.

2. **Make `process_turn` domain-aware** — Pass a `Domain` instead of `catalogue` + `pricing`. The agent loop stays the same.

3. **Generalize the session state machine** — Replace `OrderState` enum with a string-based state + domain-provided transition map. The session model already has `state: OrderState` — make it `state: str`.

4. **Generalize tools** — The `@tool` decorator and JSON Schema generation is already generic. You just need different tool functions per domain.

5. **Multi-domain routing** — `session_router.py` already routes by `(tenant_id, user_id)`. Add a `domain` field to the tenant config so the same bot can handle ordering AND support depending on context.

### 6.5 Estimated effort

| Scope | Effort |
|-------|--------|
| Make `process_turn` domain-agnostic (protocol, optional catalogue/pricing) | 1-2 days |
| Extract order domain into a pluggable module | 2-3 days |
| Add a second domain (e.g., FAQ/support) as proof of concept | 1-2 days |
| Generalize dashboard to show domain-agnostic data | 1-2 days |
| **Total to support multiple domains** | **1-2 weeks** |

### 6.6 Recommendation

**Don't generalize prematurely.** The system is a very good order bot. It does that job well. Generalizing it before you have a concrete second use case will produce an abstraction that fits nothing well. Wait until you have a real second domain (e.g., "I want this same bot to handle support tickets for Mario's") — then refactor with a concrete target. The architecture is clean enough that this won't be a rewrite, it'll be an extraction.

---

## 7. Action Items — Prioritized

### Immediate (bugs that affect users)

- [ ] **1. Fix token leakage** — Move dashboard auth from query param to cookie or header-only
- [ ] **2. Fix `/sessions` endpoint** — Query sessions, not orders
- [ ] **3. Fix address display** in `order_detail.html` — read from `customer.address`
- [ ] **4. Add missing deps to `requirements.txt`** — fastapi, uvicorn, jinja2, requests

### Short-term (stability)

- [ ] **5. Merge `dashboard-integration` → `master`** — deploy from master going forward
- [ ] **6. Remove git hooks** — unset `core.hooksPath`, delete `.githooks/` and `.claude/hooks/`
- [ ] **7. Replace `assert` with proper error handling** in `dashboard.py`
- [ ] **8. Fix menu hot-reload** — reload Catalogue after dashboard edits, or force page refresh
- [ ] **9. Rewrite CLAUDE.md** — 80-120 lines, new structure, current gotchas

### Medium-term (improvements)

- [ ] **10. Add API prefix to printer endpoints** or update printer_agent to match dashboard routes
- [ ] **11. Convert integration test to pytest** — proper fixtures, assertions, parallelizable
- [ ] **12. Add order search** to dashboard (by name, phone, ID)
- [ ] **13. Add pagination** to orders/sessions lists
- [ ] **14. Fix session/order ID collision** — use UUIDs for order IDs, not phone numbers
- [ ] **15. Remove or wire up dead code** — either delete `payment.py` and `expand_deal()` or integrate them

### Long-term (features)

- [ ] **16. Real-time order notifications** (SSE) in dashboard
- [ ] **17. Dashboard analytics** — revenue, popular items, peak hours
- [ ] **18. Kitchen display mode** — full-screen auto-refresh view for tablets
- [ ] **19. Generalize to multi-domain** — only when you have a concrete second use case
- [ ] **20. Docker/CI setup** — Dockerfile, docker-compose, GitHub Actions for tests

---

## A. Appendix: Files That Need Changing

### When removing hooks

| File | Action |
|------|--------|
| `.githooks/pre-commit` | Delete |
| `.claude/hooks/commit-guard.sh` | Delete |
| `.claude/settings.json` | No change needed (already `{"hooks":{}}`) |
| `git config core.hooksPath` | Unset: `git config --unset core.hooksPath` |
| `.gitignore` | Remove `.review-approved` entry (no longer used) |
| `CLAUDE.md` | Remove "Commit workflow" section, "Branching strategy" section |

### When merging to master

| File | Action |
|------|--------|
| `CLAUDE.md` | Rewrite (see §5.3) |
| `requirements.txt` | Add: `fastapi`, `uvicorn`, `jinja2`, `requests` |
| `main.py:520-528` | Remove branch-switch warning in `_run_server` |
| `config.py` | Remove "dormant defaults" comments on channel fields |
| `docs/` | Delete stale integration plans, update remaining docs |
| `dashboard-integration` branch | Delete after merge |

---

> **Next step:** Review this report, tell me which items you want to tackle first, and I'll implement them. No code changes have been made — this is analysis only.

# CLAUDE.md — Project rules and AI agent guidelines

## Project

Multi-tenant order processing agent — LLM-powered chatbot for restaurants.
Python + DeepSeek API + Pydantic v2 + SQLite (Peewee ORM). Channels: CLI,
Twilio WhatsApp, web dashboard. All code lives on `master`.

## Commands

```bash
python main.py cli                          # Interactive CLI session
python main.py dashboard --port 8081        # Admin dashboard
python main.py server                       # Twilio webhook server
python main.py manage-menu <id>             # Interactive menu editor
python main.py restaurant add <id>          # Add a restaurant
python main.py restaurant edit <id>         # Edit restaurant config
python -m pytest tests/ -q                  # Full test suite
python tests/test_integration.py            # Integration tests
```

## How you should work

- **Discuss before planning.** When I ask about something, discuss it with me
  before jumping to architecture or code. Do not assume my intent.
- **Plans go in docs/.** When I say "write a plan," I mean a markdown file
  in `docs/`. Not the internal Claude plan file. If you are in plan mode
  and need to write files outside docs/, ask me to exit plan mode first.
- **Clean up plan docs after implementation.** Once a plan is fully
  implemented, delete it or merge it into the relevant component doc.
  Don't leave stale implementation plans in `docs/`.
- **Ask before commits, deployments, and destructive actions.** Tests,
  linting, and read-only exploration are always allowed without asking.
- **Challenge me.** If you think I'm wrong or there is a better approach,
  say so. I value your expertise.
- **Do not overfit tests.** Tests should verify behavior, not mirror
  implementation. If a test fails because the implementation changed
  legitimately, fix the test.
- **Tests are not enough.** Unit test mocks can't catch missing variable
  assignments, broken import chains, or undefined functions. After any
  change touching function signatures, imports, or the call chain, run
  live verification: mock the LLM, call the actual entry points, and make
  sure they don't crash.
- **When a bug escapes the tests, add a test that would have caught it.**
  Don't just fix the bug and move on. Ask why the tests didn't catch it
  and close the gap. The integration test suite (`tests/test_integration.py`)
  exists specifically to catch issues that unit tests miss — use it and
  extend it.

## Architecture

`process_turn(user_message: str) -> str` is the universal interface. Every
channel wraps this — CLI, WhatsApp, web, voice, etc.

| File | Purpose |
|------|---------|
| `agent_loop.py` | Core orchestration — LLM ↔ tools loop, state transitions, rollback |
| `models.py` | Pydantic v2 domain models — OrderState, CartItem, all tool result types |
| `session.py` | OrderSession — mutable order state, conversation history, DB persistence |
| `session_router.py` | Maps (restaurant_id, phone_number) → active session; auto-replaces terminal |
| `tools.py` | @tool decorator + 7 tools (add_to_cart, remove_from_cart, update_item, view_cart, set_customer_info, request_review, confirm_order, cancel_order) |
| `prompts.py` | System prompt builder — restaurant name, cart, customer, menu hints |
| `catalogue.py` | Menu loading, fuzzy product matching, deal definitions, pricing hints |
| `pricing.py` | Python-owned pricing engine — computes all totals (LLM never touches math) |
| `llm_client.py` | DeepSeek API wrapper (OpenAI SDK) — retry logic, tool definition builder |
| `config.py` | All env vars via AppConfig dataclass — loaded from .env |
| `restaurant.py` | Multi-tenant registry — per-tenant Catalogue + PricingEngine |
| `db.py` | SQLite persistence via Peewee ORM — SessionRow + OrderRow, legacy migration |
| `dashboard.py` | FastAPI read-only admin dashboard (port 8081) — Jinja2 templates |
| `main.py` | CLI entry point — argparse subcommands for all modes |
| `menu_manager.py` | Atomic menu editing — set_price, out_of_stock, in_stock, describe |
| `payment.py` | Stripe Checkout session creation + webhook signature verification |
| `printer.py` | ESC/POS thermal printer formatter — bytes mode + HTML preview mode |

## Architecture principles

- **Python owns all state transitions and math.** The LLM can suggest, but
  only our code changes state and computes prices.
- **Session = order, not person.** One session per order. Multiple orders
  from the same person are separate sessions. Terminal sessions (CANCELLED,
  COMPLETED) are dead and replaced on next contact.
- **Session identity is (restaurant_id, phone_number).** Phone sanitized
  to digits is the session_id; restaurant_id scopes the namespace.
  Same phone can order from multiple restaurants.
- **State machine:** BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
  (CANCELLED from any active state).
- **LLM never sees raw tool results delivered to users.** Tool calls execute
  silently, then the LLM responds naturally based on result fields.
- **Tools use the @tool decorator.** Type hints on the function signature
  auto-generate the JSON Schema the LLM receives. `TOOLS_BY_STATE` controls
  which tools are available in each state.

## Gotchas

- **Session ID = sanitized phone number.** Two orders from the same phone
  overwrite each other in the DB — OrderRow uses `replace`. For production,
  generate unique order IDs separate from session IDs.
- **Dashboard menu edits don't hot-reload.** Catalogue/PricingEngine load at
  server startup. After editing a menu via dashboard, restart the server.
- **API token leaks via dashboard URLs.** Token is passed as `?token=xxx`
  query parameter — appears in browser history, server logs, Referer headers.
- **`/sessions` dashboard page queries the orders table**, not sessions —
  sessions and orders are conflated there.
- **Printer agent expects `/api/`-prefixed routes** that don't exist on the
  dashboard server. Printer agent is non-functional until those endpoints
  are added or the agent is updated.
- **`payment.py` and `catalogue.expand_deal()` are dead code** — not called
  by any reachable path on master. Either integrate them or delete them.
- **`requirements.txt` is incomplete.** `fastapi`, `uvicorn`, `jinja2`, and
  `requests` are imported at runtime but not listed as dependencies.
- **`assert` is used for runtime validation in `dashboard.py`.** If Python
  runs with `-O`, asserts are stripped → crashes.
- **No `browse_menu` tool exists.** The LLM has no way to programmatically
  explore the menu — it discovers items only through `add_to_cart` failures.
- **`set_customer_info` silently ignores invalid `order_type` values.**
  `except ValueError: pass` gives the caller no error feedback.

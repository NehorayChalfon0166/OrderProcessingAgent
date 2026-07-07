# 🍕 Order Processing Agent

An AI-powered restaurant order processing agent using tool-calling LLMs.
Python owns all state transitions and math — the LLM suggests actions,
our code executes them.

Supports **multiple restaurants** from a single deployment. Each restaurant
gets its own menu, WhatsApp number, and isolated sessions/orders.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your DEEPSEEK_API_KEY to .env
python main.py cli
```

## Dashboard

```bash
python main.py dashboard --port 8081
# Open http://localhost:8081/?token=<API_TOKEN>
```

Pages: Overview (live metrics), Orders (search + pagination), Sessions,
Restaurants, Menus (full editor), Analytics, Kitchen display, Audit log.

## WhatsApp Server

```bash
ngrok http 8080                          # Expose localhost
python main.py server --port 8080        # Twilio webhook
# Set Twilio webhook: https://<ngrok>/whatsapp/webhook
```

## Architecture

```
Customer → WhatsApp → Twilio → server.py → agent_loop.py (process_turn)
                                              ├── LLMClient (DeepSeek API)
                                              ├── RestaurantRegistry
                                              │    ├── Catalogue (menu + fuzzy matching)
                                              │    └── PricingEngine (Python-owned math)
                                              └── Tools (add_to_cart, confirm_order, ...)
                                              ↓
                                         OrderSession (SQLite via Peewee)
                                              ↓
                                    Save order → Notify restaurant → Print ticket
```

**State Flow:** `BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED` (CANCELLED from any)

## Project Structure

```
├── main.py              # CLI entry point (cli, server, dashboard subcommands)
├── agent_loop.py        # LLM ↔ tools loop, state transitions, rollback
├── server.py            # FastAPI Twilio WhatsApp webhook
├── dashboard.py         # FastAPI admin dashboard (port 8081)
├── twilio_client.py     # Twilio REST API wrapper
├── session.py           # OrderSession model + persistence
├── session_router.py    # (restaurant_id, phone) → session mapping
├── restaurant.py        # Multi-tenant restaurant registry
├── restaurants.json     # Restaurant configurations
├── menus/               # One menu JSON per restaurant
├── llm_client.py        # DeepSeek API wrapper (OpenAI SDK)
├── prompts.py           # System prompt builder
├── models.py            # Pydantic v2 domain models
├── tools.py             # @tool decorator + 7 order tools
├── catalogue.py         # Menu loading, fuzzy matching, deals
├── pricing.py           # Python-owned pricing engine
├── config.py            # Environment-variable configuration
├── db.py                # SQLite persistence (Peewee ORM)
├── menu_manager.py      # Atomic menu editing
├── payment.py           # Stripe checkout (dormant — not in Israel)
├── printer.py           # ESC/POS thermal printer formatter
├── printer_agent/       # Standalone printer polling client
├── utils.py             # Shared utilities (atomic write, order IDs)
├── dashboard_static/    # CSS + JS for dashboard
├── dashboard_templates/ # Jinja2 HTML templates
├── docs/                # Architecture + deployment documentation
├── tests/               # 395 tests (pytest + E2E)
├── orders/              # Completed order JSONs
└── sessions/            # Legacy JSON sessions (migrated to SQLite)
```

## Key Design Decisions

1. **Python owns state and math** — LLM suggests, Python validates and executes
2. **Tool-calling** — LLM calls typed Python functions, results are Pydantic models
3. **Loop-based agent** — LLM with tools → execute → repeat until clean text
4. **Session = order** — one session per order, keyed by (restaurant, phone)
5. **Menu is source of truth** — items validated against per-restaurant catalogue
6. **Multi-tenant** — restaurants.json drives everything, isolated sessions/orders
7. **Multilingual** — agent auto-matches customer's language
8. **All code on master** — single branch, Docker + CI ready

## Adding a Restaurant

1. Create a menu file in `menus/{id}.json`
2. Add to `restaurants.json` with Twilio phone and owner phone
3. Restart server — or use the dashboard to add restaurants live

## Deployment

See `docs/deployment.md` for Docker, environment variables, Twilio setup,
and monitoring.

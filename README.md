# 🍕 Order Processing Agent

An AI-powered restaurant order processing agent using tool-calling LLMs.
Python owns all state transitions and math — the LLM suggests actions,
our code executes them.

Supports **multiple restaurants** from a single deployment. Each restaurant
gets its own menu, its own WhatsApp number, and isolated sessions/orders.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your DEEPSEEK_API_KEY to .env
python main.py cli
```

### Adding a Restaurant

1. **Create a menu file** in `menus/{restaurant_id}.json`. Copy
   `menus/marios_pizzeria.json` as a template and edit products, prices,
   and deals.

2. **Register it** in `restaurants.json`:
   ```json
   {
     "restaurants": {
       "your_restaurant_id": {
         "name": "Your Restaurant Name",
         "menu_path": "menus/your_restaurant_id.json",
         "twilio_phone": "+1234567890"
       }
     }
   }
   ```

3. **Restart the server.** The registry picks it up automatically. Each
   restaurant needs its own Twilio WhatsApp number configured to POST
   webhooks to your server.

### Multilingual Responses

The agent automatically responds in whatever language the customer uses.
A Hebrew-speaking customer gets Hebrew replies; an English speaker gets
English. Product names stay in English (they come from the menu), but
all conversation happens in the customer's language. No configuration
needed — it's built into the system prompt.

## Architecture

```
User ↔ CLI / Twilio Webhook → agent_loop.py (process_turn)
         ↕
    OrderSession (session.py)         ← state machine + persistence
     ├── LLMClient (llm_client.py)    ← DeepSeek API wrapper
     ├── RestaurantRegistry           ← per-restaurant catalogue + pricing
     │    └── Catalogue (catalogue.py) ← menu loading + fuzzy matching
     │    └── PricingEngine (pricing.py) ← Python-owned math (never LLM)
     └── Tools (tools.py)             ← LLM-callable actions, state-gated
```

**State Flow:**
```
BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
    ↓         ↓           ↓
 CANCELLED CANCELLED   CANCELLED
```

## Project Structure

```
├── main.py              # CLI entry point (cli + server subcommands)
├── agent_loop.py        # Loop-based agent orchestration
├── session.py           # OrderSession model + JSON persistence
├── session_router.py    # (restaurant_id, phone) → session mapping
├── restaurant.py        # Multi-tenant restaurant registry
├── restaurants.json     # Restaurant deployment configuration
├── menus/               # One menu JSON file per restaurant
├── llm_client.py        # DeepSeek API wrapper (OpenAI SDK)
├── prompts.py           # System prompt builder (multilingual-aware)
├── models.py            # Pydantic v2 domain models
├── tools.py             # @tool decorator + tool implementations
├── catalogue.py         # Menu loading, fuzzy matching, deals
├── pricing.py           # Python-owned pricing engine
├── config.py            # Env-var configuration
├── db.py                # SQLite persistence (Peewee ORM)
├── payment.py           # Stripe checkout + webhook verification
├── printer.py           # ESC/POS thermal printer formatting
├── docs/                # Component + architecture documentation
├── tests/               # Full pytest suite (197 tests)
├── menus/               # One menu JSON file per restaurant
└── orders/              # Completed order JSONs (per-restaurant subdirs)
```

**Integration branch** (`twilio-integration` — I/O layer, rebases on master):
```
├── server.py            # FastAPI Twilio WhatsApp webhook
├── twilio_client.py     # Twilio REST API wrapper
└── .githooks/           # Pre-commit hook enforcing branch rules
```

## Key Design Decisions

1. **Python owns state and math** — LLM suggests, Python validates and executes
2. **Tool-calling, not JSON parsing** — LLM calls typed Python functions
3. **Loop-based agent** — LLM with tools → execute → repeat until clean text
4. **Session = order** — one session per order, keyed by (restaurant, phone)
5. **Menu is the source of truth** — items validated against per-restaurant menu
6. **Multi-tenant** — restaurants.json drives everything, isolated sessions/orders
7. **Multilingual** — agent auto-matches customer's language
8. **Single provider** — DeepSeek via OpenAI SDK. Add providers in `config.py`

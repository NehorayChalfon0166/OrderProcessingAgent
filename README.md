# 🍕 Order Processing Agent

An AI-powered restaurant order processing agent using tool-calling LLMs.
Python owns all state transitions and math — the LLM suggests actions, our code executes them.

## Architecture

```
User ↔ CLI / Twilio Webhook → agent_loop.py (process_turn)
         ↕
    OrderSession (session.py)         ← state machine + persistence
     ├── LLMClient (llm_client.py)    ← DeepSeek API wrapper
     ├── Catalogue (catalogue.py)     ← menu loading + validation
     ├── PricingEngine (pricing.py)   ← Python-owned math (never LLM)
     └── Tools (tools.py)             ← LLM-callable actions, state-gated
```

**State Flow:**
```
BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
    ↓         ↓           ↓
 CANCELLED CANCELLED   CANCELLED
```

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your DEEPSEEK_API_KEY to .env
python main.py
```

## Project Structure

```
├── main.py              # CLI entry point
├── agent_loop.py        # Loop-based agent orchestration
├── session.py           # OrderSession model + JSON persistence
├── session_router.py    # Phone-number → session mapping (server mode)
├── llm_client.py        # DeepSeek API wrapper (OpenAI SDK)
├── prompts.py           # System prompt builder
├── models.py            # Pydantic v2 domain models
├── tools.py             # @tool decorator + tool implementations
├── catalogue.py         # Menu loading, fuzzy matching, deals
├── pricing.py           # Python-owned pricing engine
├── config.py            # Env-var configuration
├── menu.json            # Menu data (single source of truth)
├── docs/                # Component + architecture documentation
├── tests/               # Full pytest suite
└── orders/              # Completed order JSONs
```

## Key Design Decisions

1. **Python owns state and math** — LLM suggests, Python validates and executes
2. **Tool-calling, not JSON parsing** — LLM calls typed Python functions via OpenAI function-calling
3. **Loop-based agent** — LLM with tools → execute → repeat until clean text
4. **Session = order** — one session per order, keyed by phone number
5. **Menu is the source of truth** — items validated against `menu.json`
6. **Single provider** — DeepSeek via OpenAI SDK. Add providers in `config.py`

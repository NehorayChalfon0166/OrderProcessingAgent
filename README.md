# 🍕 Order Processing Agent

A deterministic, state-machine-driven AI agent for processing restaurant orders via CLI. Built with Python, Pydantic, and any OpenAI-compatible LLM.

## Architecture

```
User ↔ CLI (main.py)
         ↕
    OrderSession (state_machine.py)     ← core state machine
     ├── LLMClient (llm_client.py)      ← provider-agnostic LLM calls
     ├── MenuManager (menu_manager.py)  ← menu loading & item validation
     └── PricingEngine (pricing.py)     ← Python-owned math (never LLM)
```

**State Flow:**
```
GREETING → ASSEMBLY ↔ DETAILS ↔ VERIFICATION → CONFIRMED
                ↓           ↓            ↓
             CANCELLED   CANCELLED    CANCELLED
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get a free API key

Pick any provider — switch anytime by changing one env var:

| Provider | Free Tier | Get Key |
|----------|-----------|---------|
| **Groq** (default) | Very generous | [console.groq.com](https://console.groq.com) |
| **Google Gemini** | Most generous | [aistudio.google.com](https://aistudio.google.com/apikey) |
| **OpenRouter** | Free models available | [openrouter.ai](https://openrouter.ai) |
| **Mistral** | Free tier | [console.mistral.ai](https://console.mistral.ai) |
| **Ollama** | Fully local, no key needed | [ollama.com](https://ollama.com) |

### 3. Configure

```bash
cp .env.example .env
# Edit .env and add your API key
```

### 4. Run

```bash
python main.py
```

Use `--debug` to see raw LLM requests/responses:
```bash
python main.py --debug
```

## Switching LLM Providers

Just change the env vars in `.env`:

```bash
# Use Groq (default)
LLM_PROVIDER=groq
LLM_API_KEY=gsk_...

# Use Google Gemini
LLM_PROVIDER=gemini
LLM_API_KEY=AI...

# Use local Ollama (no API key needed)
LLM_PROVIDER=ollama
# Make sure ollama is running: ollama serve
```

## Project Structure

```
OrderProcessingAgent/
├── main.py            # CLI entry point & interactive loop
├── state_machine.py   # Core state machine (OrderSession)
├── llm_client.py      # Provider-agnostic LLM client
├── prompts.py         # System prompt templates per state
├── models.py          # Pydantic v2 data models
├── menu_manager.py    # Menu loading, validation, fuzzy matching
├── pricing.py         # Python-owned pricing engine
├── config.py          # Configuration & provider presets
├── menu.json          # Menu data (single source of truth)
├── .env.example       # Environment variable template
├── requirements.txt   # Python dependencies
└── orders/            # Completed orders dumped here (auto-created)
```

## Key Design Decisions

1. **Python owns state transitions** — LLM *suggests* actions, Python *validates* them
2. **Python owns all math** — LLM never computes prices, totals, or fees
3. **Menu is the source of truth** — Items validated against `menu.json`, not LLM memory
4. **Provider-agnostic** — Uses OpenAI SDK with swappable base URLs
5. **Graceful JSON parsing** — 4-tier fallback handles even badly formatted LLM output

## Customizing the Menu

Edit `menu.json` directly — no code changes needed. The menu supports:
- Categories with items (pizzas, sides, drinks, desserts)
- Sized items (small/medium/large) and flat-price items
- Toppings with per-topping pricing
- Deals and specials
- Delivery fee and minimum order amount

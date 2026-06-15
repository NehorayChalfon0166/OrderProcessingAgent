# Build Order

Status: **SETTLED**

Dependency-ordered sequence for implementing files. Each step produces a file
that can be imported and tested before moving to the next.

## Phase 1: Foundation (no LLM dependency)

### Step 1: `models.py`
**Depends on:** nothing (only pydantic)
**Verification:** `python -c "from models import OrderState, CartItem, CustomerInfo"`

### Step 2: `config.py`
**Depends on:** nothing (only dotenv)
**Verification:** `python -c "from config import AppConfig; c = AppConfig.from_env()"`

### Step 3: `catalogue.py`
**Depends on:** models, menu.json
**Verification:** load menu, test `find_product()`, `resolve_size()`, `get_hints()`

### Step 4: `pricing.py`
**Depends on:** models (CartItem, OrderType)
**Verification:** create a CartItem, call `price_item()`, verify `base_price` and `line_total` set

## Phase 2: Agent Core (no LLM dependency)

### Step 5: `session.py`
**Depends on:** models
**Verification:** create session, append messages, save to JSON, load back

### Step 6: `tools.py` + `@tool` decorator
**Depends on:** models, catalogue, pricing, session
**Verification:** instantiate catalogue + pricing + session, call each tool function directly (no LLM), verify cart mutations and return types

## Phase 3: LLM Integration

### Step 7: `prompts.py`
**Depends on:** models, session
**Verification:** build prompt with test session, verify it's a string under 500 chars

### Step 8: `llm_client.py`
**Depends on:** config, models, session (for message conversion), @tool decorator
**Verification:** initialize client, call `chat("Hello")` — verify non-tool response works
**Verification:** call `build_tool_definitions([add_to_cart, ...])` — verify correct JSON Schema

## Phase 4: Orchestration

### Step 9: `agent_loop.py`
**Depends on:** all of the above
**Verification:** create session, call `process_turn(session, "Hi", ...)` — verify greeting

## Phase 5: CLI

### Step 10: `main.py`
**Depends on:** agent_loop
**Verification:** `python main.py` — full interactive session

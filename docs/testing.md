# Testing Strategy

Status: **SETTLED**

## What's Unit-Testable Without an LLM

~70% of the codebase is deterministic pure logic. These components can be tested
with standard pytest, no mocking needed beyond test fixtures.

### `models.py`
- **Enums:** `OrderState`, `OrderType` — verify values
- **CartItem:** create with required fields, verify defaults, verify `model_dump()`
- **CustomerInfo:** create empty, create partial, verify Optional fields
- **Tool result types:** create each with success=True and success=False, verify field presence

### `catalogue.py`
- **`find_product(name)`:** exact match, case-insensitive match, substring match, reverse substring match, no match
- **`find_topping(name)`:** same fuzzy strategies, by-ID fallback
- **`resolve_size(product, size)`:** valid size → returned, invalid size → issue, None → default
- **`resolve_toppings(product, names)`:** valid toppings → CartTopping list, unknown topping → issue, topping not available for product → issue
- **`get_hints()`:** returns string, length under 300 chars, includes category names
- **`expand_deal(deal)`:** returns correct number of CartItems, each has expected product_id and missing_options
- **Edge cases:** empty topping list, product with no sizes (flat price), product with no toppings allowed

### `pricing.py`
- **`get_item_base_price(id, size)`:** sized item → correct price per size, flat-price item → correct price, unknown id → ValueError
- **`price_item(cart_item)`:** sets base_price and line_total = (base_price + toppings) * quantity
- **`compute_totals(items, order_type)`:** subtotal = sum of line_totals, delivery_fee for DELIVERY, 0 for PICKUP
- **Edge cases:** zero quantity (should error), negative price (should error), empty items list

### `session.py`
- **Create session:** has id, state=BUILDING, empty cart, empty conversation
- **Append messages:** UserMessage, AssistantMessage, ToolResult — all append correctly
- **Save/load:** save session to temp JSON, load back, verify all fields match
- **`_pending_transition`:** not serialized in model_dump
- **`payment_method`:** serialized, survives roundtrip

### `tools.py` (with mock session/catalogue/pricing)
- **`add_to_cart`:** found product → item in cart, not found → suggestions, deal → expanded items
- **`remove_from_cart`:** single match → removed, multiple matches → matches list, no match → success=False
- **`update_item`:** quantity change → re-priced, size change → new size + re-priced
- **`view_cart`:** empty cart, cart with items, subtotal correct
- **`set_customer_info`:** partial fill, override, missing_required calculation
- **`request_review`:** cart empty → issues, no name → issues, all good → _transition set
- **`confirm_order`:** cash (default) → COMPLETED, link → PAYMENT_PENDING, sets payment_method
- **`cancel_order`:** sets _transition=CANCELLED

### `prompts.py`
- **`build_system_prompt()`:** includes restaurant name, state, cart summary, hints
- **Empty cart:** shows "(empty)"
- **No customer info:** shows "(not yet provided)"
- **Result:** string under 500 chars

### `llm_client.py` (without API calls)
- **`messages_to_openai()`:** USER → {"role": "user", ...}, ASSISTANT with tool_calls → correct format, TOOL → {"role": "tool", ...}
- **`build_tool_definitions()`:** generates valid JSON Schema with name, description, parameters

### `agent_loop.py` (without LLM — mock LLM responses)
- **`apply_transition()`:** REVIEW preconditions (empty cart → blocked, no name → blocked, delivery no address → blocked), PAYMENT_PENDING, COMPLETED, CANCEL always wins
- **`process_turn()`:** greeting (no tools), add item (tool → respond), chained tools (browse → add → respond), max iterations fallback, empty text fallback, state change mid-loop

### `@tool` decorator
- Stores __tool_name__, __tool_description__, __tool_schema__
- Required params appear in required[], optional params do not
- Injected params (session, catalogue, pricing) excluded from schema

## What Needs an LLM (Integration / E2E)

- Full conversation flow: greeting → add items → review → confirm
- Edge case: LLM calls wrong tool for state (should be impossible if tool filtering works)
- Edge case: LLM hallucinates product name → tool returns suggestions → LLM adapts
- Edge case: user cancels mid-order
- Payment: cash vs link flow, PAYMENT_PENDING → COMPLETED

These are tested manually via `python main.py cli` or with recorded LLM responses.

## Test File Organization

```
tests/
  test_models.py
  test_catalogue.py
  test_pricing.py
  test_session.py
  test_tools.py          # uses fixtures, no LLM
  test_prompts.py
  test_llm_client.py     # message conversion + tool defs, no API calls
  test_agent_loop.py     # transition logic + loop pattern, no LLM
  test_session_router.py # phone-number session lookup
  test_config.py         # env var loading
  test_decorator.py
  conftest.py            # shared fixtures
```

## Fixtures (in `conftest.py`)

- `sample_catalogue` — Catalogue loaded from menu.json (or a minimal test menu)
- `sample_pricing` — PricingEngine with test menu data
- `empty_session` — fresh OrderSession with BUILDING state
- `session_with_items` — OrderSession with 2 items in cart
- `session_with_customer` — OrderSession with items + full customer info

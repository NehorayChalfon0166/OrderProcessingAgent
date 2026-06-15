# Component: `prompts.py`

Status: **SETTLED**

Builds the system prompt for each LLM call. Radically simpler than v1 — no JSON schema,
no full menu, no per-state instruction blocks.

## Design Rationale

v1's prompt was ~1500 tokens: full JSON schema, full formatted menu, behavioral rules,
per-state instruction blocks. With tool calling, all of this collapses:

| v1 content | v2 replacement |
|---|---|
| JSON schema (40 lines) | Gone — tool calling handles structured output natively |
| Full menu (100+ lines) | `catalogue.get_hints()` — category names + 2-3 popular items, ~200 chars |
| Per-state instruction blocks | Tool availability IS the instruction — if `confirm_order` isn't in the tool list, the LLM can't confirm |
| Behavioral rules | Gone — tool results guide the LLM ("not found → suggest alternatives") |

## Prompt Template

```python
def build_system_prompt(session: OrderSession, hints: str) -> str:
    cart_summary = _format_cart(session.cart)
    customer_summary = _format_customer(session.customer)

    return f"""You are a friendly order-taking assistant for {restaurant_name}.

Current state: {session.state.value}
Use the available tools to help the customer. Do not invent menu items —
use add_to_cart and let the system validate the product.

Cart:
{cart_summary}

Customer:
{customer_summary}

Menu categories: {hints}
"""
```

~200-300 tokens total.

## Helper Functions

### `_format_cart(items: list[CartItem]) -> str`
```
1. Margherita Pizza (medium) — $12.99
   + Extra Cheese (+$1.50)
2. Caesar Salad — $7.49
   ...
Subtotal: $21.98
```
If empty: `"(empty)"`

### `_format_customer(info: CustomerInfo) -> str`
```
Name: John
Phone: 555-0123
Address: 123 Main St
Type: delivery
Missing: phone  ← only shown during BUILDING to guide the LLM
```
If empty: `"(not yet provided)"`

## TBD: Per-Instance Failure Guidance

If a specific model consistently mishandles tool results (e.g., ignoring `suggestions`
in `AddToCartResult`), add targeted instruction to this prompt. Not added preemptively.

## How the Prompt Reaches the LLM

1. `agent_loop.py` calls `build_system_prompt(session, catalogue.get_hints())`
2. Result goes into the `system` role message
3. Agent loop appends recent conversation history (from `session.conversation`)
4. Agent loop calls `llm_client.build_tool_definitions(state)` to get the tool
   schema matching the current state's available tools
5. Full payload: system prompt + conversation + tool definitions → LLM

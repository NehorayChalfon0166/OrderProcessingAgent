# Component: `prompts.py`

Status: **SETTLED — unified prompt**

Builds a single system prompt used for every iteration of the agent loop.
Radically simpler than v1 — no JSON schema, no full menu, no per-state blocks.

## Design Rationale

v1's prompt was ~1500 tokens: full JSON schema, full formatted menu, behavioral rules,
per-state instruction blocks. With tool calling, all of this collapses:

| v1 content | v2 replacement |
|---|---|
| JSON schema (40 lines) | Gone — tool calling handles structured output natively |
| Full menu (100+ lines) | `catalogue.get_hints()` — category names + 2-3 popular items, ~200 chars |
| Per-state instruction blocks | Tool availability IS the instruction — if `confirm_order` isn't in the tool list, the LLM can't confirm |
| Behavioral rules | Gone — tool results guide the LLM ("not found → suggest alternatives") |

## Unified Prompt

A single `build_system_prompt()` is used for every iteration of the loop.
There is no separate "call tools" vs "respond" prompt — the model always
has tools available and decides each iteration whether to call them or
respond to the customer.

```
You are a friendly, brief order-taking assistant for {restaurant_name}.
Current state: {state}

Use tools when you need to take action — add items, browse the menu,
check the cart, set customer info, confirm orders, etc.
When you call tools, you don't need to include text — you will see
the results and can respond after.
When you are ready to respond to the customer, keep it brief —
1 to 3 sentences.
Only say an order is confirmed when the state IS "completed".
Let the system validate products. Do not guess the menu.

Cart: ...
Customer: ...
Menu (reference when asked, do not list in greeting): ...
```

~200-300 tokens total.

No mention of `<invoke>`, tool-call syntax, or XML tags. In the loop pattern,
the model always has access to the legitimate `tool_calls` channel — there's
no forbidden path it needs to hack around via text.

The unified `build_system_prompt` is the single entry point — the loop pattern
uses one prompt for all iterations.

## Helper Functions

### `_format_cart(session: OrderSession) -> str`
```
1. Margherita Pizza (medium) — $12.99
   + Extra Cheese (+$1.50)
2. Caesar Salad — $7.49
   ...
```
If empty: `"(empty)"`

### `_format_customer(info: CustomerInfo) -> str`
```
Name: John
Phone: 555-0123
Address: 123 Main St
Type: delivery
```
If empty: `"(not yet provided)"`

## How the Prompt Reaches the LLM

1. `agent_loop.py` calls `build_system_prompt(session, restaurant_name, catalogue.get_hints())`
2. Result goes into the `system` role message
3. Agent loop appends full conversation history (from `session.conversation`)
4. Agent loop calls `build_tool_definitions(tool_funcs)` to get the tool
   schema matching the current state's available tools
5. Full payload: system prompt + conversation + tool definitions → LLM
6. Same prompt used for every iteration — the loop handles convergence

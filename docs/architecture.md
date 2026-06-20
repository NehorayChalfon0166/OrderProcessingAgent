# Architecture — High-Level Plan

Status: **SETTLED**

## Context

Tool-calling architecture using DeepSeek API via OpenAI SDK. The LLM suggests
actions through function calls; Python owns all state transitions, pricing math,
and menu validation.

## State Machine

```
BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
   ↓         ↓           ↓
   └─────────┴───────────┴──→ CANCELLED (from any active state)
```

- **BUILDING:** Adding items, collecting customer info.
- **REVIEW:** Cart complete. User can confirm, modify, or cancel.
- **PAYMENT_PENDING:** Payment initiated (link method), waiting for webhook.
  Cash method skips this state — confirm_order goes directly to COMPLETED.
- **COMPLETED / CANCELLED:** Terminal states.

## Tools & State Availability

Only tools legal in the current state are sent to the LLM.

| Tool | BUILDING | REVIEW | PAYMENT_PENDING |
|---|---|---|---|
| add_to_cart | ✓ | ✓ | — |
| remove_from_cart | ✓ | ✓ | — |
| update_item | ✓ | ✓ | — |
| view_cart | ✓ | ✓ | ✓ |
| browse_menu | ✓ | ✓ | ✓ |
| set_customer_info | ✓ | ✓ | — |
| request_review | ✓ | — | — |
| confirm_order | — | ✓ | — |
| cancel_order | ✓ | ✓ | ✓ |

## Agent Loop

Loop-based: LLM with tools → execute → repeat until clean text (max 5 iterations).
Tools are always available — the model chains them naturally and converges when
it has nothing left to do. See `docs/agent_loop.md`.

## Payment

Dormant infrastructure on master. `confirm_order` accepts `payment_method`:
- `"cash"` (default) → COMPLETED directly
- `"link"` → PAYMENT_PENDING, waits for external webhook

See `docs/payment_architecture.md`.

## Key Decisions

- **Python owns state and math.** LLM suggests, Python validates and executes.
- **`process_turn()` is the universal interface.** String in, string out.
- **Session = order.** One session per order, keyed by phone number.
- **Tool calling, not JSON parsing.** LLM calls typed Python functions.
- **Menu discovery via hints + tool results.** Lightweight hints in prompt,
  full details (options, toppings, sizes) returned in tool results.

## Component Docs

| Component | Status | Doc |
|---|---|---|
| `models.py` | SETTLED | [models.md](models.md) |
| `catalogue.py` | SETTLED | [catalogue.md](catalogue.md) |
| `tools.py` | SETTLED | [tools.md](tools.md) |
| `session.py` | SETTLED | [session.md](session.md) |
| `prompts.py` | SETTLED | [prompts.md](prompts.md) |
| `llm_client.py` | SETTLED | [llm_client.md](llm_client.md) |
| `agent_loop.py` | SETTLED | [agent_loop.md](agent_loop.md) |
| `main.py` | SETTLED | CLI entry point |

## Subsystem Docs

| Doc | Purpose |
|---|---|
| [decorator.md](decorator.md) | `@tool` decorator design |
| [testing.md](testing.md) | Testing strategy |
| [payment_architecture.md](payment_architecture.md) | Payment flow design |
| [menu_schema.md](menu_schema.md) | Menu JSON reference schema |
| [multi_restaurant.md](multi_restaurant.md) | Multi-tenant architecture |
| [production_roadmap.md](production_roadmap.md) | Production component roadmap |

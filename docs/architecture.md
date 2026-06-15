# Architecture — High-Level Plan

## Context

Rebuilding from JSON-mode (structured output) to a tool-calling architecture.
v1 (`master` branch) forced the LLM to emit conversation + actions in a single JSON blob.
v2 (`v2-tool-calling-architecture` branch) uses native tool calling.

## Files Kept from v1

| File | Changes needed |
|---|---|
| `menu.json` | None |
| `config.py` | Minor |
| `pricing.py` | Minor API surface tweaks |
| `menu_manager.py` | Remove `format_menu_for_prompt()`; keep lookup/validation |
| `requirements.txt` | Add new deps if any |

## Files to Create

| File | Purpose |
|---|---|
| `models.py` | Domain models: enums, CartItem, CustomerInfo, tool param/result types |
| `catalogue.py` | Product lookup, option validation, menu hints |
| `tools.py` | Tool implementations as pure Python functions |
| `agent_loop.py` | Orchestration loop |
| `prompts.py` | System prompt builder (much simpler than v1) |
| `llm_client.py` | Thin OpenAI SDK wrapper — no JSON parsing |
| `session.py` | OrderSession + persistence |
| `main.py` | CLI entry point |

---

## High-Level Decisions (SETTLED)

### 1. Interaction Model: Tool Calling
LLM emits text (conversation) and tool calls (actions) through separate channels.
No JSON parsing, no schema in prompt, no fallback logic.

### 2. State Machine
4 active states + 2 terminal:

```
BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
   ↓         ↓           ↓
   └─────────┴───────────┴──→ CANCELLED (from any active state)
```

- **BUILDING:** Adding items, collecting customer info. add_to_cart and set_customer_info both available.
- **REVIEW:** Cart complete, showing summary. User can confirm, modify, or cancel.
- **PAYMENT_PENDING:** Payment initiated, waiting for completion.
- **COMPLETED / CANCELLED:** Terminal states.

### 3. Tools & State Availability
Only tools legal in the current state are sent to the LLM → no runtime validation needed.

| Tool | States | Effect |
|---|---|---|
| `add_to_cart` | BUILDING, REVIEW | Resolve product, validate options, add to cart |
| `remove_from_cart` | BUILDING, REVIEW | Remove by name-based reference or UUID |
| `update_item` | BUILDING, REVIEW | Modify quantity or options |
| `view_cart` | All active | Formatted cart + pricing |
| `set_customer_info` | BUILDING, REVIEW | Merge name/phone/address/order_type |
| `request_review` | BUILDING | Transition to REVIEW if preconditions met |
| `confirm_order` | REVIEW | Initiate payment, → PAYMENT_PENDING |
| `cancel_order` | All active | → CANCELLED |

### 4. Agent Loop
```
1. Load session
2. Append user message to conversation history
3. Build system prompt (state + cart + customer + hints)
4. Call LLM with prompt + history + state-filtered tool definitions
5. LLM returns: assistant text + optional tool_calls
6. For each tool_call (sequential, max 5):
   a. Execute tool function
   b. Append tool result to conversation
   c. If tool returns _transition hint, record it (stripped before LLM sees it)
7. Evaluate and apply state transitions (loop owns this)
8. Save session to JSON
9. Return response
```

### 5. Menu Discovery: Middle Ground
- Lightweight hints in system prompt (categories, popular items)
- Full product details (options, toppings, sizes) returned in tool results
- On unknown product: tool returns suggestions, LLM adapts

### 6. Cart Item References
- Primary: stable UUID in CartItem.id
- User-facing: name-based string matching with disambiguation fallback

### 7. Session Persistence: JSON Files
One JSON file per session in `sessions/` directory. Written after every turn.

### 8. Streaming: Batch for Now
Design the loop to support streaming later.

---

## TBD

- AWAITING_CONFIRMATION state (between REVIEW and PAYMENT_PENDING) — explicit double-confirm tracking. Easy to add later.
- Streaming responses
- Real payment integration

---

## Subsystem Docs

| Doc | Purpose |
|---|---|
| [decorator.md](decorator.md) | `@tool` decorator design — type hint → JSON Schema |
| [integration.md](integration.md) | v1 survivor updates (pricing.py, config.py) |
| [build_order.md](build_order.md) | Dependency-ordered implementation sequence |
| [testing.md](testing.md) | Testing strategy per component |

---

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
| `main.py` | SETTLED | [main.md](main.md) |

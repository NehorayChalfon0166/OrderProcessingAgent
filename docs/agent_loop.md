# Component: `agent_loop.py`

Status: **SETTLED — loop pattern**

The orchestration layer. Ties together session, catalogue, pricing, tools, prompts,
and the LLM client. This is the only file that understands the full control flow.

## Design: Loop Pattern

A single `process_turn()` loops: LLM with tools → execute → repeat until the
LLM responds without tool calls (or max iterations reached).

```
while not done:
    LLM with tools → text + tool_calls
    if no tool_calls:
        return text          ← model is responding to the customer
    execute tools, append results, apply transitions
    loop                    ← model sees results, may call more tools or respond
```

Tools are always available. The model decides each iteration whether to call
them or respond. It naturally converges when it has everything it needs.

If the model returns no tool calls and no tools were executed, this is a
single-call turn (e.g., "Hi" → "Welcome!"). If tools execute, the model
sees their results in the next iteration and responds with full context.

This guarantees every turn ends with a complete, tool-result-aware response.
No empty messages, no "Adding..." without "Added!".

## Why not two-call?

The previous two-call pattern (Call 1 with tools, Call 2 without) created a
problem: on Call 2 the model saw tool interactions in context and wanted to
continue the pattern, but `tools=None` blocked the legitimate channel. DeepSeek
models sometimes routed through text instead, outputting raw `<invoke>` tags.

In the loop pattern, tools are always available — the model uses the structured
`tool_calls` channel when it wants to invoke something. There's no forbidden
path to hack around. The model converges naturally.

## `process_turn()`

```python
def process_turn(
    session: OrderSession,
    user_message: str,
    catalogue: Catalogue,
    pricing: PricingEngine,
    llm_client: LLMClient,
) -> str:
```

Called once per user message. Returns the text to display to the user.

### Flow

```
1. Append user message
   session.add_user_message(user_message)

2. Loop (max MAX_ITERATIONS = 5):
   a. Refresh tool definitions (state may have changed)
      tool_funcs = TOOLS_BY_STATE[session.state]
      tool_defs = build_tool_definitions(tool_funcs)

   b. Build prompt + messages
      prompt = build_system_prompt(session, ...)
      full_msgs = [{"role": "system", "content": prompt}] + messages

   c. LLM call
      text, tool_calls = llm_client.chat(full_msgs, tool_defs)

   d. If no tool_calls:
      Record assistant message (text)
      Save session
      Return text                    ← DONE

   e. Tool calls present:
      Record assistant message (tool_calls, no text)
      Execute tools sequentially (max 5 per iteration)
      Apply state transitions
      Continue loop                  ← model sees results, decides next step

3. Fallback (max iterations exhausted):
   Log warning, return generic message
```

## Prompts

Single unified prompt built by `prompts.py`:

- **`build_system_prompt`:** "Use tools when action is needed. When you call
  tools, you don't need text — you'll see results and respond after. When
  ready to respond, keep it brief (1-3 sentences)."

No separate "you have tools" vs "you don't have tools" mode. No mention of
`<invoke>` or tool-call syntax in the prompt — the model doesn't need to be
told not to do something it has no reason to do.

## State Transitions: `_apply_transition()`

Evaluates `session._pending_transition` after each iteration's tool calls.

| Transition | Set by | Preconditions |
|---|---|---|
| CANCELLED | `cancel_order` | None (always wins) |
| REVIEW | `request_review` | Cart not empty, name + phone present, address if delivery |
| PAYMENT_PENDING | `confirm_order` (link) | None |
| COMPLETED | `confirm_order` (cash, default) | None |

**Fallback:** unknown transitions are silently cleared — safety net only.

## Tool Dispatch

`TOOLS_BY_STATE` from `tools.py` is the single source of truth for tool
availability. Both the prompt builder and `_lookup_tool` read from it.

## Safety Nets

| Guard | Mechanism |
|---|---|
| Wrong-state tool call | Not in TOOLS_BY_STATE → not in LLM's tool list → can't be called |
| Unknown tool name | `_lookup_tool` returns None → error result fed back to LLM |
| Tool exception | try/except around each tool call → error result, loop continues |
| Too many tool calls | Slice `tool_calls[:5]` — only first 5 processed per iteration |
| Invalid transition | `_apply_transition` prevalidation blocks it |
| Empty response | Loop doesn't exit until text is produced; fallback if all else fails |
| Infinite loop | `MAX_ITERATIONS = 5` hard cap with fallback message |
| Tool hallucination | Tools always available — model uses real tool_calls channel, no need to hack via text |

## Payment

`confirm_order` takes a `payment_method` parameter:
- `"cash"` (default) — transitions to COMPLETED
- `"link"` — transitions to PAYMENT_PENDING, waits for external webhook

Sets `session.payment_method` for the future payment webhook. See
`docs/payment_architecture.md` for the full design and activation plan.

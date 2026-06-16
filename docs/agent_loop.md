# Component: `agent_loop.py`

Status: **SETTLED — two-call loop**

The orchestration layer. Ties together session, catalogue, pricing, tools, prompts,
and the LLM client. This is the only file that understands the full control flow.

## Design: Two-Call Loop

A single `process_turn()` makes up to two LLM calls:

1. **Call 1 (tool execution):** LLM sees the user message + tool definitions.
   It returns tool calls. Text is ignored — tools execute silently.
2. Tools execute sequentially, results appended to conversation.
3. **Call 2 (response):** LLM sees user message + tool calls + tool results,
   but **without tool definitions** (`tools=None`). It responds naturally.

If Call 1 returns no tool calls, Call 2 is skipped — the text from Call 1
is returned directly.

This guarantees every user turn ends with a complete, tool-result-aware
response. No empty messages, no "Adding..." without "Added!"

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

2. Build Call 1 prompt + tool definitions
   prompt = build_tool_prompt(session, ...)
   tool_funcs = TOOLS_BY_STATE[session.state]
   tool_defs = build_tool_definitions(tool_funcs)

3. CALL 1 — LLM with tools
   full_msgs = [{"role": "system", "content": prompt}] + messages
   _text_1, tool_calls = llm_client.chat(full_msgs, tool_defs)

4. If tool_calls:
   a. Record assistant message (tool_calls only, no text)
      session.add_assistant_message(content=None, tool_calls=tool_calls)

   b. Execute tool calls sequentially (max 5)
      for tc in tool_calls[:5]:
          result = dispatch_and_execute(tc)
          session.add_tool_result(tc.id, tc.name, result.model_dump_json())

   c. Apply state transitions
      _apply_transition(session)

   d. CALL 2 — LLM WITHOUT tools
      prompt_2 = build_response_prompt(session, ...)
      full_msgs_2 = [{"role": "system", "content": prompt_2}] + messages
      text_2, _ = llm_client.chat(full_msgs_2, tools=None)

   e. Record assistant response (text)
      session.add_assistant_message(content=text_2)

5. If no tool_calls:
   Record assistant message (text from Call 1)

6. Save session
   session.save()

7. Return text (text_2 if tools called, _text_1 if not)
```

## Prompts

Two variants built by `prompts.py`:

- **`build_tool_prompt` (Call 1):** "If tools are needed, call them — no text.
  If no tools needed, respond briefly." Menu hints: "Reference when asked, do not list."

- **`build_response_prompt` (Call 2):** "You CANNOT call tools. Only respond with text.
  Do not write tool-call syntax or XML. Keep responses short — 1 to 3 sentences.
  Only say confirmed when state IS 'completed'."

The `tools=None` in Call 2 is an API-level enforcement, not just a prompt request.

## State Transitions: `_apply_transition()`

Evaluates `session._pending_transition` after all tool calls complete.

| Transition | Set by | Preconditions |
|---|---|---|
| CANCELLED | `cancel_order` | None (always wins) |
| REVIEW | `request_review` | Cart not empty, name + phone present, address if delivery |
| PAYMENT_PENDING | (future payment tool) | Reserved for real payment processing |
| COMPLETED | `confirm_order` | None (prototype skips payment hop) |

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
| Too many tool calls | Slice `tool_calls[:5]` — only first 5 processed |
| Invalid transition | `_apply_transition` prevalidation blocks it |
| Empty response | Two-call loop guarantees Call 2 produces text |
| Tool hallucination | `tools=None` in Call 2 prevents tool calls at API level |

## Payment Simulation

`confirm_order` generates a fake payment ID via `uuid.uuid4()` and transitions
directly to COMPLETED. PAYMENT_PENDING exists in the enum as a reserved state
for future real payment processing — the prototype skips it.

# Component: `agent_loop.py`

Status: **SETTLED**

The orchestration layer. Ties together session, catalogue, pricing, tools, prompts,
and the LLM client. This is the only file that understands the full control flow.

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
   session.conversation.append(Message(role=USER, content=user_message))

2. Build prompt + tool definitions
   prompt = build_system_prompt(session, catalogue.get_hints())
   tool_funcs = TOOLS_BY_STATE[session.state]
   tool_defs = llm_client.build_tool_definitions(tool_funcs)

3. Call LLM
   messages = llm_client.messages_to_openai(session.conversation)
   full_msgs = [{"role": "system", "content": prompt}] + messages
   text, tool_calls = llm_client.chat(full_msgs, tool_defs)

4. Record assistant response
   session.conversation.append(
       Message(role=ASSISTANT, content=text, tool_calls=tool_calls)
   )

5. Execute tool calls sequentially (max 5)
   for tc in (tool_calls or [])[:5]:
       tool_fn = lookup(tc.name, tool_funcs)
       if tool_fn is None:
           result = error_result("unknown tool")  # defense-in-depth
       else:
           try:
               result = tool_fn(session, catalogue, pricing, **tc.arguments)
           except Exception as e:
               result = error_result(str(e))
       session.conversation.append(
           Message(role=TOOL, tool_call_id=tc.id, content=result.model_dump_json())
       )

6. Evaluate and apply state transition
   apply_transition(session)

7. Save session
   session.save()

8. Return assistant text
   return text
```

## State Transitions: `apply_transition()`

Evaluates `session._pending_transition` after all tool calls complete.
If `_pending_transition` is None, no transition occurs.

```python
TRANSITION_PREVALIDATION: dict[OrderState, list[callable]] = {
    OrderState.REVIEW: [
        # Called when request_review sets _pending_transition = REVIEW
        lambda s: len(s.cart) > 0 or "Cart is empty",
        lambda s: s.customer.name or "Name is required",
        lambda s: s.customer.phone or "Phone is required",
        lambda s: (s.customer.order_type != OrderType.DELIVERY
                    or s.customer.address
                    or "Address is required for delivery"),
    ],
    OrderState.PAYMENT_PENDING: [
        # Called when confirm_order sets _pending_transition = PAYMENT_PENDING
        # No extra preconditions beyond being in REVIEW
    ],
}
```

If any prevalidation function returns a string (error message): `_pending_transition` is
cleared, the tool result already contains the issues for the LLM. No transition occurs.

If all return `True`: `session.state = session._pending_transition`.

**Cancel always wins:** If `_pending_transition == CANCELLED`, bypass all prevalidation.
CANCELLED has no preconditions.

## Tool Dispatch

```python
def _dispatch_tool(name: str, tool_funcs: list[callable]) -> callable | None:
    for f in tool_funcs:
        if f.__tool_name__ == name:
            return f
    return None
```

`TOOLS_BY_STATE` is imported from `tools.py`. It is the single source of truth for
which tools are available in which state. Both the prompt builder and the dispatcher
read from it.

## Safety Nets

| Guard | Mechanism |
|---|---|
| Wrong-state tool call | Tool not in TOOLS_BY_STATE → not in LLM's tool list → can't be called |
| Unknown tool name | `_dispatch_tool` returns None → error result fed back to LLM |
| Tool exception | try/except around each tool call → error result, loop continues |
| Too many tool calls | Slice `tool_calls[:5]` — only first 5 processed |
| Invalid transition | `apply_transition` prevalidation blocks it, clears `_pending_transition` |

## Payment Simulation

`confirm_order` generates a fake payment ID via `uuid.uuid4()`. In PAYMENT_PENDING
state, `view_cart` remains available. The only exit from PAYMENT_PENDING in the
prototype is `cancel_order` (back to CANCELLED). COMPLETED is reached when we add
a payment webhook or manual confirmation — TBD.

## Error Result Format

When the loop catches an unexpected tool error, it feeds back a structured result
so the LLM can respond:

```json
{"success": false, "error": "Tool execution failed: division by zero"}
```

The LLM presents this naturally to the user: "Something went wrong with that request.
Could you try again?"

# Error Recovery

Status: **SETTLED**

Three layers of protection against transient failures in the agent loop.

---

## Layer 1: LLM Retry

**File:** `llm_client.py`

Wrap the DeepSeek API call with retry for transient failures.

| Error | Retry? | Reason |
|---|---|---|
| `APIConnectionError` | Yes, 3x backoff 1s/3s/9s | DNS failure, connection refused |
| `APITimeoutError` | Yes, 3x backoff 1s/3s/9s | Request timed out |
| `APIStatusError` 503 | Yes, 3x backoff 1s/3s/9s | Server overloaded |
| `APIStatusError` 429 | Yes, 3x backoff 1s/3s/9s | Rate limited |
| `APIStatusError` 400/401/403 | No — raise immediately | Won't self-heal |

### Approach

Refactor `LLMClient.chat()` into:
- `_chat_once()` — current implementation (unchanged)
- `chat()` — retry wrapper around `_chat_once()`

This keeps the core logic identical. Only the call site changes.

---

## Layer 2: Tool Atomicity

**File:** `agent_loop.py`

Each tool in `_execute_tool()` already has its own try/except — errors become
JSON tool results, the LLM sees them and adjusts. This handles the common case.

For catastrophic failures (exception that bypasses per-tool try/except, e.g.
a crash in `_apply_transition` or a Pydantic serialization bug), save a
session snapshot before executing tools. On crash, reload from snapshot and
let the LLM retry the turn naturally.

### Where

In `process_turn()`, before adding the assistant message with tool calls.
The snapshot has the conversation up to the user message — clean retry.

```
session.save(sessions_dir)       # snapshot
session.add_assistant_message(content=None, tool_calls=tool_calls)

try:
    for tc in tool_calls[:5]:
        _execute_tool(session, catalogue, pricing, tc, tool_funcs)
    _apply_transition(session)
except Exception:
    logger.exception("Tool batch failed — rolling back")
    _restore_session_from_snapshot(session, sessions_dir)
    # Assistant message not in snapshot → clean conversation state
    continue  # LLM retries the turn
```

### Restore Helper

```python
def _restore_session_from_snapshot(session, sessions_dir):
    """Reload session from DB or JSON and copy fields in-place."""
    if session._db is not None:
        restored = session._db.load_session(
            session.restaurant_id, session.session_id
        )
    else:
        restored = OrderSession.load(session.session_id, sessions_dir)

    if restored is not None:
        session.cart = restored.cart
        session.customer = restored.customer
        session.conversation = restored.conversation
        session.state = restored.state
        session._pending_transition = None
        session.updated_at = restored.updated_at
```

### Side Benefit

The snapshot save at each iteration also persists state before every LLM call,
closing the "crash between iterations loses unsaved state" gap.

---

## Layer 3: Twilio Send Failures

**Branch:** twilio-integration (`server.py`)

Already handled by existing architecture:
1. `session.save()` runs BEFORE the Twilio send
2. The response text is in the conversation history (assistant message)
3. If Twilio fails, the customer messages again → LLM sees the unsent
   response in context and re-delivers it naturally

No code changes needed for MVP. If explicit pending-response detection is
needed later, add a `_pending_response` field to `OrderSession`.

---

## Verification

1. `python -m pytest tests/ -q` — all existing tests pass
2. New retry test: mock API to fail 2x with timeout, succeed on 3rd — verify
   `chat()` retries and returns the eventual success
3. New rollback test: mock a tool that modifies cart, then a second tool that
   raises unexpectedly — verify session rolls back to pre-batch state
4. `python tests/test_integration.py` — all behavioral checks pass
5. Live smoke test: `python main.py cli --restaurant marios_pizzeria` — place
   a test order

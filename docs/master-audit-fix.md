# Master Branch — Audit Fixes

For each issue: root cause analysis and the fix, focusing on the core
failure reason, not just patching symptoms.

> **2026-06-17 update:** The two-call loop described in Issues #3/#5 was replaced
> by a loop-based agent. The two-call's `tools=None` on Call 2 caused DeepSeek
> models to hallucinate `<invoke>` tags. The loop keeps tools always available —
> the model uses structured `tool_calls` instead of hacking via raw text.
> See `docs/agent_loop.md`.

---

## Issue #1: COMPLETED transition never implemented

**Severity:** Critical  
**Location:** `agent_loop.py` `_apply_transition()`, `tools.py` `confirm_order()`

### Root cause

The state machine was designed with an external payment step in mind:
BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED. `confirm_order` sets
`_pending_transition = PAYMENT_PENDING` (payment must happen first), but
PAYMENT_PENDING → COMPLETED was deferred ("TBD — when we add a payment
webhook"). No payment webhook was ever added, so the order dies in
PAYMENT_PENDING.

The gap is in `_apply_transition`: it handles CANCELLED, REVIEW, and
PAYMENT_PENDING, but has no handler for COMPLETED. When `confirm_order`
sets `_pending_transition = OrderState.COMPLETED` (the prototype path),
it hits the catch-all fallback and is silently cleared.

### Fix

1. **`tools.py`**: `confirm_order` sets `_pending_transition = OrderState.COMPLETED`
   (skip PAYMENT_PENDING in the prototype — no real payment yet).

2. **`agent_loop.py`**: Add COMPLETED handler in `_apply_transition`:
   ```python
   if target == OrderState.COMPLETED:
       session.state = OrderState.COMPLETED
       session._pending_transition = None
       logger.info("State transition: → COMPLETED")
       return
   ```

3. **Keep PAYMENT_PENDING** in the enum and tools — forward-compatible.
   When a payment step is added later, a new endpoint or tool will set
   PAYMENT_PENDING first, then transition to COMPLETED. The prototype
   just doesn't use that hop.

4. **Test**: add `test_completed` to `TestApplyTransition` in
   `tests/test_agent_loop.py`.

---

## Issue #2: PAYMENT_PENDING is a dead-end state

**Severity:** Critical  
**Location:** `tools.py` `TOOLS_BY_STATE`, `models.py` `OrderState`

### Root cause

PAYMENT_PENDING was designed as a pre-completion state where payment is
being processed. The only available tools are `view_cart` and `cancel_order`
— the customer can look but not act. This is correct for a real payment
flow, but since the prototype skips payment entirely (see Issue #1),
PAYMENT_PENDING is an accidental dead-end: there's no path to COMPLETED
and the tools don't allow any action other than cancel.

### Fix

With Issue #1's fix (`confirm_order` → COMPLETED directly), PAYMENT_PENDING
becomes dormant but not harmful. It stays in the code as forward-compatible
infrastructure. The prototype flow is:

```
BUILDING → REVIEW → COMPLETED  (direct)
            ↓
        CANCELLED (any state)
```

When real payments are added: insert PAYMENT_PENDING between REVIEW and
COMPLETED, add a payment processing tool, move `confirm_order` to trigger
payment rather than skip it.

### Decision

Keep PAYMENT_PENDING as-is. No changes needed beyond Issue #1's fix.
Removing it now and re-adding it later would create unnecessary churn.

---

## Issue #3 & #5: Empty text with tool calls + "Adding..." requires two turns

**Severity:** High  
**Location:** `agent_loop.py` `process_turn()`

### Root cause

These are the same problem viewed from two angles.

In the single-call loop, `process_turn` returns the LLM's text BEFORE
executing tools. Two failures occur:

1. **Empty text:** The OpenAI function-calling API allows `content=null`
   when `tool_calls` is present. Some models (DeepSeek included) sometimes
   omit text, returning only tool calls. The user sees nothing.

2. **Stale text:** Even when the LLM includes text ("Adding that!"),
   the text is generated BEFORE tools execute. The LLM can't say "Added
   large Pepperoni — $14.99" because it hasn't seen the pricing result
   yet. The user gets a generic confirmation now, and the actual result
   only appears on the NEXT turn when the LLM sees the tool output.

A prompt instruction ("always include text") can reduce #1 but can't
eliminate it — the behavior is at the API protocol level, not a model
preference. And it doesn't address #2 at all.

### Fix: Two-call loop

```
Call 1: LLM with tools → returns tool_calls (text ignored if present)
        → execute tools, append results, apply transitions
Call 2: LLM WITHOUT tools → sees tool results → responds naturally
        → this text goes to the user
```

If Call 1 returns no tool_calls, Call 2 is skipped and the text from
Call 1 is returned directly.

**Files changed:**
- `agent_loop.py`: `process_turn()` — split into two calls
- `prompts.py`: split into `build_tool_prompt` (Call 1) and
  `build_response_prompt` (Call 2)

**Call 1 prompt:** tells the LLM it should call tools silently, no text
needed (if no tools are needed, respond briefly).

**Call 2 prompt:** tells the LLM it CANNOT call tools — only respond
with text. Explicitly forbids writing tool-call-like syntax. This
prevents the hallucinated XML tool calls we saw on Twilio.

**Guarantees:**
- Every turn ends with a response informed by tool results
- No empty responses reach the user
- "Added X — $Y" is possible (LLM sees pricing from tool result)

---

## Issue #4: Menu dump on greeting

**Severity:** High  
**Location:** `prompts.py` `build_system_prompt()`

### Root cause

The prompt includes `Menu categories: {hints}` as a raw string — e.g.
"Pizzas: Margherita, Pepperoni, BBQ Chicken, ... Sides: Garlic Bread, ..."
The LLM has no instruction that these are reference material, not
conversation filler. On a cold greeting (empty cart, no customer), the
LLM has no task to perform and no tools worth calling, so it defaults to
narrating the only content it has: the menu.

### Fix

Part of the prompt overhaul (see Issues #6, #7). The hints must come
with an explicit constraint: "Use these categories when the customer
asks what's available. Do not list them in the greeting."

This will be addressed together with the broader prompt improvements
in Issue #6.

---

## Issue #6: Verbose, unnatural responses
## Issue #7: No state-specific prompt instructions
## Issue #8: LLM-generated greeting is unpredictable

**Severity:** Medium (combined)  
**Location:** `prompts.py` `build_system_prompt()`

### Root cause

These share one root: the prompt is too minimal. It shows the LLM *what
it knows* (cart, customer, hints) but never tells it *how to behave*.

The single prompt contains one instruction: "Use the available tools to
help the customer. Do not invent menu items." Nothing about:
- **Brevity** — the LLM defaults to multi-paragraph marketing text (Issue #6)
- **State guidance** — "building" vs "review" vs "payment_pending" are
  just strings. The LLM doesn't know what's expected in each (Issue #7)
- **Greeting constraint** — on a cold start, the LLM narrates the entire
  menu because it has no task to perform (Issue #8, #4)

### Fix: Prompt redesign (single change to prompts.py)

Split into two functions matching the two-call loop:

**`build_tool_prompt` (Call 1):** State + cart + customer + hints.
- "If tools are needed: call them, no text needed."
- "If no tools needed: respond briefly, 1-2 sentences."
- No menu dump in greeting — the LLM knows the menu from hints but
  should only mention it when asked.
- Hints come with: "Reference when asked. Do not list."

**`build_response_prompt` (Call 2):** Same context but NO tools sent
(`tools=None` in the API call — hard enforcement, not a polite request).
- "You cannot call tools. Only respond with text."
- "Do not write tool-call syntax or XML."
- "Keep responses short — 1 to 3 sentences."
- "Only say an order is confirmed when state IS 'completed'."

This is one focused change, not four separate patches. It also
eliminates the greeting hack: `main.py` currently calls
`process_turn(session, "Hi", ...)` to get an LLM-generated greeting.
With a proper prompt, the greeting is naturally constrained to a short
welcome.

### Note on `tools=None`

This is not just a prompt instruction — it's an API-level constraint.
When Call 2 is made without tool definitions, the LLM literally cannot
return `tool_calls`. This prevents both real tool calls AND hallucinated
tool-call-like text (which we saw on the Twilio integration). The
prompt instruction reinforces this boundary.

---

## Issue #9: Session router terminal-state check uses bare import

**Severity:** Low  
**Location:** `session_router.py`

### Root cause

`session_router.py` imports `OrderState` from `models` and checks
`session.state in (OrderState.CANCELLED, OrderState.COMPLETED)`. If
the enum values change or models.py is refactored, this check silently
breaks. The logic is correct but the coupling is loose — no test
directly verifies that terminal-session detection works correctly with
all enum values.

### Fix

The existing test suite (`test_session_router.py`) already covers the
CANCELLED-reload behavior. No code change needed — this is a note
that the terminal check should stay in sync with the enum.

---

## Issue #10: No session persistence between CLI runs

**Severity:** Low  
**Location:** `main.py` `run_session()`

### Root cause

`run_session()` calls `OrderSession()` — always creates a new session.
The session save/load API exists but CLI mode doesn't use it. If a user
quits mid-order and re-runs the CLI, they get a fresh session.

### Fix

By design for CLI mode. The `session_router` solves this for server
mode (phone-based persistence). No change.

---

## Issue #11: Dead fallback path in `_apply_transition`

**Severity:** Low  
**Location:** `agent_loop.py` `_apply_transition()`

### Root cause

The fallback `session._pending_transition = None` catches any
transition not explicitly handled. Currently COMPLETED hits this
fallback. Once Issue #1 adds a COMPLETED handler, the fallback becomes
a true safety net for unexpected transitions only.

### Fix

Self-resolves with Issue #1. No separate change needed.

---

## Issue #12: No tests for COMPLETED/PAYMENT_PENDING exit

**Severity:** Low  
**Location:** `tests/test_agent_loop.py`

### Root cause

`TestApplyTransition` covers CANCELLED and REVIEW paths but has no
test for COMPLETED. The behavior didn't exist yet.

### Fix

Add `test_completed` alongside Issue #1's implementation.

# CLAUDE.md — Project rules and AI agent guidelines

## How you should work

- **Discuss before planning.** When I ask about something, discuss it with me
  before jumping to architecture or code. Do not assume my intent.
- **Plans go in docs/.** When I say "write a plan," I mean a markdown file
  in `docs/`. Not the internal Claude plan file. If you are in plan mode
  and need to write files outside docs/, ask me to exit plan mode first.
- **Clean up plan docs after implementation.** Once a plan is fully
  implemented, delete it or merge it into the relevant component doc.
  Don't leave stale implementation plans in `docs/`.
- **No execution without approval.** Do not write code, commit, or make
  changes unless I explicitly tell you to. When I say "let's talk about X,"
  I mean talk — not plan, not implement.
- **Challenge me.** If you think I'm wrong or there is a better approach,
  say so. I value your expertise.
- **Do not overfit tests.** Tests should verify behavior, not mirror
  implementation. If a test fails because the implementation changed
  legitimately, fix the test.

## Branching strategy

```
master: all core logic
  ├── {channel}-integration: I/O layer only for that channel
```

### What belongs on master

- `models.py`, `catalogue.py`, `pricing.py`, `tools.py`
- `session.py`, `session_router.py` — session lifecycle and identity mapping
- `agent_loop.py`, `prompts.py`, `llm_client.py`
- `config.py` — all env vars (channel fields allowed as dormant defaults)
- `main.py` — CLI entry point only
- `requirements.txt` — core dependencies only
- All tests for the above
- All docs EXCEPT `docs/{channel}_integration.md`

### What belongs on integration branches

- `twilio_client.py` / `whatsapp_client.py` — channel-specific API wrapper
- `server.py` — FastAPI webhook wiring (connects API to `process_turn`)
- `docs/{channel}_integration.md` — channel-specific documentation
- `tests/test_{channel}_*.py` — channel-specific tests
- `.env.example` additions for that channel
- Channel-specific dependencies documented but not forced on master

### What integration branches must NOT do

- Modify any file that exists on master. Integration branches add NEW files
  only. Core improvements happen on master and integration branches rebase.
- Switching between channels should be trivial — same core, different I/O.

## Merge strategy

1. Core improvements (agent_loop, prompts, tools, etc.) → `master`
2. Integration branches rebase onto `master`
3. Deploy whichever integration branch you need — the core is identical

## Architecture principles

- **Python owns all state transitions and math.** The LLM can suggest, but
  only our code changes state and computes prices.
- **`process_turn()` is the universal interface.** It takes a string, returns
  a string. Every channel wraps this — CLI, WhatsApp, Voice, web, etc.
- **Session = order, not person.** One session per order. Multiple orders
  from the same person are separate sessions. Terminal sessions (CANCELLED,
  COMPLETED) are dead and replaced on next contact.
- **Session identity is a phone number.** Sanitized to digits. Used as both
  the session filename and the lookup key.
- **State machine:** BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
  (CANCELLED from any active state).
- **LLM never sees raw tool results delivered to users.** Tool calls execute
  silently, then the LLM responds naturally.

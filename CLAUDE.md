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
- **Tests are not enough.** The unit test suite mocks at the function level
  and cannot catch missing variable assignments, broken import chains, or
  undefined functions. After any change that touches function signatures,
  imports, or the call chain, run live verification: mock the LLM, call
  the actual entry points, and make sure they don't crash.
- **When a bug escapes the tests, add a test that would have caught it.**
  Don't just fix the bug and move on. Ask why the tests didn't catch it
  and close the gap. The integration test suite (`tests/test_integration.py`)
  exists specifically to catch issues that unit tests miss — use it and
  extend it.

## Branching strategy

```
master: all core logic
  ├── {channel}-integration: I/O layer only for that channel
```

### What belongs on master

- `models.py`, `catalogue.py`, `pricing.py`, `tools.py`
- `session.py`, `session_router.py` — session lifecycle and identity mapping
- `agent_loop.py`, `prompts.py`, `llm_client.py`
- `restaurant.py`, `restaurants.json` — multi-tenant restaurant registry
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
- **Session identity is (restaurant_id, phone_number).** Phone sanitized
  to digits is the session_id; restaurant_id scopes the namespace
  (subdirectory). Same phone can order from multiple restaurants.
- **State machine:** BUILDING → REVIEW → PAYMENT_PENDING → COMPLETED
  (CANCELLED from any active state).
- **LLM never sees raw tool results delivered to users.** Tool calls execute
  silently, then the LLM responds naturally.

## Pre-commit verification

Before committing on any branch, run these checks. Do not skip any.

1. **Test suite:**
   ```bash
   python -m pytest tests/ -q
   ```
   Every test must pass. If you changed a function signature, update the
   callers in tests too.

2. **Integration tests:**
   ```bash
   python tests/test_integration.py
   ```
   These test full call chains with real file I/O — they catch what unit
   tests miss (missing variables, broken imports, wrong subdirectories).

3. **Live smoke test — CLI entry point:**
   ```python
   from unittest import mock; import sys, io
   with mock.patch('main.process_turn', return_value='Welcome!'):
       from config import AppConfig; from main import run_session
       sys.stdin = io.StringIO('quit\n')
       run_session(AppConfig.from_env())
   ```
   This catches `NameError`, `ImportError`, and `AttributeError` in the
   full `run_session` call chain. If you added parameters to
   `process_turn`, changed `OrderSession` fields, or modified imports in
   `main.py`, this must not crash.

4. **Live smoke test — `--list-restaurants`:**
   ```bash
   python main.py --list-restaurants
   ```
   Verifies the argument parser and `RestaurantRegistry` load correctly.

5. **Dead code check:**
   - Search for unused imports in every file you touched.
   - Search for references to removed fields/functions.
   - Check that docstrings and error messages don't reference deleted files
     (like the old `menu.json`).

6. **Docs check:** If you added a feature, changed a workflow, or modified
   configuration, update the relevant docs in `docs/` and `README.md`.
   Delete stale docs. Per the rule above: plans are deleted or converted
   to operational docs after implementation.

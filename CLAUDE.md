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

## Commit workflow

Every commit is gated by TWO independent layers. Both must pass. Neither
can be skipped (Claude Code blocks the Bash call before git even sees it).

### Layer 1 — Mechanical checks (git pre-commit hook)

Located at `.githooks/pre-commit`. Runs automatically via `core.hooksPath`:

- Test suite: `python -m pytest tests/ -q` (auto-discovers all tests)
- Integration tests: `python tests/test_integration.py`
- Live smoke test: CLI entry point with mocked LLM
- Branch rules: `*-integration` branches cannot modify master files

If any fail, git blocks the commit.

### Layer 2 — Manual review gate (Claude Code PreToolUse hook)

Located at `.claude/hooks/commit-guard.sh`. Triggered by `settings.json`
`PreToolUse` on `Bash(git commit*)`. This fires BEFORE the Bash tool runs,
so it cannot be bypassed (even `--no-verify` won't help — Claude Code
intercepts the command before git).

The guard blocks ALL `git commit` attempts unless `.review-approved`
exists. This file is created by the AI agent only after:

1. Mechanical checks pass
2. Manual review complete:
   - Dead code: unused imports, references to removed fields/files
   - Docs: README.md, docs/ up to date? Stale docs deleted?
   - Test gaps: did any bug escape? Add a test that would have caught it
   - Consistency: timestamps, file paths, naming match across files?
3. **Findings reported to user**
4. **User explicitly approves**

### Commit sequence

```
1. AI stages changes, attempts git commit
2. Claude Code PreToolUse hook → .review-approved? → NO → BLOCKED
3. AI does manual review, reports to user
4. User approves → AI runs: echo approved > .review-approved
5. AI retries git commit
6. Claude Code PreToolUse hook → .review-approved exists → allow
7. Git pre-commit hook → tests, integration, smoke → pass → commit succeeds
```

### After commit

- `.review-approved` is auto-deleted by the Claude Code hook
- Docs cleanup happens in a follow-up commit if needed
- The next commit starts fresh — no stale approval file


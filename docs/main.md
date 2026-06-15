# Component: `main.py`

Status: **SETTLED**

CLI entry point. Thin shell around `agent_loop.process_turn()`. Nearly identical
structure to v1's `main.py` — the complexity moved from `_apply_response` to the
agent loop, so this file gets simpler.

## Flow

```python
def main():
    # 1. Parse --debug flag
    args = parse_args()

    # 2. Load config from .env
    config = AppConfig.from_env()
    if args.debug:
        config.debug = True

    # 3. Setup logging
    setup_logging(config.debug)

    # 4. Initialize components
    catalogue = Catalogue(config.menu_path)
    pricing = PricingEngine(catalogue.menu_data)
    llm_client = LLMClient(config)

    # 5. Print banner
    print(BANNER)

    # 6. Create new session
    session = OrderSession.create()

    # 7. Generate greeting via agent loop
    text = process_turn(session, "Hi", catalogue, pricing, llm_client)
    print_agent(text)

    # 8. REPL
    while True:
        user_input = get_input()
        if is_meta_command(user_input):
            handle_meta(user_input, session, catalogue, pricing, llm_client)
            continue

        text = process_turn(session, user_input, catalogue, pricing, llm_client)
        print_agent(text)

        if session.state == OrderState.CONFIRMED:
            save_order(session)
            print_receipt(session)
            break
        elif session.state == OrderState.CANCELLED:
            print_goodbye()
            break
```

## Meta Commands

| Command | Behavior |
|---|---|
| `quit` / `exit` | End session (no save unless CONFIRMED) |
| `status` | Print current cart, customer info, state |
| `restart` | Create new session, re-greet |

## Session Creation

Always new session on start. Resumption can be added later with `--resume <id>`.

```python
session = OrderSession(
    session_id=str(uuid.uuid4())[:8].upper(),
    state=OrderState.BUILDING,
    cart=[],
    customer=CustomerInfo(),
    conversation=[],
    created_at=datetime.now(tz=timezone.utc).isoformat(),
    updated_at=datetime.now(tz=timezone.utc).isoformat(),
)
```

## Display

Same emoji-prefixed format as v1:

```
🤖 Agent: Welcome to Mario's Pizzeria! ...

[🛒 Building Order] You: I'd like a large pepperoni pizza

🤖 Agent: Great choice! One large Pepperoni Pizza added. Anything else?
```

State indicator prefix comes from the current `session.state.value`.

## Order Saving

When `session.state == CONFIRMED`:
1. Build final payload from session (items + customer + totals)
2. Save to `orders/{session_id}_{timestamp}.json` (same pattern as v1)
3. Display receipt with order ID and total

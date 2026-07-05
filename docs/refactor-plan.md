# Refactor Plan

## Bugs (fix first)

1. **server.py `_processed_sids` shadowing** — local variable shadows module-level, memory leak
2. **restaurant.py `reload()` hardcoded path** — stores path from init
3. **dashboard `_check_token` pre-init bypass** — token check before init is useless

## Deduplication

4. **Extract `_atomic_write`** to shared utility
5. **Extract `_generate_order_id`** to shared utility  
6. **Extract review preconditions** to single function
7. **Remove `main.py` fallback** in save_order (unreachable, dead code)

## Dead Code Removal

8. **Delete `_conversation_to_json()`** in db.py
9. **Delete `delete_session()`** in db.py (or keep — it's a valid CRUD method)
10. **Remove double `@staticmethod`** in catalogue.py

## Simplification

11. **PricingEngine reads from Catalogue** instead of re-indexing menu
12. **`_apply_transition` → dict-based** instead of if/elif chain
13. **`receive_whatsapp` split** into smaller named functions

## Consistency

14. **Module-level logger in tools.py**
15. **Replace `assert` in llm_client.py** with if/raise
16. **Log warning on dropped tool calls** in agent_loop.py

## Hardening

17. **Per-restaurant try/except in Registry._load** — one bad menu shouldn't crash all
18. **Guard create_checkout_session** with Stripe key check before calling

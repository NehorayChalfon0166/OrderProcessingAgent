# Multi-Restaurant Support

## Why

The system currently supports a single restaurant — one `menu.json`, one
`Catalogue`/`PricingEngine` singleton, sessions keyed by phone number only.
For a SaaS platform, we need multiple independent restaurants, each with
their own menu, their own WhatsApp number, and session isolation so the same
customer can order from different restaurants simultaneously.

## Constraints

- No backward compatibility. This is not deployed anywhere — old sessions,
  orders, and the flat `menu.json` structure are replaced, not migrated.
- Clean break. No "auto-detect old format" fallbacks.

## Two Phone Numbers at Play

Every Twilio webhook carries two distinct phone numbers. They serve completely
different purposes and must not be confused:

| Field | Who it is | Example | Used as | Role |
|---|---|---|---|---|
| `From` / `WaId` | The **customer** messaging us | `972539534345` | `session_id` | Identifies the person placing the order |
| `To` | The **restaurant's** Twilio WhatsApp number | `+14155238886` | looked up in `RestaurantRegistry` | Determines which restaurant they're ordering from |

- `WaId` → `session_id` (the session is *whose* order)
- `To` → `restaurant_id` via registry lookup (the order is *from where*)

After the initial routing lookup, `restaurant_id` (a slug like `"marios_pizzeria"`)
carries the restaurant identity through the rest of the flow. The `To` number
is never stored on the session — it was only needed to find the restaurant.

## Sessions vs Orders

These are different things with different lifecycles:

| | Session | Order |
|---|---|---|
| **What** | A live conversation in progress | A finalized receipt |
| **Mutable?** | Yes — cart and state change every turn | No — written once, never touched again |
| **How many?** | One active per (restaurant, customer) | Many per (restaurant, customer) over time |
| **When replaced?** | Terminal sessions (COMPLETED/CANCELLED) are overwritten on next contact | Never replaced — each is a permanent record |
| **Filename** | `{phone}.json` | `{phone}_{timestamp}.json` |
| **Path** | `sessions/{restaurant_id}/{phone}.json` | `orders/{restaurant_id}/{phone}_{ts}.json` |
| **Why the name?** | The directory already scopes to restaurant; one file per customer, constantly overwritten | Timestamp makes each completed order filename unique so they never collide |

Both use `{restaurant_id}` subdirectories for the same reason: one customer
can interact with multiple restaurants simultaneously.

## Branch

All work happens on `multi-restaurant` branch (branched from `master`).
Once stable and tested, merges to `master`. Then `twilio-integration`
rebases on the updated master.

```
master
  ├── twilio-integration (I/O layer only, rebases after merge)
  └── multi-restaurant (this feature)
```

## Agreed Design Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | No backward compat | Not deployed, no migration needed |
| 2 | `restaurants.json` required at startup | Missing = error, no silent defaults |
| 3 | `session_id` stays as phone digits | Phone is unique; `restaurant_id` is a separate field |
| 4 | Sessions stored in `sessions/{restaurant_id}/{phone}.json` | Composite namespace via directories |
| 5 | `process_turn()` gets `sessions_dir` param (default `"sessions"`) | Explicit, clean, backward-compat for signature |
| 6 | `twilio_phone` field on `RestaurantConfig` | Per-restaurant deployment config, one source of truth |

## What Changes

### New Files

| File | Purpose |
|---|---|
| `restaurant.py` | `RestaurantConfig`, `RestaurantContext`, `RestaurantRegistry` |
| `restaurants.json` | Deployment config mapping restaurant slugs to menus and Twilio numbers |
| `menus/` directory | One JSON menu file per restaurant (e.g. `menus/marios_pizzeria.json`) |

### Modified Files

| File | Change |
|---|---|
| `session.py` | Add `restaurant_id: str` field to `OrderSession` |
| `session_router.py` | `get_or_create(restaurant_id, phone_number)`, subdirectory-based paths |
| `agent_loop.py` | Add `sessions_dir: str = "sessions"` param to `process_turn()` |
| `config.py` | Add `RESTAURANTS_PATH` env var; **note `MENU_PATH` for later cleanup** |
| `main.py` | Add `--restaurant` / `--list-restaurants` flags, use `RestaurantRegistry` |
| `server.py` | Replace `_catalogue`/`_pricing` globals with `_registry`, `To`-field routing, composite lock keys |

### Files NOT Changed

`pricing.py`, `tools.py`, `prompts.py`, `twilio_client.py`, `llm_client.py`,
`models.py` — all receive what they need by injection or have no multi-tenancy
concerns.

### Cleanup Noted for Later

- `MENU_PATH` env var in `config.py` — superseded by `restaurants.json`, remove after multi-restaurant is stable
- Root-level `menu.json` — delete after migrating content to `menus/`

## Implementation Steps

### 1. Set up directory structure
- Create `menus/` directory
- Move `menu.json` content into `menus/marios_pizzeria.json`
- Create `restaurants.json` with Mario's Pizzeria as the first entry
- Delete root-level `menu.json`

### 2. `restaurant.py`
- `RestaurantConfig` (frozen dataclass): `id`, `name`, `menu_path`, `twilio_phone` (required — every restaurant must have a number; missing/empty raises error)
- `RestaurantContext`: bundles config + Catalogue + PricingEngine
- `RestaurantRegistry`: loads `restaurants.json`, creates all contexts
  - Validates every restaurant has a `twilio_phone` — raises error if missing or empty
  - `get_by_id(id) -> RestaurantContext | None`
  - `get_by_twilio_phone(phone) -> RestaurantContext | None` — returns `None` if no match
  - `get_default() -> RestaurantContext` (first in the dict)
  - `list_restaurants() -> list[RestaurantConfig]`
  - No `menu.json` fallback — `restaurants.json` is required, error if missing

### 3. `session.py`
- Add `restaurant_id: str` field to `OrderSession`

### 4. `session_router.py`
- `get_or_create(self, restaurant_id: str, phone_number: str) -> OrderSession`
- Session path: `{sessions_dir}/{restaurant_id}/{phone_digits}.json`
- Always sets `session.restaurant_id = restaurant_id`

### 5. `agent_loop.py`
- `process_turn()` gets `sessions_dir: str = "sessions"` parameter
- Internal `session.save(sessions_dir)` calls use it

### 6. `config.py`
- Add `restaurants_path: str = "restaurants.json"` to `AppConfig`
- Add `RESTAURANTS_PATH` env var in `from_env()`
- Note: `menu_path` / `MENU_PATH` to be removed later (cleanup)

### 7. `main.py`
- Add `--restaurant` and `--list-restaurants` CLI flags
- Use `RestaurantRegistry` instead of direct `Catalogue(path)`
- Set `session.restaurant_id` from context
- Pass `f"sessions/{restaurant_id}"` as `sessions_dir` to `process_turn`
- Save completed orders to `orders/{restaurant_id}/{phone}_{ts}.json`
- Add `"restaurant_id"` to order JSON payload

### 8. `server.py`
- Replace `_catalogue`/`_pricing` module globals with `_registry: RestaurantRegistry`
- In `_lifespan`: `_registry = RestaurantRegistry(cfg.restaurants_path)` instead of creating Catalogue/PricingEngine directly
- In `receive_whatsapp`: extract `To` from `flat_params`, strip `whatsapp:` prefix, call `_registry.get_by_twilio_phone()`
- Unknown restaurant → HTTP 500
- Lock key: `f"{restaurant_id}:{wa_id}"` (was just `wa_id`)
- Pass `f"{_router.sessions_dir}/{restaurant_id}"` as `sessions_dir` to `process_turn`
- `_save_order_file(session, restaurant_ctx)`:
  - Takes `RestaurantContext` instead of using globals `_catalogue`/`_pricing`
  - Saves to `orders/{restaurant_id}/{phone}_{ts}.json`
  - Adds `"restaurant_id": session.restaurant_id` to payload
- Remove `assert _catalogue is not None` and `assert _pricing is not None` guards (no longer globals)

### 9. Tests (written alongside each component above)

**New: `tests/test_restaurant.py`**
- Load `restaurants.json` with two restaurants → both accessible by `get_by_id()`
- `get_by_twilio_phone("+14155238886")` → returns correct restaurant
- `get_by_twilio_phone("+00000000000")` → returns `None`
- `get_default()` → returns first restaurant in the dict
- `list_restaurants()` → returns all configs
- `restaurants.json` file missing → raises clear error
- Menu file path in config points to nonexistent file → error on `Catalogue` creation

**Update: `tests/test_session_router.py`**
- Update all existing calls to new signature: `get_or_create(restaurant_id, phone_number)`
- Same phone, different `restaurant_id` → two different sessions, stored in different subdirectories
- Sessions read/write to `sessions/{restaurant_id}/` subdirectory, not flat `sessions/`

**Update: `tests/test_server.py`**
- Mock `_registry` instead of `_catalogue`/`_pricing`
- Webhook with `To=whatsapp:+14155238886` → routes to correct `RestaurantContext`
- Webhook with unrecognized `To` number → HTTP 500
- Composite lock keys: two requests from same phone to different restaurants use different locks (don't block each other)
- `_save_order_file` writes to correct `orders/{restaurant_id}/` subdirectory

**Update: CLI tests (in existing test file or new)**
- `--restaurant marios_pizzeria` → uses correct menu
- `--list-restaurants` → prints configured restaurant names/IDs
- No `--restaurant` flag → uses `get_default()`

**Tests that need NO changes:**
`test_catalogue.py`, `test_pricing.py`, `test_tools.py`, `test_prompts.py`,
`test_agent_loop.py`, `test_twilio_client.py`, `test_llm_client.py` — their
interfaces are unchanged (`process_turn`'s new parameter has a default).

## Verification

1. Run all existing tests (updated for new signatures)
2. `python main.py --list-restaurants` → lists configured restaurants
3. `python main.py --restaurant marios_pizzeria` → loads correct menu
4. Start server, send WhatsApp messages to different Twilio numbers → each routes to correct restaurant
5. Same phone orders from two different restaurants → two independent sessions

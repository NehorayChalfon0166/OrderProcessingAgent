# v1 Survivor Integration

Status: **SETTLED**

What needs to change in the files we kept from v1.

## `pricing.py`

Renames only. The logic is unchanged.

| v1 | v2 |
|---|---|
| `from models import OrderItem, OrderType` | `from models import CartItem, OrderType` |
| `OrderItem` | `CartItem` |
| `item.item_name` | `item.name` |
| `t.topping_name` for t in item.toppings | `t.name` for t in item.toppings |
| `ToppingSelection` | `CartTopping` (transitive — CartItem uses CartTopping) |

`get_item_base_price(item_id, size)` stays identical.
`price_item(cart_item)` stays identical except for field name access.
`compute_totals(items, order_type)` stays identical.

## `config.py`

Stripped to DeepSeek-only. No provider presets dict.

```python
@dataclass
class AppConfig:
    llm_api_key: str
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com"
    menu_path: str = "menu.json"
    orders_dir: str = "orders"
    sessions_dir: str = "sessions"
    debug: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required. Set it in .env or as an env var."
            )
        return cls(
            llm_api_key=api_key,
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            menu_path=os.getenv("MENU_PATH", "menu.json"),
            orders_dir=os.getenv("ORDERS_DIR", "orders"),
            sessions_dir=os.getenv("SESSIONS_DIR", "sessions"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )
```

`.env` changes: `LLM_API_KEY` → `DEEPSEEK_API_KEY`. Remove `LLM_PROVIDER`.

## `menu.json`

No changes. The data file is architecture-independent.

## `requirements.txt`

Same dependencies. `openai` SDK, `pydantic`, `python-dotenv`. No new deps.

## Files NOT Kept (already deleted from v2 branch)

- `prompts.py` — rewritten (radically simpler)
- `state_machine.py` — replaced by `agent_loop.py`
- `llm_client.py` — rewritten (no JSON parsing)
- `models.py` — rewritten (different models)
- `main.py` — rewritten (simpler, drives agent loop)
- `menu_manager.py` — replaced by `catalogue.py` (different API surface)

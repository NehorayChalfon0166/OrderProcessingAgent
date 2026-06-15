# Component: `catalogue.py`

Status: **SETTLED**

Evolution of v1's `menu_manager.py`. Fuzzy matching stays. Menu-in-prompt goes. Deals become first-class.

## Data Classes

```python
class ProductDef:
    """Resolved product from the menu — read-only, never mutated."""
    id: str
    name: str
    category: str
    description: str
    sizes: dict[str, float] | None     # {"small": 9.99, ...} or None for flat-price
    default_size: str | None
    flat_price: float | None           # for items without sizes
    available_toppings: list[str]      # topping IDs this product accepts
    required_options: dict[str, list[str]]  # e.g. {"sauce": ["marinara", "alfredo", "pesto"]}

class ToppingDef:
    id: str
    name: str
    price: float

class DealDef:
    id: str
    name: str
    description: str
    price: float
    includes: dict    # raw structure from menu.json
```

## Catalogue API

```python
class Catalogue:
    # --- Lookup (fuzzy matching carried over from v1 menu_manager.py) ---
    def find_product(name: str) -> ProductDef | None
        # Fuzzy match: exact → substring → reverse substring
    def get_product(id: str) -> ProductDef | None
        # Exact ID lookup
    def find_topping(name: str) -> ToppingDef | None
        # Fuzzy match: exact → substring → reverse substring → by-ID
    def get_topping(id: str) -> ToppingDef | None
    def find_deal(id_or_name: str) -> DealDef | None
        # Fuzzy match by name, fallback to ID

    # --- Validation & resolution ---
    def resolve_size(product: ProductDef, size: str | None) -> tuple[str | None, list[str]]
        # Returns (resolved_size, issues).
        # Uses default_size if none provided. Issues if invalid size given.

    def resolve_toppings(product: ProductDef, names: list[str]) -> tuple[list[CartTopping], list[str]]
        # Resolves topping names to CartTopping objects with IDs and prices.
        # Issues: topping not found, topping not available for this product.

    def expand_deal(deal: DealDef) -> list[CartItem]
        # Expands a deal template into constituent cart items.
        # Each item has missing_options set for choices the user must make.
        # e.g. "Family Deal" → 2x Pizza(missing=["which pizza?"]), 1x Side(missing=["which side?"]), ...

    # --- Prompt hints ---
    def get_hints() -> str
        # Lightweight: category names + 2-3 popular items per category.
        # NOT the full menu. ~200 chars max.

    # --- List accessors ---
    def get_categories() -> list[str]
    def get_toppings() -> list[ToppingDef]
    def get_deals() -> list[DealDef]
```

## How `add_to_cart` Uses the Catalogue

1. `catalogue.find_product(name)` — if None, return `AddToCartResult(success=False, suggestions=[...])`
2. `catalogue.resolve_size(product, size)` — validate/normalize size
3. `catalogue.resolve_toppings(product, topping_names)` — resolve to `CartTopping` objects
4. Check `product.required_options` against provided `options` dict — collect missing
5. If product is a deal: `catalogue.expand_deal()` — expand into multiple `CartItem`s
6. If quantity > 1: set quantity on the `CartItem`
7. `pricing.price_item(cart_item)` — fill in `base_price` and `line_total`
8. Return `AddToCartResult(success=True, item=cart_item, missing_options=[...])`

## What Changed from v1 `menu_manager.py`

| v1 | v2 |
|---|---|
| `format_menu_for_prompt()` | `get_hints()` — radically smaller output |
| `validate_extracted_item()` monolith | Split into `resolve_size()`, `resolve_toppings()` — catalogue validates, tools.py assembles |
| Deals loaded but ignored | `expand_deal()` — first-class support |
| `ExtractedItem` parameter | Tools pass typed params directly |

"""Product catalogue — loads a restaurant menu JSON file, provides lookup, validation, and hints.

Evolution of v1's menu_manager.py. The fuzzy matching core is preserved.
What changed:
- format_menu_for_prompt() → get_hints() (radically smaller output)
- validate_extracted_item() monolith → resolve_size() + resolve_toppings()
- Deals registered as flat-price products for add_to_cart compatibility
- ExtractedItem is gone — tools pass typed parameters directly
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

from models import CartItem, CartTopping


# =============================================================================
# Read-only product definitions
# =============================================================================


@dataclass(frozen=True)
class ProductDef:
    """Resolved product from the menu — read-only, never mutated."""

    id: str
    name: str
    category: str
    description: str
    sizes: dict[str, float] | None = None       # {"small": 9.99, ...} or None
    default_size: str | None = None
    flat_price: float | None = None              # for items without sizes
    available_toppings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToppingDef:
    """A single topping from the menu."""

    id: str
    name: str
    price: float


@dataclass(frozen=True)
class DealDef:
    """A deal/special from the menu."""

    id: str
    name: str
    description: str
    price: float
    includes: dict


# =============================================================================
# Catalogue
# =============================================================================


class Catalogue:
    """Loads and manages the restaurant menu.

    Provides:
    - Fast lookup of products, toppings, and deals by ID or name
    - Fuzzy matching (exact → substring → reverse substring)
    - Size and topping resolution
    - Deal expansion into cart items
    - Lightweight prompt hints
    """

    def __init__(self, menu_path: str) -> None:
        self._menu_data = self._load_menu(menu_path)

        # Indexes
        self._products_by_id: dict[str, ProductDef] = {}
        self._products_by_name: dict[str, ProductDef] = {}
        self._toppings_by_id: dict[str, ToppingDef] = {}
        self._toppings_by_name: dict[str, ToppingDef] = {}
        self._deals_by_id: dict[str, DealDef] = {}
        self._deals_by_name: dict[str, DealDef] = {}

        self._build_indexes()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def menu_data(self) -> dict:
        """Raw menu data dict (loaded from JSON)."""
        return self._menu_data

    @property
    def restaurant_name(self) -> str:
        return self._menu_data.get("restaurant_name", "Restaurant")

    # ------------------------------------------------------------------
    # Loading & Indexing
    # ------------------------------------------------------------------

    @staticmethod
    def _load_menu(path: str) -> dict:
        menu_file = Path(path)
        if not menu_file.exists():
            raise FileNotFoundError(
                f"Menu file not found at '{menu_file.resolve()}'. "
                f"Make sure the menu file exists at the configured path."
            )
        with open(menu_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_indexes(self) -> None:
        # Products across all categories
        for category in self._menu_data.get("categories", []):
            category_name = category.get("name", "")
            for item in category.get("items", []):
                # Skip items explicitly marked unavailable
                if item.get("available") is False:
                    continue

                # Filter out unavailable variants from the sizes dict
                sizes = item.get("sizes")
                unavailable = item.get("unavailable_variants", [])
                if sizes and unavailable:
                    sizes = {
                        k: v for k, v in sizes.items()
                        if k not in unavailable
                    }
                    if not sizes:
                        # All variants unavailable — skip the item
                        continue

                product = ProductDef(
                    id=item["id"],
                    name=item["name"],
                    category=category_name,
                    description=item.get("description", ""),
                    sizes=sizes,
                    default_size=item.get("default_size"),
                    flat_price=item.get("price"),
                    available_toppings=item.get("available_toppings", []),
                )
                self._products_by_id[product.id] = product
                self._products_by_name[product.name.lower()] = product

        # Toppings
        for topping in self._menu_data.get("toppings", []):
            td = ToppingDef(
                id=topping["id"],
                name=topping["name"],
                price=topping["price"],
            )
            self._toppings_by_id[td.id] = td
            self._toppings_by_name[td.name.lower()] = td

        # Deals — registered as regular products so find_product / add_to_cart
        # work without special-casing. Deals appear as single line items
        # with a flat price.
        for deal in self._menu_data.get("deals", []):
            dd = DealDef(
                id=deal["id"],
                name=deal["name"],
                description=deal.get("description", ""),
                price=deal["price"],
                includes=deal.get("includes", {}),
            )
            self._deals_by_id[dd.id] = dd
            self._deals_by_name[dd.name.lower()] = dd

            # Also register as a product for normal lookup
            dp = ProductDef(
                id=dd.id,
                name=dd.name,
                category="Deals",
                description=dd.description,
                flat_price=dd.price,
            )
            self._products_by_id[dp.id] = dp
            self._products_by_name[dp.name.lower()] = dp

    # ------------------------------------------------------------------
    # Product Lookup
    # ------------------------------------------------------------------

    def find_product(self, name: str) -> ProductDef | None:
        """Fuzzy match a product by name.

        Exact → substring → difflib similarity (handles typos, plurals,
        missing punctuation like 'couple special' → 'Couple's Special').
        """
        return self._fuzzy_best_match(name, self._products_by_name)

    def get_product(self, product_id: str) -> ProductDef | None:
        """Exact lookup by product ID."""
        return self._products_by_id.get(product_id)

    # ------------------------------------------------------------------
    # Topping Lookup
    # ------------------------------------------------------------------

    def find_topping(self, name: str) -> ToppingDef | None:
        """Fuzzy match a topping by name or ID."""
        result = self._fuzzy_best_match(name, self._toppings_by_name)
        if result is not None:
            return result
        # Fallback: match by ID (handles 'extra_cheese' style)
        id_query = name.lower().strip().replace(" ", "_")
        return self._toppings_by_id.get(id_query)

    def get_topping(self, topping_id: str) -> ToppingDef | None:
        """Exact lookup by topping ID."""
        return self._toppings_by_id.get(topping_id)

    # ------------------------------------------------------------------
    # Deal Lookup
    # ------------------------------------------------------------------

    def find_deal(self, id_or_name: str) -> DealDef | None:
        """Fuzzy match a deal by name, fallback to ID."""
        result = self._fuzzy_best_match(id_or_name, self._deals_by_name)
        if result is not None:
            return result
        return self._deals_by_id.get(id_or_name.lower().strip())

    # ------------------------------------------------------------------
    # Fuzzy Matching
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(s: str) -> str:
        """Normalize a string for fuzzy matching — lowercase, strip quotes."""
        return s.lower().strip().replace("’", "").replace("’", "").replace(""", "").replace(""", "")

    @staticmethod
    def _fuzzy_best_match(query: str, index: dict[str, object]) -> object | None:
        """Fuzzy lookup against a name→object index.

        Priority:
          1. Exact match (case-insensitive, punctuation-normalized)
          2. Substring — requires exactly one unambiguous match
          3. difflib similarity — length-gated to avoid short-query false positives
             (e.g. "coke" → "Diet Coke" is a substring hit, not difflib)
        """
        q_norm = Catalogue._normalize(query)

        # Build a normalized-key index so apostrophes/smart-quotes
        # in product names don’t prevent matching.
        norm_index = {Catalogue._normalize(k): v for k, v in index.items()}

        # 1. Exact
        if q_norm in norm_index:
            return norm_index[q_norm]

        # 2. Substring — collect ALL matches, require exactly one.
        #    "coke" substring-matches "diet coke" but not "coca cola" — a
        #    single match that is semantically wrong.  Short queries (≤4 chars)
        #    skip substring matching entirely: they are too prone to false
        #    positives, and the LLM handles suggestions well.
        #    "chicken" matches "bbq chicken", "buffalo chicken", "chicken wings"
        #    → 3 matches → ambiguous → return None (caller shows suggestions).
        if len(q_norm) > 4:
            substring_matches: list[object] = []
            for key_norm, value in norm_index.items():
                if q_norm in key_norm or key_norm in q_norm:
                    substring_matches.append(value)
            if len(substring_matches) == 1:
                return substring_matches[0]
            if len(substring_matches) > 1:
                return None  # ambiguous — caller returns suggestions

        # 3. difflib — length-gated to avoid false positives on short queries
        #    and queries that are substantially longer than the target.
        #    "pepperoncini" (12 chars) vs "Pepperoni" (9 chars) scores 0.857
        #    but is wrong — a query longer than the target suggests extra
        #    characters, not a typo. Require the query to be at most ~15%
        #    longer than the target.
        if len(q_norm) < 4:
            return None  # too short for reliable difflib matching

        threshold = 0.85 if len(q_norm) < 6 else 0.80
        best_score = 0.0
        best_value = None
        second_best = 0.0
        for key_norm, value in norm_index.items():
            # If the query is longer than the target, require high length
            # similarity — extra chars aren't typos.
            if len(q_norm) > len(key_norm):
                ratio = len(key_norm) / len(q_norm)
                if ratio < 0.85:
                    continue
            score = difflib.SequenceMatcher(None, q_norm, key_norm).ratio()
            if score > best_score:
                second_best = best_score
                best_score = score
                best_value = value
            elif score > second_best:
                second_best = score

        # Require a clear winner — best must beat the threshold AND lead
        # the runner-up by at least 0.05 (prevents near-ties).
        if best_score >= threshold and (best_score - second_best) >= 0.05:
            return best_value

        return None

    def get_deal(self, deal_id: str) -> DealDef | None:
        """Exact lookup by deal ID."""
        return self._deals_by_id.get(deal_id)

    # ------------------------------------------------------------------
    # Size Resolution
    # ------------------------------------------------------------------

    def resolve_size(
        self,
        product: ProductDef,
        size: str | None,
    ) -> tuple[str | None, list[str]]:
        """Validate and normalize a size for a product.

        Returns:
            (resolved_size, issues). Issues are non-blocking warnings
            like "invalid size, used default".
        """
        if product.sizes is None:
            # Flat-price item — size is not applicable
            if size is not None:
                return None, [f"{product.name} doesn't come in different sizes."]
            return None, []

        if size is None:
            return product.default_size, []

        size_lower = size.lower().strip()
        if size_lower in product.sizes:
            return size_lower, []

        # Invalid size — use default and warn
        valid = ", ".join(sorted(product.sizes.keys()))
        return product.default_size, [
            f"'{size}' is not a valid size for {product.name}. "
            f"Available sizes: {valid}. Using default '{product.default_size}'."
        ]

    # ------------------------------------------------------------------
    # Topping Resolution
    # ------------------------------------------------------------------

    def resolve_toppings(
        self,
        product: ProductDef,
        names: list[str],
    ) -> tuple[list[CartTopping], list[str]]:
        """Resolve topping names to CartTopping objects.

        Returns:
            (resolved_toppings, issues). Issues for unknown or unavailable toppings.
        """
        if not names:
            return [], []

        if not product.available_toppings:
            return [], [f"Toppings are not available for {product.name}."]

        issues: list[str] = []
        resolved: list[CartTopping] = []

        for name in names:
            topping = self.find_topping(name)
            if topping is None:
                issues.append(f"Unknown topping: '{name}'.")
                continue

            if topping.id not in product.available_toppings:
                issues.append(
                    f"'{topping.name}' is not available for {product.name}."
                )
                continue

            resolved.append(
                CartTopping(
                    topping_id=topping.id,
                    name=topping.name,
                    price=topping.price,
                )
            )

        return resolved, issues

    # ------------------------------------------------------------------
    # Menu Summary (for browse_menu tool)
    # ------------------------------------------------------------------

    def get_menu_summary(self) -> dict:
        """Full structured menu data for the browse_menu tool.

        Returns categories with items (name, description, sizes, topping names),
        deals, and delivery fee. Topping prices are NOT included — the LLM
        discovers those via add_to_cart responses.
        """
        categories: list[dict] = []
        for cat in self._menu_data.get("categories", []):
            items: list[dict] = []
            for item in cat.get("items", []):
                if item.get("available") is False:
                    continue
                sizes = item.get("sizes")
                unavailable = item.get("unavailable_variants", [])
                if sizes and unavailable:
                    sizes = {k: v for k, v in sizes.items() if k not in unavailable}
                    if not sizes:
                        continue
                entry: dict = {
                    "name": item["name"],
                    "description": item.get("description", ""),
                }
                if sizes:
                    entry["sizes"] = sizes
                else:
                    entry["price"] = item.get("price", 0.0)
                topping_ids = item.get("available_toppings", [])
                if topping_ids:
                    entry["available_toppings"] = [
                        self._toppings_by_id[tid].name
                        for tid in topping_ids
                        if tid in self._toppings_by_id
                    ]
                items.append(entry)
            if items:
                categories.append({"name": cat["name"], "items": items})

        deals: list[dict] = []
        for deal in self._menu_data.get("deals", []):
            deals.append({
                "name": deal["name"],
                "description": deal.get("description", ""),
                "price": deal["price"],
                "includes": deal.get("includes", {}),
            })

        return {
            "categories": categories,
            "deals": deals,
            "delivery_fee": self._menu_data.get("delivery_fee", 0.0),
        }

    # ------------------------------------------------------------------
    # Prompt Hints
    # ------------------------------------------------------------------

    def get_hints(self) -> str:
        """Lightweight menu summary for the system prompt.

        Category names + 2-3 popular items per category. NOT the full menu.
        Target: ~200 chars.
        """
        lines: list[str] = []
        for category in self._menu_data.get("categories", []):
            cat_name = category["name"]
            items = category.get("items", [])
            # Show first 3 items as examples
            examples = [item["name"] for item in items[:3]]
            if len(items) > 3:
                examples.append("...")
            lines.append(f"{cat_name}: {', '.join(examples)}")

        deals = self._menu_data.get("deals", [])
        if deals:
            deal_names = [d["name"] for d in deals[:3]]
            lines.append(f"Deals: {', '.join(deal_names)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # List Accessors
    # ------------------------------------------------------------------

    def get_categories(self) -> list[str]:
        return [cat["name"] for cat in self._menu_data.get("categories", [])]

    def get_toppings(self) -> list[ToppingDef]:
        return list(self._toppings_by_id.values())

    def get_deals(self) -> list[DealDef]:
        return list(self._deals_by_id.values())

    def get_product_suggestions(self, query: str, limit: int = 5) -> list[str]:
        """Return product and deal name suggestions for a failed lookup.

        More lenient than find_product — handles misspellings via
        character-sequence overlap (e.g. 'peproni' matches 'Pepperoni').
        Also searches deal names.
        """
        q = query.lower().strip()
        scored: list[tuple[int, str]] = []

        for product in self._products_by_id.values():
            name_lower = product.name.lower()
            score = 0
            if q == name_lower:
                score = 100
            elif q in name_lower:
                score = 50
            elif name_lower in q:
                score = 25
            else:
                overlap = self._seq_overlap(q, name_lower)
                if overlap >= 0.6:
                    score = int(overlap * 40)

            if score > 0:
                scored.append((score, product.name))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [name for _, name in scored[:limit]]

    @staticmethod
    def _seq_overlap(query: str, target: str) -> float:
        """Fraction of query characters that appear in target in order.

        e.g. 'peproni' vs 'pepperoni' → all chars appear in order → 1.0
             'mrgherita' vs 'margherita' → most chars → ~0.9
             'zzzz' vs 'pizza' → 0 chars → 0.0
        """
        if not query:
            return 0.0
        qi = 0
        for ch in target:
            if qi < len(query) and ch == query[qi]:
                qi += 1
            if qi >= len(query):
                break
        return qi / len(query)

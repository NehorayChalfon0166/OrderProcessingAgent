"""
menu_manager.py — Menu loading, validation, and lookup for the pizzeria agent.

Loads the menu from JSON once at startup, builds fast lookup indexes, and
provides methods to resolve fuzzy user input (via the LLM) into validated
menu items with correct IDs and prices.
"""

import json
from pathlib import Path

from models import ExtractedItem, OrderItem, ToppingSelection


class MenuManager:
    """
    Loads and manages the restaurant menu.

    Provides:
    - Fast lookup of items and toppings by ID or name
    - Simple fuzzy matching (exact → lowercase → substring)
    - Validation of LLM-extracted items against the real menu
    - Text formatting for LLM system prompt injection
    """

    def __init__(self, menu_path: str) -> None:
        self._menu_data = self._load_menu(menu_path)
        self._items_by_id: dict[str, dict] = {}
        self._items_by_name: dict[str, dict] = {}  # lowercased name → item
        self._toppings_by_id: dict[str, dict] = {}
        self._toppings_by_name: dict[str, dict] = {}  # lowercased name → topping
        self._build_indexes()

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def menu_data(self) -> dict:
        """Raw menu data dict (loaded from JSON)."""
        return self._menu_data

    @property
    def restaurant_name(self) -> str:
        """The restaurant name from the menu."""
        return self._menu_data.get("restaurant_name", "Restaurant")

    # -------------------------------------------------------------------------
    # Loading & Indexing
    # -------------------------------------------------------------------------

    @staticmethod
    def _load_menu(path: str) -> dict:
        """
        Load and parse the menu JSON file.

        Args:
            path: Path to the menu.json file.

        Returns:
            Parsed menu data as a dict.

        Raises:
            FileNotFoundError: If the menu file doesn't exist.
            json.JSONDecodeError: If the file contains invalid JSON.
        """
        menu_file = Path(path)
        if not menu_file.exists():
            raise FileNotFoundError(
                f"Menu file not found at '{menu_file.resolve()}'. "
                f"Make sure menu.json exists in the project directory."
            )
        with open(menu_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_indexes(self) -> None:
        """
        Build fast lookup dictionaries for items and toppings.

        Items are indexed by both their ID and lowercased display name.
        Each indexed item includes its parent category name for convenience.
        """
        # Index items across all categories
        for category in self._menu_data.get("categories", []):
            category_name = category.get("name", "")
            for item in category.get("items", []):
                entry = {**item, "category": category_name}
                self._items_by_id[item["id"]] = entry
                self._items_by_name[item["name"].lower()] = entry

        # Index toppings
        for topping in self._menu_data.get("toppings", []):
            self._toppings_by_id[topping["id"]] = topping
            self._toppings_by_name[topping["name"].lower()] = topping

    # -------------------------------------------------------------------------
    # Item Lookup (fuzzy matching)
    # -------------------------------------------------------------------------

    def find_item(self, name: str) -> dict | None:
        """
        Find a menu item by name with simple fuzzy matching.

        Match priority:
          1. Exact case-insensitive match on item name
          2. Substring match (user's query is contained in an item name)
          3. Reverse substring (item name is contained in user's query)

        Args:
            name: The item name to search for (from user/LLM input).

        Returns:
            The full item dict (with 'category' included) or None if not found.
        """
        query = name.lower().strip()

        # 1) Exact match on lowered name
        if query in self._items_by_name:
            return self._items_by_name[query]

        # 2) Substring: user query is contained in a menu item name
        for item_name, item_data in self._items_by_name.items():
            if query in item_name:
                return item_data

        # 3) Reverse substring: menu item name is contained in user query
        for item_name, item_data in self._items_by_name.items():
            if item_name in query:
                return item_data

        return None

    def find_item_by_id(self, item_id: str) -> dict | None:
        """Look up an item by its exact ID."""
        return self._items_by_id.get(item_id)

    def find_topping(self, name: str) -> dict | None:
        """
        Find a topping by name with simple fuzzy matching.

        Match priority:
          1. Exact case-insensitive match on topping name
          2. Substring match (user's query contained in topping name)
          3. Reverse substring (topping name contained in user's query)
          4. Match by topping ID (e.g. 'extra_cheese')

        Args:
            name: The topping name to search for.

        Returns:
            The topping dict or None if not found.
        """
        query = name.lower().strip()

        # 1) Exact match on lowered name
        if query in self._toppings_by_name:
            return self._toppings_by_name[query]

        # 2) Substring: query in topping name
        for topping_name, topping_data in self._toppings_by_name.items():
            if query in topping_name:
                return topping_data

        # 3) Reverse substring: topping name in query
        for topping_name, topping_data in self._toppings_by_name.items():
            if topping_name in query:
                return topping_data

        # 4) Match by ID (handles cases like 'extra_cheese')
        if query.replace(" ", "_") in self._toppings_by_id:
            return self._toppings_by_id[query.replace(" ", "_")]

        return None

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_size(self, item: dict, size: str | None) -> str | None:
        """
        Check if the size is valid for a menu item.

        Args:
            item: The menu item dict.
            size: The requested size string.

        Returns:
            The normalized size string if valid, or None if invalid.
            If the item has no sizes (flat price), returns None.
        """
        sizes = item.get("sizes")
        if sizes is None:
            # Flat-price item — size is not applicable
            return None

        if size is None:
            # No size specified — use default
            return item.get("default_size")

        # Normalize to lowercase
        size_lower = size.lower().strip()

        if size_lower in sizes:
            return size_lower

        # No valid match
        return None

    def validate_extracted_item(
        self, extracted: ExtractedItem
    ) -> tuple[OrderItem | None, list[str]]:
        """
        Validate an LLM-extracted item against the real menu.

        Resolves the item name to a menu entry, validates the size,
        resolves toppings, and builds a clean OrderItem. Any issues
        (unknown item, bad size, unknown toppings) are collected into
        the issues list so the caller can inform the user.

        Args:
            extracted: The raw ExtractedItem from the LLM.

        Returns:
            A tuple of (validated_order_item, list_of_issues).
            If the item itself can't be found, returns (None, issues).
        """
        issues: list[str] = []

        # --- Resolve item ---
        menu_item = self.find_item(extracted.name)
        if menu_item is None:
            issues.append(f"Could not find '{extracted.name}' on the menu.")
            return None, issues

        # --- Resolve size ---
        has_sizes = "sizes" in menu_item
        if has_sizes:
            resolved_size = self.validate_size(menu_item, extracted.size)
            if extracted.size is not None and resolved_size is None:
                # User asked for an invalid size
                valid_sizes = ", ".join(menu_item["sizes"].keys())
                issues.append(
                    f"'{extracted.size}' is not a valid size for {menu_item['name']}. "
                    f"Available sizes: {valid_sizes}. Using default size '{menu_item.get('default_size')}'."
                )
                resolved_size = menu_item.get("default_size")
        else:
            resolved_size = None
            if extracted.size is not None:
                issues.append(
                    f"{menu_item['name']} doesn't come in different sizes."
                )

        # --- Resolve toppings ---
        # Only pizzas support toppings
        available_toppings = menu_item.get("available_toppings", [])
        resolved_toppings: list[ToppingSelection] = []

        for topping_name in extracted.toppings:
            topping = self.find_topping(topping_name)
            if topping is None:
                issues.append(f"Unknown topping: '{topping_name}'.")
                continue

            # Check if this topping is allowed for this item
            if available_toppings and topping["id"] not in available_toppings:
                issues.append(
                    f"'{topping['name']}' is not available for {menu_item['name']}."
                )
                continue

            # Check if this item's category supports toppings at all
            if not available_toppings and extracted.toppings:
                issues.append(
                    f"Toppings are not available for {menu_item['name']}."
                )
                break

            resolved_toppings.append(
                ToppingSelection(
                    topping_id=topping["id"],
                    topping_name=topping["name"],
                    price=topping["price"],
                )
            )

        # --- Build OrderItem ---
        order_item = OrderItem(
            item_id=menu_item["id"],
            item_name=menu_item["name"],
            category=menu_item["category"],
            size=resolved_size,
            quantity=max(1, extracted.quantity),
            toppings=resolved_toppings,
            special_instructions=extracted.special_instructions,
        )

        return order_item, issues

    # -------------------------------------------------------------------------
    # Menu Formatting for LLM Prompt
    # -------------------------------------------------------------------------

    def format_menu_for_prompt(self) -> str:
        """
        Format the entire menu as structured text for LLM system prompt injection.

        Produces a clean, human-readable representation including all categories,
        items, prices, sizes, available toppings, and active deals. This is
        injected into the system prompt so the LLM knows what's on the menu.

        Returns:
            A multi-line string containing the formatted menu.
        """
        lines: list[str] = []
        menu = self._menu_data
        currency = menu.get("currency", "USD")

        lines.append(f"=== {menu.get('restaurant_name', 'Restaurant')} Menu ===")
        lines.append("")

        # --- Categories and items ---
        for category in menu.get("categories", []):
            lines.append(f"--- {category['name']} ---")

            for item in category.get("items", []):
                # Item name and description
                lines.append(f"  {item['name']}: {item.get('description', '')}")

                # Price display
                if "sizes" in item:
                    size_parts = [
                        f"{size.capitalize()}: ${price:.2f}"
                        for size, price in item["sizes"].items()
                    ]
                    default = item.get("default_size", "")
                    lines.append(
                        f"    Sizes: {' | '.join(size_parts)} (default: {default})"
                    )
                elif "price" in item:
                    lines.append(f"    Price: ${item['price']:.2f}")

                # Available toppings (only for items that have them)
                if item.get("available_toppings"):
                    topping_names = []
                    for tid in item["available_toppings"]:
                        t = self._toppings_by_id.get(tid)
                        if t:
                            topping_names.append(t["name"])
                    lines.append(f"    Available toppings: {', '.join(topping_names)}")

            lines.append("")

        # --- Toppings reference ---
        lines.append("--- Extra Toppings ---")
        for topping in menu.get("toppings", []):
            lines.append(f"  {topping['name']}: +${topping['price']:.2f}")
        lines.append("")

        # --- Deals ---
        deals = menu.get("deals", [])
        if deals:
            lines.append("--- Deals & Specials ---")
            for deal in deals:
                lines.append(f"  {deal['name']}: ${deal['price']:.2f}")
                lines.append(f"    {deal.get('description', '')}")
            lines.append("")

        # --- Delivery info ---
        lines.append("--- Ordering Info ---")
        lines.append(f"  Delivery fee: ${menu.get('delivery_fee', 0):.2f}")
        lines.append(f"  Minimum order: ${menu.get('min_order_amount', 0):.2f}")
        lines.append(
            f"  Estimated delivery time: {menu.get('estimated_delivery_time', 'N/A')}"
        )
        lines.append("")

        return "\n".join(lines)

    def get_categories(self) -> list[str]:
        """Return a list of category names from the menu."""
        return [cat["name"] for cat in self._menu_data.get("categories", [])]

    def get_deals(self) -> list[dict]:
        """Return the list of deals from the menu."""
        return self._menu_data.get("deals", [])

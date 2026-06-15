"""Pricing engine for the order processing agent.

Python owns ALL price computation. The LLM extracts what the customer wants
via tool calls; this module resolves every dollar amount from the single
source of truth (menu.json). This separation ensures prices are always
accurate regardless of LLM behavior.

Adapted from v1 — OrderItem → CartItem, ToppingSelection → CartTopping.
Logic unchanged.
"""

from __future__ import annotations

from models import CartItem, OrderType


class PricingEngine:
    """Computes prices for cart items and full orders.

    Built from the raw menu dict loaded from menu.json. Internally maintains
    fast lookup tables so pricing is O(1) per item.
    """

    def __init__(self, menu_data: dict) -> None:
        # Flat lookup: item_id → full item dict from menu
        self._items: dict[str, dict] = {}
        # Flat lookup: topping_id → full topping dict from menu
        self._toppings: dict[str, dict] = {}
        # Delivery fee and minimum from the menu
        self._delivery_fee: float = menu_data.get("delivery_fee", 0.0)
        self._min_order_amount: float = menu_data.get("min_order_amount", 0.0)

        self._build_lookups(menu_data)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def delivery_fee(self) -> float:
        return self._delivery_fee

    @property
    def min_order_amount(self) -> float:
        return self._min_order_amount

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _build_lookups(self, menu_data: dict) -> None:
        """Index all menu items and toppings by their ID for fast lookup."""
        for category in menu_data.get("categories", []):
            category_name = category.get("name", "")
            for item in category.get("items", []):
                item_entry = {**item, "category": category_name}
                self._items[item["id"]] = item_entry

        for topping in menu_data.get("toppings", []):
            self._toppings[topping["id"]] = topping

    # ------------------------------------------------------------------
    # Price Resolution
    # ------------------------------------------------------------------

    def get_item_base_price(self, item_id: str, size: str | None = None) -> float:
        """Look up the base price for a menu item.

        Args:
            item_id: The menu item identifier.
            size: The selected size (e.g. 'small', 'medium', 'large').

        Returns:
            The base price as a float.

        Raises:
            ValueError: If the item ID is not found in the menu.
            ValueError: If the item requires a size but none/invalid provided.
        """
        item = self._items.get(item_id)
        if item is None:
            raise ValueError(f"Unknown item ID: '{item_id}'")

        # Flat-price item (no sizes)
        if "price" in item:
            return float(item["price"])

        # Sized item — must have a valid size
        sizes = item.get("sizes", {})
        if not sizes:
            raise ValueError(
                f"Item '{item_id}' has neither 'price' nor 'sizes' in menu data"
            )

        if size is None:
            size = item.get("default_size")

        if size not in sizes:
            valid = ", ".join(sorted(sizes.keys()))
            raise ValueError(
                f"Invalid size '{size}' for item '{item_id}'. Valid sizes: {valid}"
            )

        return float(sizes[size])

    def get_topping_price(self, topping_id: str) -> float:
        """Look up the price of a topping.

        Raises:
            ValueError: If the topping ID is not found in the menu.
        """
        topping = self._toppings.get(topping_id)
        if topping is None:
            raise ValueError(f"Unknown topping ID: '{topping_id}'")
        return float(topping["price"])

    # ------------------------------------------------------------------
    # Pricing Operations
    # ------------------------------------------------------------------

    def price_item(self, cart_item: CartItem) -> CartItem:
        """Compute base_price and line_total for a CartItem.

        Resolves the base price from the menu (by item_id + size), adds up
        all topping surcharges, and multiplies by quantity.

        Returns:
            A new CartItem with base_price and line_total filled in.
        """
        base_price = self.get_item_base_price(cart_item.product_id, cart_item.size)

        toppings_total = sum(t.price for t in cart_item.toppings)
        unit_price = base_price + toppings_total
        line_total = round(unit_price * cart_item.quantity, 2)

        return cart_item.model_copy(
            update={
                "base_price": base_price,
                "line_total": line_total,
            }
        )

    def compute_totals(
        self, items: list[CartItem], order_type: OrderType
    ) -> tuple[float, float, float]:
        """Compute full order totals.

        Returns:
            (subtotal, delivery_fee, total).
            delivery_fee is 0.0 for pickup orders.
        """
        subtotal = round(sum(item.line_total for item in items), 2)
        delivery_fee = (
            self._delivery_fee if order_type == OrderType.DELIVERY else 0.0
        )
        total = round(subtotal + delivery_fee, 2)
        return subtotal, delivery_fee, total

    def check_minimum_order(self, subtotal: float) -> bool:
        """Check if the subtotal meets the minimum order requirement."""
        return subtotal >= self._min_order_amount

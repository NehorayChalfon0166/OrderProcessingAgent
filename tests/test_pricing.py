"""Tests for pricing.py — price resolution and order totals."""

import pytest

from catalogue import Catalogue
from models import CartItem, CartTopping, OrderType
from pricing import PricingEngine


@pytest.fixture
def pricing():
    """PricingEngine backed by the real menu.json."""
    from catalogue import Catalogue
    catalogue = Catalogue("menu.json")
    return PricingEngine(catalogue.menu_data)


@pytest.fixture
def margherita():
    """A basic Margherita pizza cart item."""
    return CartItem(
        product_id="pizza_margherita",
        name="Margherita",
        category="Pizzas",
        size="medium",
        quantity=1,
        toppings=[
            CartTopping(topping_id="extra_cheese", name="Extra Cheese", price=1.50),
        ],
    )


class TestBasePrice:
    def test_sized_item(self, pricing):
        price = pricing.get_item_base_price("pizza_margherita", "large")
        assert price == 15.99

    def test_sized_item_different_size(self, pricing):
        price = pricing.get_item_base_price("pizza_margherita", "small")
        assert price == 9.99

    def test_flat_price_item(self, pricing):
        price = pricing.get_item_base_price("side_garlic_bread")
        assert price == 4.99

    def test_unknown_item(self, pricing):
        with pytest.raises(ValueError, match="Unknown item ID"):
            pricing.get_item_base_price("nonexistent")

    def test_invalid_size(self, pricing):
        with pytest.raises(ValueError, match="Invalid size"):
            pricing.get_item_base_price("pizza_margherita", "xlarge")


class TestToppingPrice:
    def test_topping_price(self, pricing):
        price = pricing.get_topping_price("extra_cheese")
        assert price == 1.50

    def test_unknown_topping(self, pricing):
        with pytest.raises(ValueError, match="Unknown topping"):
            pricing.get_topping_price("nonexistent")


class TestPriceItem:
    def test_sets_base_price(self, pricing, margherita):
        priced = pricing.price_item(margherita)
        assert priced.base_price == 12.99  # medium margherita

    def test_sets_line_total(self, pricing, margherita):
        priced = pricing.price_item(margherita)
        # (12.99 + 1.50) * 1 = 14.49
        assert priced.line_total == 14.49

    def test_line_total_matches_quantity(self, pricing):
        item = CartItem(
            product_id="pizza_margherita",
            name="Margherita",
            category="Pizzas",
            size="medium",
            quantity=3,
            toppings=[],
        )
        priced = pricing.price_item(item)
        assert priced.base_price == 12.99
        assert priced.line_total == 38.97  # 12.99 * 3

    def test_with_multiple_toppings(self, pricing):
        item = CartItem(
            product_id="pizza_margherita",
            name="Margherita",
            category="Pizzas",
            size="medium",
            quantity=1,
            toppings=[
                CartTopping(topping_id="extra_cheese", name="Extra Cheese", price=1.50),
                CartTopping(topping_id="bacon", name="Bacon", price=1.75),
            ],
        )
        priced = pricing.price_item(item)
        assert priced.line_total == 16.24  # (12.99 + 1.50 + 1.75)

    def test_flat_price_item(self, pricing):
        item = CartItem(
            product_id="side_caesar_salad",
            name="Caesar Salad",
            category="Sides",
            quantity=2,
        )
        priced = pricing.price_item(item)
        assert priced.base_price == 7.49
        assert priced.line_total == 14.98


class TestComputeTotals:
    def test_pickup_no_delivery_fee(self, pricing):
        items = [
            CartItem(
                product_id="pizza_margherita",
                name="Margherita",
                category="Pizzas",
                size="medium",
                quantity=1,
                base_price=12.99,
                line_total=12.99,
            ),
        ]
        subtotal, delivery_fee, total = pricing.compute_totals(items, OrderType.PICKUP)
        assert subtotal == 12.99
        assert delivery_fee == 0.0
        assert total == 12.99

    def test_delivery_adds_fee(self, pricing):
        items = [
            CartItem(
                product_id="pizza_margherita",
                name="Margherita",
                category="Pizzas",
                size="medium",
                quantity=1,
                base_price=12.99,
                line_total=12.99,
            ),
        ]
        subtotal, delivery_fee, total = pricing.compute_totals(
            items, OrderType.DELIVERY
        )
        assert subtotal == 12.99
        assert delivery_fee == 3.99
        assert total == 16.98

    def test_multiple_items(self, pricing):
        items = [
            CartItem(product_id="a", name="A", category="X", quantity=1, base_price=10.0, line_total=10.0),
            CartItem(product_id="b", name="B", category="X", quantity=1, base_price=15.0, line_total=15.0),
        ]
        subtotal, _, total = pricing.compute_totals(items, OrderType.PICKUP)
        assert subtotal == 25.0
        assert total == 25.0

    def test_empty_cart(self, pricing):
        subtotal, _, total = pricing.compute_totals([], OrderType.PICKUP)
        assert subtotal == 0.0
        assert total == 0.0


class TestMinimumOrder:
    def test_meets_minimum(self, pricing):
        assert pricing.check_minimum_order(10.00) is True

    def test_below_minimum(self, pricing):
        assert pricing.check_minimum_order(5.00) is False

    def test_exact_minimum(self, pricing):
        assert pricing.check_minimum_order(pricing.min_order_amount) is True

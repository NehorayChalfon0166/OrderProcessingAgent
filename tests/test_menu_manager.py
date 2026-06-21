"""Tests for menu_manager.py — menu editing and atomic writes."""

import json
from pathlib import Path

import pytest

from menu_manager import MenuAction, MenuActionResult, manage_menu


@pytest.fixture
def menu_file(tmp_path):
    """Create a temporary menu file for testing."""
    menu = {
        "restaurant_name": "Test Pizzeria",
        "currency": "USD",
        "categories": [
            {
                "name": "Pizzas",
                "items": [
                    {
                        "id": "pizza_margherita",
                        "name": "Margherita",
                        "description": "Classic",
                        "sizes": {"small": 9.99, "medium": 12.99, "large": 15.99},
                        "default_size": "medium",
                        "available_toppings": ["extra_cheese"],
                    },
                    {
                        "id": "side_bread",
                        "name": "Garlic Bread",
                        "description": "Buttery",
                        "price": 4.99,
                    },
                ],
            },
        ],
        "toppings": [
            {"id": "extra_cheese", "name": "Extra Cheese", "price": 1.50},
        ],
        "deals": [],
        "delivery_fee": 3.99,
    }
    path = tmp_path / "menu.json"
    path.write_text(json.dumps(menu))
    return str(path)


class TestManageMenu:
    def test_set_price_with_variant(self, menu_file):
        actions = [MenuAction("set_price", "pizza_margherita", variant_id="large", value=60.0)]
        result = manage_menu(menu_file, actions)
        assert result.success
        assert result.actions_applied == 1

        menu = json.loads(Path(menu_file).read_text())
        assert menu["categories"][0]["items"][0]["sizes"]["large"] == 60.0

    def test_set_price_flat_item(self, menu_file):
        actions = [MenuAction("set_price", "side_bread", value=5.99)]
        result = manage_menu(menu_file, actions)
        assert result.success

        menu = json.loads(Path(menu_file).read_text())
        assert menu["categories"][0]["items"][1]["price"] == 5.99

    def test_set_price_requires_value(self, menu_file):
        actions = [MenuAction("set_price", "pizza_margherita", variant_id="large")]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert "requires a value" in result.errors[0]

    def test_set_price_negative_fails(self, menu_file):
        actions = [MenuAction("set_price", "pizza_margherita", variant_id="large", value=-5)]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert "positive" in result.errors[0].lower()

    def test_out_of_stock_and_back(self, menu_file):
        # Mark out of stock
        result = manage_menu(menu_file, [
            MenuAction("out_of_stock", "pizza_margherita", variant_id="large"),
        ])
        assert result.success
        menu = json.loads(Path(menu_file).read_text())
        assert "large" in menu["categories"][0]["items"][0]["unavailable_variants"]

        # Mark back in stock
        result = manage_menu(menu_file, [
            MenuAction("in_stock", "pizza_margherita", variant_id="large"),
        ])
        assert result.success
        menu = json.loads(Path(menu_file).read_text())
        assert "unavailable_variants" not in menu["categories"][0]["items"][0]

    def test_out_of_stock_whole_item(self, menu_file):
        result = manage_menu(menu_file, [
            MenuAction("out_of_stock", "side_bread"),
        ])
        assert result.success
        menu = json.loads(Path(menu_file).read_text())
        assert menu["categories"][0]["items"][1]["available"] is False

    def test_describe(self, menu_file):
        actions = [MenuAction("describe", "pizza_margherita", value="New description")]
        result = manage_menu(menu_file, actions)
        assert result.success

        menu = json.loads(Path(menu_file).read_text())
        assert menu["categories"][0]["items"][0]["description"] == "New description"

    def test_unknown_item(self, menu_file):
        actions = [MenuAction("set_price", "nonexistent", value=10)]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert "not found" in result.errors[0]

    def test_unknown_action(self, menu_file):
        actions = [MenuAction("invalid_action", "pizza_margherita")]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert "Unknown action" in result.errors[0]

    def test_invalid_variant(self, menu_file):
        actions = [MenuAction("set_price", "pizza_margherita", variant_id="gigantic", value=99)]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert "not found" in result.errors[0]

    def test_variant_on_flat_item(self, menu_file):
        actions = [MenuAction("set_price", "side_bread", variant_id="large", value=10)]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert "not sized" in result.errors[0]

    def test_atomic_no_partial_write(self, menu_file):
        """If one action fails, no changes are written."""
        original = Path(menu_file).read_text()
        actions = [
            MenuAction("set_price", "pizza_margherita", variant_id="large", value=60.0),
            MenuAction("set_price", "nonexistent", value=10),  # fails
        ]
        result = manage_menu(menu_file, actions)
        assert not result.success
        assert Path(menu_file).read_text() == original  # unchanged

    def test_batch_all_succeed(self, menu_file):
        actions = [
            MenuAction("set_price", "pizza_margherita", variant_id="large", value=60.0),
            MenuAction("describe", "side_bread", value="Warm bread"),
        ]
        result = manage_menu(menu_file, actions)
        assert result.success
        assert result.actions_applied == 2

    def test_missing_file(self):
        result = manage_menu("nonexistent_menu.json", [])
        assert not result.success
        assert "not found" in result.message.lower()

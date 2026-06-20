"""Tests for restaurant.py — multi-tenant restaurant registry."""

import json
import tempfile
from pathlib import Path

import pytest

from restaurant import RestaurantConfig, RestaurantContext, RestaurantRegistry


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_menu_json(path: Path, name: str = "Test Restaurant") -> None:
    """Write a minimal valid menu JSON file."""
    path.write_text(json.dumps({
        "restaurant_name": name,
        "currency": "USD",
        "categories": [
            {
                "name": "Pizzas",
                "items": [
                    {
                        "id": "pizza_test",
                        "name": "Test Pizza",
                        "description": "A test pizza",
                        "sizes": {"small": 9.99, "medium": 12.99, "large": 15.99},
                        "default_size": "medium",
                        "available_toppings": ["extra_cheese"],
                    }
                ],
            }
        ],
        "toppings": [
            {"id": "extra_cheese", "name": "Extra Cheese", "price": 1.50}
        ],
        "deals": [],
        "delivery_fee": 3.99,
        "min_order_amount": 10.00,
        "estimated_delivery_time": "30-45 min",
    }))


def _make_restaurants_json(path: Path, restaurants: dict) -> None:
    """Write a restaurants.json file."""
    path.write_text(json.dumps({"restaurants": restaurants}))


# ---------------------------------------------------------------------------
# RestaurantConfig
# ---------------------------------------------------------------------------


class TestRestaurantConfig:
    def test_frozen(self):
        config = RestaurantConfig(
            id="test_id",
            name="Test",
            menu_path="menus/test.json",
            twilio_phone="+1234567890",
            owner_phone="+15551234567",
        )
        with pytest.raises(Exception):
            config.name = "Changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RestaurantRegistry — loading
# ---------------------------------------------------------------------------


class TestRestaurantRegistryLoading:
    def test_loads_single_restaurant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu, "Mario's Pizzeria")
            restaurants_file = tmp / "restaurants.json"
            _make_restaurants_json(restaurants_file, {
                "marios": {
                    "name": "Mario's Pizzeria",
                    "menu_path": str(menu),
                    "twilio_phone": "+14155238886",
                    "owner_phone": "+15551234567",
                }
            })

            registry = RestaurantRegistry(str(restaurants_file))
            ctx = registry.get_by_id("marios")
            assert ctx is not None
            assert ctx.config.id == "marios"
            assert ctx.config.name == "Mario's Pizzeria"
            assert ctx.config.twilio_phone == "+14155238886"

    def test_loads_multiple_restaurants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu_a = tmp / "menu_a.json"
            menu_b = tmp / "menu_b.json"
            _make_menu_json(menu_a, "Restaurant A")
            _make_menu_json(menu_b, "Restaurant B")
            _make_restaurants_json(tmp / "restaurants.json", {
                "rest_a": {
                    "name": "Restaurant A",
                    "menu_path": str(menu_a),
                    "twilio_phone": "+1111111111",
                    "owner_phone": "+15551234567",
                },
                "rest_b": {
                    "name": "Restaurant B",
                    "menu_path": str(menu_b),
                    "twilio_phone": "+2222222222",
                    "owner_phone": "+15551234567",
                },
            })

            registry = RestaurantRegistry(str(tmp / "restaurants.json"))
            assert registry.get_by_id("rest_a") is not None
            assert registry.get_by_id("rest_b") is not None
            assert len(registry.list_restaurants()) == 2

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            RestaurantRegistry("nonexistent_restaurants.json")

    def test_empty_restaurants_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _make_restaurants_json(tmp / "restaurants.json", {})
            with pytest.raises(ValueError, match="No restaurants defined"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_missing_name_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "menu_path": str(menu),
                    "twilio_phone": "+1234567890",
                    "owner_phone": "+15551234567",
                }
            })
            with pytest.raises(ValueError, match="missing required field 'name'"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_missing_menu_path_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "name": "Bad Restaurant",
                    "twilio_phone": "+1234567890",
                    "owner_phone": "+15551234567",
                }
            })
            with pytest.raises(ValueError, match="missing required field 'menu_path'"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_missing_twilio_phone_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "name": "Bad Restaurant",
                    "menu_path": str(menu),
                }
            })
            with pytest.raises(ValueError, match="missing required field 'twilio_phone'"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_empty_twilio_phone_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "name": "Bad Restaurant",
                    "menu_path": str(menu),
                    "twilio_phone": "",
                }
            })
            with pytest.raises(ValueError, match="missing required field 'twilio_phone'"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_missing_owner_phone_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "name": "Bad Restaurant",
                    "menu_path": str(menu),
                    "twilio_phone": "+1234567890",
                }
            })
            with pytest.raises(ValueError, match="missing required field 'owner_phone'"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_empty_owner_phone_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "name": "Bad Restaurant",
                    "menu_path": str(menu),
                    "twilio_phone": "+1234567890",
                    "owner_phone": "",
                }
            })
            with pytest.raises(ValueError, match="missing required field 'owner_phone'"):
                RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_nonexistent_menu_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            _make_restaurants_json(tmp / "restaurants.json", {
                "bad": {
                    "name": "Bad Restaurant",
                    "menu_path": "nonexistent_menu.json",
                    "twilio_phone": "+1234567890",
                    "owner_phone": "+15551234567",
                }
            })
            with pytest.raises(FileNotFoundError):
                RestaurantRegistry(str(tmp / "restaurants.json"))


# ---------------------------------------------------------------------------
# RestaurantRegistry — lookups
# ---------------------------------------------------------------------------


class TestRestaurantRegistryLookups:
    @pytest.fixture
    def registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu_a = tmp / "menu_a.json"
            menu_b = tmp / "menu_b.json"
            _make_menu_json(menu_a, "Restaurant A")
            _make_menu_json(menu_b, "Restaurant B")
            _make_restaurants_json(tmp / "restaurants.json", {
                "rest_a": {
                    "name": "Restaurant A",
                    "menu_path": str(menu_a),
                    "twilio_phone": "+1111111111",
                    "owner_phone": "+15551234567",
                },
                "rest_b": {
                    "name": "Restaurant B",
                    "menu_path": str(menu_b),
                    "twilio_phone": "+2222222222",
                    "owner_phone": "+15551234567",
                },
            })
            yield RestaurantRegistry(str(tmp / "restaurants.json"))

    def test_get_by_id_returns_correct(self, registry):
        ctx = registry.get_by_id("rest_a")
        assert ctx is not None
        assert ctx.config.name == "Restaurant A"

    def test_get_by_id_nonexistent(self, registry):
        assert registry.get_by_id("nonexistent") is None

    def test_get_by_twilio_phone_returns_correct(self, registry):
        ctx = registry.get_by_twilio_phone("+1111111111")
        assert ctx is not None
        assert ctx.config.id == "rest_a"

    def test_get_by_twilio_phone_nonexistent(self, registry):
        assert registry.get_by_twilio_phone("+0000000000") is None

    def test_get_by_twilio_phone_strips_whatsapp_prefix(self, registry):
        ctx = registry.get_by_twilio_phone("whatsapp:+1111111111")
        assert ctx is not None
        assert ctx.config.id == "rest_a"

    def test_get_default_returns_first(self, registry):
        ctx = registry.get_default()
        assert ctx.config.id == "rest_a"

    def test_list_restaurants(self, registry):
        configs = registry.list_restaurants()
        assert len(configs) == 2
        ids = {c.id for c in configs}
        assert ids == {"rest_a", "rest_b"}

    def test_get_default_works_with_single_restaurant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "only": {
                    "name": "Only Restaurant",
                    "menu_path": str(menu),
                    "twilio_phone": "+1234567890",
                    "owner_phone": "+15551234567",
                }
            })
            registry = RestaurantRegistry(str(tmp / "restaurants.json"))
            # Should work fine with one restaurant
            assert registry.get_default().config.id == "only"


# ---------------------------------------------------------------------------
# RestaurantContext
# ---------------------------------------------------------------------------


class TestRestaurantContext:
    def test_context_has_catalogue_and_pricing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            menu = tmp / "menu.json"
            _make_menu_json(menu)
            _make_restaurants_json(tmp / "restaurants.json", {
                "test": {
                    "name": "Test Restaurant",
                    "menu_path": str(menu),
                    "twilio_phone": "+1234567890",
                    "owner_phone": "+15551234567",
                }
            })

            registry = RestaurantRegistry(str(tmp / "restaurants.json"))
            ctx = registry.get_by_id("test")
            assert ctx is not None
            assert isinstance(ctx, RestaurantContext)
            assert ctx.catalogue is not None
            assert ctx.pricing is not None
            assert ctx.catalogue.restaurant_name == "Test Restaurant"

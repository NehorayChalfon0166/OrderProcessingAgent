"""Pytest configuration — shared fixtures for all tests."""

import json
from pathlib import Path

import pytest

from catalogue import Catalogue
from db import Database
from pricing import PricingEngine
from restaurant import RestaurantRegistry
from session import OrderSession


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------


def make_test_menu(path: Path, name: str = "Test Restaurant", items=None, deals=None):
    """Write a test menu JSON file. Reusable across integration and unit tests."""
    if items is None:
        items = [
            {
                "id": "pizza_margherita", "name": "Margherita",
                "description": "Classic pizza",
                "sizes": {"small": 9.99, "medium": 12.99, "large": 15.99},
                "default_size": "medium",
                "available_toppings": ["extra_cheese", "mushrooms", "olives"],
            },
            {
                "id": "pizza_pepperoni", "name": "Pepperoni",
                "description": "Pepperoni pizza",
                "sizes": {"small": 10.99, "medium": 13.99, "large": 16.99},
                "default_size": "medium",
                "available_toppings": ["extra_cheese", "mushrooms"],
            },
            {
                "id": "side_garlic_bread", "name": "Garlic Bread",
                "description": "Buttered bread", "price": 4.99,
            },
            {
                "id": "drink_cola", "name": "Cola",
                "description": "Soda",
                "sizes": {"regular": 1.99, "large": 2.99},
                "default_size": "regular",
            },
        ]
    if deals is None:
        deals = [
            {
                "id": "deal_family", "name": "Family Deal",
                "description": "2 large pizzas + side + 2 large drinks",
                "price": 34.99,
            },
            {
                "id": "deal_couple", "name": "Couple's Special",
                "description": "2 pizzas", "price": 24.99,
            },
        ]
    menu = {
        "restaurant_name": name, "currency": "USD",
        "categories": [
            {"name": "Pizzas", "items": [i for i in items if i["id"].startswith("pizza")]},
            {"name": "Sides", "items": [i for i in items if i["id"].startswith("side")]},
            {"name": "Drinks", "items": [i for i in items if i["id"].startswith("drink")]},
        ],
        "toppings": [
            {"id": "extra_cheese", "name": "Extra Cheese", "price": 1.50},
            {"id": "mushrooms", "name": "Mushrooms", "price": 1.00},
            {"id": "olives", "name": "Olives", "price": 1.00},
        ],
        "deals": deals,
        "delivery_fee": 3.99,
        "min_order_amount": 10.00,
    }
    path.write_text(json.dumps(menu))
    return menu


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def menu_path(tmp_path):
    p = tmp_path / "test_menu.json"
    make_test_menu(p)
    return str(p)


@pytest.fixture
def catalogue(menu_path):
    return Catalogue(menu_path)


@pytest.fixture
def pricing(catalogue):
    return PricingEngine(catalogue.menu_data)


@pytest.fixture
def session():
    s = OrderSession()
    s.restaurant_id = "test"
    return s


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def db_session(session, db):
    session._db = db
    session.save()
    return session


@pytest.fixture
def registry_path(tmp_path):
    """Create a restaurants.json pointing at two independent menus."""
    menu_a = tmp_path / "menu_a.json"
    menu_b = tmp_path / "menu_b.json"
    make_test_menu(menu_a, "Restaurant A")
    make_test_menu(menu_b, "Restaurant B")
    p = tmp_path / "restaurants.json"
    p.write_text(json.dumps({"restaurants": {
        "rest_a": {
            "name": "Restaurant A", "menu_path": str(menu_a),
            "twilio_phone": "+1111111111", "owner_phone": "+15551234567",
        },
        "rest_b": {
            "name": "Restaurant B", "menu_path": str(menu_b),
            "twilio_phone": "+2222222222", "owner_phone": "+15551234567",
        },
    }}))
    return str(p)


@pytest.fixture
def registry(registry_path):
    return RestaurantRegistry(registry_path)

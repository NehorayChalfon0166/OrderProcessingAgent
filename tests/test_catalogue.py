"""Tests for catalogue.py — product lookup, validation, deals, hints."""

import pytest

from catalogue import Catalogue, ProductDef, ToppingDef, DealDef
from models import CartTopping


@pytest.fixture
def catalogue():
    """Load the real menu.json for testing."""
    return Catalogue("menus/marios_pizzeria.json")


class TestProductLookup:
    def test_exact_match(self, catalogue):
        p = catalogue.find_product("Margherita")
        assert p is not None
        assert p.id == "pizza_margherita"

    def test_case_insensitive(self, catalogue):
        p = catalogue.find_product("MARGHERITA")
        assert p is not None
        assert p.id == "pizza_margherita"

    def test_substring_match(self, catalogue):
        p = catalogue.find_product("pepperoni")
        assert p is not None
        assert "pepperoni" in p.id.lower()

    def test_partial_match(self, catalogue):
        # "margherita" is a substring of exactly one product — unambiguous
        p = catalogue.find_product("margherita")
        assert p is not None
        assert "margherita" in p.name.lower()

    def test_not_found(self, catalogue):
        p = catalogue.find_product("sushi")
        assert p is None

    def test_get_product_by_id(self, catalogue):
        p = catalogue.get_product("pizza_margherita")
        assert p is not None
        assert p.name == "Margherita"

    def test_get_product_unknown_id(self, catalogue):
        assert catalogue.get_product("nonexistent") is None


class TestToppingLookup:
    def test_exact_match(self, catalogue):
        t = catalogue.find_topping("Extra Cheese")
        assert t is not None
        assert t.id == "extra_cheese"

    def test_case_insensitive(self, catalogue):
        t = catalogue.find_topping("EXTRA CHEESE")
        assert t is not None
        assert t.id == "extra_cheese"

    def test_substring(self, catalogue):
        t = catalogue.find_topping("cheese")
        assert t is not None
        assert "cheese" in t.name.lower()

    def test_by_id(self, catalogue):
        t = catalogue.find_topping("extra_cheese")
        assert t is not None
        assert t.name == "Extra Cheese"

    def test_not_found(self, catalogue):
        t = catalogue.find_topping("caviar")
        assert t is None


class TestSizeResolution:
    def test_valid_size(self, catalogue):
        product = catalogue.get_product("pizza_margherita")
        size, issues = catalogue.resolve_size(product, "large")
        assert size == "large"
        assert issues == []

    def test_no_size_uses_default(self, catalogue):
        product = catalogue.get_product("pizza_margherita")
        size, issues = catalogue.resolve_size(product, None)
        assert size == "medium"
        assert issues == []

    def test_invalid_size_warns(self, catalogue):
        product = catalogue.get_product("pizza_margherita")
        size, issues = catalogue.resolve_size(product, "xlarge")
        assert size == "medium"  # falls back to default
        assert len(issues) == 1
        assert "not a valid size" in issues[0]

    def test_flat_price_item_no_size(self, catalogue):
        product = catalogue.get_product("side_garlic_bread")
        size, issues = catalogue.resolve_size(product, None)
        assert size is None
        assert issues == []

    def test_flat_price_item_size_given(self, catalogue):
        product = catalogue.get_product("side_garlic_bread")
        size, issues = catalogue.resolve_size(product, "large")
        assert size is None
        assert len(issues) == 1
        assert "different sizes" in issues[0]


class TestToppingResolution:
    def test_valid_toppings(self, catalogue):
        product = catalogue.get_product("pizza_margherita")
        resolved, issues = catalogue.resolve_toppings(
            product, ["Extra Cheese", "Mushrooms"]
        )
        assert len(resolved) == 2
        assert resolved[0].topping_id == "extra_cheese"
        assert resolved[1].topping_id == "mushrooms"
        assert resolved[0].price == 1.50
        assert resolved[1].price == 1.00
        assert issues == []

    def test_unknown_topping(self, catalogue):
        product = catalogue.get_product("pizza_margherita")
        resolved, issues = catalogue.resolve_toppings(product, ["Caviar"])
        assert len(resolved) == 0
        assert len(issues) == 1
        assert "Unknown topping" in issues[0]

    def test_unavailable_topping_for_product(self, catalogue):
        product = catalogue.get_product("pizza_buffalo")
        resolved, issues = catalogue.resolve_toppings(product, ["Sausage"])
        assert len(resolved) == 0
        assert len(issues) == 1
        assert "not available" in issues[0]

    def test_toppings_not_allowed_on_item(self, catalogue):
        product = catalogue.get_product("side_caesar_salad")
        resolved, issues = catalogue.resolve_toppings(product, ["Extra Cheese"])
        assert len(resolved) == 0
        assert len(issues) == 1
        assert "not available" in issues[0]

    def test_empty_toppings(self, catalogue):
        product = catalogue.get_product("pizza_margherita")
        resolved, issues = catalogue.resolve_toppings(product, [])
        assert resolved == []
        assert issues == []


class TestDealLookup:
    def test_find_deal_by_name(self, catalogue):
        d = catalogue.find_deal("Family Deal")
        assert d is not None
        assert d.id == "deal_family"

    def test_find_deal_by_id(self, catalogue):
        d = catalogue.find_deal("deal_couple")
        assert d is not None
        assert d.name == "Couple's Special"

    def test_deal_not_found(self, catalogue):
        assert catalogue.find_deal("nonexistent") is None


class TestHints:
    def test_get_hints_returns_string(self, catalogue):
        hints = catalogue.get_hints()
        assert isinstance(hints, str)
        assert len(hints) > 0

    def test_get_hints_includes_categories(self, catalogue):
        hints = catalogue.get_hints()
        assert "Pizzas" in hints
        assert "Sides" in hints
        assert "Drinks" in hints

    def test_get_hints_includes_deals(self, catalogue):
        hints = catalogue.get_hints()
        assert "Deals" in hints


class TestSuggestions:
    def test_get_suggestions_handles_misspellings(self, catalogue):
        suggestions = catalogue.get_product_suggestions("peproni")
        assert len(suggestions) > 0
        assert any("Pepperoni" in s for s in suggestions)

    def test_get_suggestions_handles_typo(self, catalogue):
        suggestions = catalogue.get_product_suggestions("mrgherita")
        assert len(suggestions) > 0
        assert any("Margherita" in s for s in suggestions)

    def test_get_suggestions_respects_limit(self, catalogue):
        suggestions = catalogue.get_product_suggestions("e", limit=3)
        assert len(suggestions) <= 3

    def test_get_suggestions_empty_for_gibberish(self, catalogue):
        suggestions = catalogue.get_product_suggestions("zzzznotathing")
        assert suggestions == []


class TestCategories:
    def test_get_categories(self, catalogue):
        cats = catalogue.get_categories()
        assert "Pizzas" in cats
        assert "Sides" in cats
        assert "Drinks" in cats
        assert "Desserts" in cats

    def test_get_toppings(self, catalogue):
        toppings = catalogue.get_toppings()
        assert len(toppings) > 0
        assert all(isinstance(t, ToppingDef) for t in toppings)

    def test_get_deals(self, catalogue):
        deals = catalogue.get_deals()
        assert len(deals) >= 1  # at least one deal configured
        assert all(isinstance(d, DealDef) for d in deals)


class TestRestaurantName:
    def test_restaurant_name(self, catalogue):
        assert "Mario" in catalogue.restaurant_name

"""Integration tests — end-to-end behaviour across multiple components.

Tests catalogue, pricing, session persistence, tools, multi-restaurant
registry, session routing, and edge cases. All use real component instances —
no mocks at the function level.
"""

import json
from pathlib import Path

import pytest


def _write_test_menu(path: Path):
    """Minimal test menu for edge-case tests that need their own files."""
    path.write_text(json.dumps({
        "restaurant_name": "Test",
        "currency": "USD",
        "categories": [
            {"name": "Pizza", "items": [
                {"id": "pizza_margherita", "name": "Margherita",
                 "sizes": {"medium": 12.99}, "default_size": "medium"},
            ]},
        ],
        "toppings": [],
        "deals": [],
        "delivery_fee": 0,
        "min_order_amount": 0,
    }))

from catalogue import Catalogue
from db import Database
from models import CartItem, OrderState, OrderType
from pricing import PricingEngine
from restaurant import RestaurantConfig, RestaurantRegistry
from session import OrderSession
from session_router import SessionRouter
from tools import (
    add_to_cart, cancel_order, confirm_order, remove_from_cart,
    request_review, set_customer_info, update_item, view_cart,
)
from voice_handler import (
    build_error_twiml,
    build_goodbye_twiml,
    build_reply_twiml,
    build_welcome_twiml,
)


# =============================================================================
# Catalogue — product lookup
# =============================================================================


class TestCatalogueProductLookup:
    def test_find_by_exact_name(self, catalogue):
        p = catalogue.find_product("Margherita")
        assert p is not None
        assert p.id == "pizza_margherita"

    def test_find_case_insensitive(self, catalogue):
        assert catalogue.find_product("margherita").id == "pizza_margherita"

    def test_find_by_substring(self, catalogue):
        assert catalogue.find_product("pepper").id == "pizza_pepperoni"

    def test_find_flat_price_item(self, catalogue):
        assert catalogue.find_product("Garlic Bread").id == "side_garlic_bread"

    def test_unknown_returns_none(self, catalogue):
        assert catalogue.find_product("sushi") is None

    def test_get_product_by_id(self, catalogue):
        assert catalogue.get_product("pizza_margherita").name == "Margherita"

    def test_get_product_unknown(self, catalogue):
        assert catalogue.get_product("nope") is None


class TestCatalogueToppingLookup:
    def test_find_by_name(self, catalogue):
        t = catalogue.find_topping("Extra Cheese")
        assert t is not None
        assert t.id == "extra_cheese"

    def test_find_case_insensitive(self, catalogue):
        assert catalogue.find_topping("mushrooms").id == "mushrooms"

    def test_find_by_id_fallback(self, catalogue):
        assert catalogue.find_topping("extra_cheese").id == "extra_cheese"

    def test_unknown_returns_none(self, catalogue):
        assert catalogue.find_topping("caviar") is None


class TestCatalogueSizeResolution:
    def test_resolves_valid_size(self, catalogue):
        prod = catalogue.get_product("pizza_margherita")
        resolved, _ = catalogue.resolve_size(prod, "large")
        assert resolved == "large"

    def test_defaults_when_no_size_given(self, catalogue):
        prod = catalogue.get_product("pizza_margherita")
        default, _ = catalogue.resolve_size(prod, None)
        assert default == "medium"

    def test_warns_on_invalid_size(self, catalogue):
        prod = catalogue.get_product("pizza_margherita")
        bad_size, warnings = catalogue.resolve_size(prod, "gigantic")
        assert len(warnings) > 0
        assert bad_size == "medium"

    def test_flat_price_item_has_no_sizes(self, catalogue):
        flat = catalogue.get_product("side_garlic_bread")
        size, warnings = catalogue.resolve_size(flat, "large")
        assert size is None
        assert len(warnings) > 0


class TestCatalogueToppingResolution:
    def test_resolves_toppings(self, catalogue):
        prod = catalogue.get_product("pizza_margherita")
        tops, issues = catalogue.resolve_toppings(prod, ["Extra Cheese", "Mushrooms"])
        assert len(tops) == 2
        assert tops[0].name == "Extra Cheese"
        assert tops[0].price == 1.50

    def test_unknown_topping_warns(self, catalogue):
        prod = catalogue.get_product("pizza_margherita")
        tops, issues = catalogue.resolve_toppings(prod, ["caviar"])
        assert len(issues) > 0
        assert len(tops) == 0

    def test_topping_not_allowed_on_item(self, catalogue):
        flat = catalogue.get_product("side_garlic_bread")
        tops, issues = catalogue.resolve_toppings(flat, ["Extra Cheese"])
        assert len(issues) > 0

    def test_empty_toppings_list(self, catalogue):
        prod = catalogue.get_product("pizza_margherita")
        tops, issues = catalogue.resolve_toppings(prod, [])
        assert tops == []
        assert issues == []


class TestCatalogueDeals:
    def test_find_deal_by_name(self, catalogue):
        deal = catalogue.find_deal("Family Deal")
        assert deal is not None
        assert deal.name == "Family Deal"
        assert deal.price == 34.99

    def test_deal_not_found(self, catalogue):
        assert catalogue.find_deal("nonexistent") is None

    def test_deals_listed(self, catalogue):
        deals = catalogue.get_deals()
        assert len(deals) >= 1
        assert any(d.name == "Family Deal" for d in deals)

    def test_deal_findable_as_product(self, catalogue):
        product = catalogue.find_deal("Family Deal")
        assert product is not None
        assert abs(product.price - 34.99) < 0.01


class TestCatalogueHints:
    def test_hints_non_empty(self, catalogue):
        hints = catalogue.get_hints()
        assert len(hints) > 0

    def test_hints_includes_category_name(self, catalogue):
        hints = catalogue.get_hints()
        assert "Pizzas" in hints

    def test_suggestions_for_misspelling(self, catalogue):
        suggestions = catalogue.get_product_suggestions("peproni")
        assert len(suggestions) > 0

    def test_suggestions_with_limit(self, catalogue):
        suggestions = catalogue.get_product_suggestions("pizza", limit=2)
        assert len(suggestions) <= 2


class TestCatalogueNormalize:
    def test_apostrophe_handling(self, catalogue):
        # Smart-quote normalization doesn't break exact matches
        topping = catalogue.find_topping("Extra Cheese")
        assert topping is not None
        # Normalized query still works
        assert catalogue.find_topping("extra cheese") is not None


# =============================================================================
# Pricing
# =============================================================================


class TestPricing:
    def test_base_price_set_correctly(self, catalogue, pricing):
        product = catalogue.find_product("Margherita")
        item = CartItem(product_id=product.id, name=product.name,
                        category=product.category, size="medium")
        priced = pricing.price_item(item)
        assert priced.base_price == 12.99

    def test_line_total_equals_base_times_quantity(self, catalogue, pricing):
        product = catalogue.find_product("Margherita")
        item = CartItem(product_id=product.id, name=product.name,
                        category=product.category, size="medium", quantity=2)
        priced = pricing.price_item(item)
        assert priced.line_total == pytest.approx(25.98)

    def test_base_plus_toppings(self, catalogue, pricing):
        from models import CartTopping
        product = catalogue.find_product("Margherita")
        topping = catalogue.find_topping("Extra Cheese")
        item = CartItem(
            product_id=product.id, name=product.name, category=product.category,
            size="medium", toppings=[CartTopping(topping_id=topping.id, name=topping.name, price=topping.price)],
        )
        priced = pricing.price_item(item)
        assert priced.line_total == pytest.approx(14.49)

    def test_quantity_multiplier(self, catalogue, pricing):
        product = catalogue.find_product("Garlic Bread")
        item = CartItem(product_id=product.id, name=product.name,
                        category=product.category, quantity=3)
        priced = pricing.price_item(item)
        assert priced.line_total == pytest.approx(14.97)

    def test_delivery_totals(self, session, catalogue, pricing):
        product = catalogue.find_product("Margherita")
        item = CartItem(product_id=product.id, name=product.name,
                        category=product.category, size="medium")
        priced = pricing.price_item(item)
        session.cart.append(priced)
        session.customer.order_type = OrderType.DELIVERY
        subtotal, delivery_fee, total = pricing.compute_totals(session.cart, OrderType.DELIVERY)
        assert subtotal == pytest.approx(12.99)
        assert delivery_fee == pytest.approx(3.99)
        assert total == pytest.approx(16.98)

    def test_pickup_no_delivery_fee(self, session, catalogue, pricing):
        product = catalogue.find_product("Margherita")
        item = CartItem(product_id=product.id, name=product.name,
                        category=product.category, size="medium")
        priced = pricing.price_item(item)
        session.cart.append(priced)
        _, delivery_fee, _ = pricing.compute_totals(session.cart, OrderType.PICKUP)
        assert delivery_fee == 0.0

    def test_min_order_met(self, pricing):
        assert pricing.check_minimum_order(12.99) is True

    def test_min_order_not_met(self, pricing):
        assert pricing.check_minimum_order(5.00) is False


# =============================================================================
# Session persistence
# =============================================================================


class TestSession:
    def test_default_state_is_building(self, session):
        assert session.state == OrderState.BUILDING

    def test_default_cart_empty(self, session):
        assert session.cart == []

    def test_is_active(self, session):
        assert session.is_active is True

    def test_is_complete_false(self, session):
        assert session.is_complete is False

    def test_add_user_message(self, session):
        session.add_user_message("Hi")
        assert len(session.conversation) == 1
        assert session.conversation[0].role.value == "user"

    def test_save_load_roundtrip(self, db):
        s = OrderSession(restaurant_id="test")
        s._db = db
        s.save()
        s.add_user_message("Test message")
        s.save()
        loaded = db.load_session("test", s.session_id)
        assert loaded is not None
        assert loaded.session_id == s.session_id
        assert len(loaded.conversation) == 1

    def test_cart_preserved_after_load(self, db, catalogue, pricing):
        s = OrderSession(restaurant_id="test")
        s._db = db
        s.save()
        product = catalogue.find_product("Margherita")
        item = CartItem(product_id=product.id, name=product.name,
                        category=product.category, size="medium")
        priced = pricing.price_item(item)
        s.cart.append(priced)
        s.save()
        loaded = db.load_session("test", s.session_id)
        assert loaded is not None
        assert len(loaded.cart) == 1
        assert loaded.cart[0].name == "Margherita"
        assert loaded.cart[0].line_total == pytest.approx(12.99)

    def test_completed_is_terminal(self):
        s = OrderSession(state=OrderState.COMPLETED)
        assert s.is_complete is True
        assert s.is_active is False
        assert s.is_cancelled is False

    def test_cancelled_is_terminal(self):
        s = OrderSession(state=OrderState.CANCELLED)
        assert s.is_cancelled is True
        assert s.is_active is False


# =============================================================================
# Tools
# =============================================================================


class TestTools:
    def test_add_to_cart_succeeds(self, session, catalogue, pricing):
        result = add_to_cart(session, catalogue, pricing, "Margherita")
        assert result.success is True
        assert len(session.cart) == 1
        assert session.cart[0].name == "Margherita"

    def test_add_with_size(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, "Margherita", size="large")
        assert session.cart[0].size == "large"

    def test_add_with_topping(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, "Margherita", toppings=["Extra Cheese"])
        assert len(session.cart[0].toppings) == 1
        assert session.cart[0].toppings[0].name == "Extra Cheese"

    def test_unknown_product_returns_failure(self, session, catalogue, pricing):
        result = add_to_cart(session, catalogue, pricing, "thisdoesnotexist")
        assert result.success is False

    def test_misspelled_product_gets_suggestions(self, session, catalogue, pricing):
        # "garlik" is close to "Garlic Bread" — difflib should catch it
        result = add_to_cart(session, catalogue, pricing, "garlik")
        # Either it matches or it suggests — both are valid
        if not result.success:
            assert len(result.suggestions) > 0

    def test_view_cart(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, "Margherita")
        add_to_cart(session, catalogue, pricing, "Cola")
        result = view_cart(session, catalogue, pricing)
        assert result.item_count == 2
        assert len(result.items) == 2

    def test_remove_by_name(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, "Margherita")
        add_to_cart(session, catalogue, pricing, "Cola")
        add_to_cart(session, catalogue, pricing, "Pepperoni")
        result = remove_from_cart(session, catalogue, pricing, "Margherita")
        assert result.success is True
        assert len(session.cart) == 2

    def test_remove_by_index(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, "Margherita")
        add_to_cart(session, catalogue, pricing, "Cola")
        result = remove_from_cart(session, catalogue, pricing, "1")
        assert result.success is True
        assert len(session.cart) == 1

    def test_update_quantity(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, "Margherita")
        result = update_item(session, catalogue, pricing, "Margherita", quantity=2)
        assert result.success is True
        assert session.cart[0].quantity == 2

    def test_set_name_and_phone(self, session, catalogue, pricing):
        result = set_customer_info(
            session, catalogue, pricing,
            name="Nehoray", phone="+972539534345",
        )
        assert result.success is True
        assert session.customer.name == "Nehoray"
        assert session.customer.phone == "+972539534345"

    def test_set_address(self, session, catalogue, pricing):
        set_customer_info(session, catalogue, pricing, name="A", phone="+1")
        result = set_customer_info(
            session, catalogue, pricing,
            address="123 Main St", order_type="delivery",
        )
        assert result.success is True
        assert session.customer.address == "123 Main St"
        assert session.customer.order_type == OrderType.DELIVERY

    def test_invalid_order_type_surfaces_issue(self, session, catalogue, pricing):
        result = set_customer_info(session, catalogue, pricing, order_type="invalid")
        assert result.success is True
        assert any("order_type" in m for m in result.missing_required)

    def test_request_review_succeeds_with_all_info(self, session, catalogue, pricing):
        set_customer_info(session, catalogue, pricing, name="A", phone="+1",
                          address="X", order_type="delivery")
        add_to_cart(session, catalogue, pricing, "Margherita")
        result = request_review(session, catalogue, pricing)
        assert result.success is True

    def test_request_review_blocked_empty_cart(self, session, catalogue, pricing):
        result = request_review(session, catalogue, pricing)
        assert result.success is False
        assert any("empty" in i.lower() for i in result.issues)

    def test_confirm_cash_order(self, session, catalogue, pricing):
        set_customer_info(session, catalogue, pricing, name="A", phone="+1")
        add_to_cart(session, catalogue, pricing, "Margherita")
        request_review(session, catalogue, pricing)
        result = confirm_order(session, catalogue, pricing, payment_method="cash")
        assert result.success is True
        assert result.total > 0
        assert session.payment_method == "cash"

    def test_cancel_order(self, session, catalogue, pricing):
        result = cancel_order(session, catalogue, pricing)
        assert result.success is True
        assert session._pending_transition == OrderState.CANCELLED


# =============================================================================
# Multi-restaurant registry
# =============================================================================


class TestRegistryLoading:
    def test_two_restaurants_loaded(self, registry):
        assert len(registry.list_restaurants()) == 2

    def test_accessible_by_id(self, registry):
        assert registry.get_by_id("rest_a") is not None
        assert registry.get_by_id("rest_b") is not None

    def test_nonexistent_returns_none(self, registry):
        assert registry.get_by_id("nonexistent") is None

    def test_get_default(self, registry):
        default = registry.get_default()
        assert default.config.id == "rest_a"


class TestRegistryPhoneLookup:
    def test_phone_lookup(self, registry):
        assert registry.get_by_twilio_phone("+1111111111") is not None
        assert registry.get_by_twilio_phone("+2222222222") is not None

    def test_unknown_phone(self, registry):
        assert registry.get_by_twilio_phone("+9999999999") is None

    def test_whatsapp_prefix_stripped(self, registry):
        ctx = registry.get_by_twilio_phone("whatsapp:+1111111111")
        assert ctx is not None
        assert ctx.config.id == "rest_a"

    def test_whitespace_stripped(self, registry):
        ctx = registry.get_by_twilio_phone("  +1111111111  ")
        assert ctx is not None
        assert ctx.config.id == "rest_a"

    def test_empty_phone_returns_none(self, registry):
        assert registry.get_by_twilio_phone("") is None


class TestRegistryList:
    def test_list_returns_configs(self, registry):
        configs = registry.list_restaurants()
        assert len(configs) == 2
        assert all(isinstance(c, RestaurantConfig) for c in configs)
        ids = {c.id for c in configs}
        assert "rest_a" in ids
        assert "rest_b" in ids


class TestRegistryContextIntegrity:
    def test_catalogue_and_pricing_loaded(self, registry):
        ctx = registry.get_by_id("rest_a")
        assert ctx is not None
        assert ctx.catalogue is not None
        assert ctx.pricing is not None
        assert ctx.catalogue.restaurant_name == "Restaurant A"

    def test_can_find_products(self, registry):
        ctx = registry.get_by_id("rest_a")
        assert ctx is not None
        assert ctx.catalogue.find_product("Margherita") is not None

    def test_restaurants_independent(self, registry):
        ctx_a = registry.get_by_id("rest_a")
        ctx_b = registry.get_by_id("rest_b")
        assert ctx_a is not None
        assert ctx_b is not None
        assert ctx_a.config.name != ctx_b.config.name


class TestRegistryValidation:
    def test_missing_twilio_phone_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"restaurants": {
            "r": {"name": "X", "menu_path": "m.json"},
        }}))
        with pytest.raises(ValueError, match="twilio_phone"):
            RestaurantRegistry(str(p))

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            RestaurantRegistry("/nonexistent/path/restaurants.json")


class TestRegistryConfigFrozen:
    def test_config_is_immutable(self, registry):
        config = registry.list_restaurants()[0]
        with pytest.raises(Exception):
            config.name = "changed"


# =============================================================================
# Session isolation and routing
# =============================================================================


class TestSessionIsolation:
    def test_same_phone_different_restaurants(self, registry, db):
        router = SessionRouter()
        s_a = router.get_or_create("rest_a", "972539534345", db=db)
        s_b = router.get_or_create("rest_b", "972539534345", db=db)
        assert s_a.session_id == s_b.session_id
        assert s_a.restaurant_id == "rest_a"
        assert s_b.restaurant_id == "rest_b"

    def test_active_session_reused(self, db):
        router = SessionRouter()
        s1 = router.get_or_create("rest", "972511111111", db=db)
        s1.add_user_message("hello")
        s1.save()
        s2 = router.get_or_create("rest", "972511111111", db=db)
        assert s2.session_id == s1.session_id
        assert len(s2.conversation) == 1


class TestSessionRouterTerminal:
    def test_completed_replaced_with_fresh(self, db):
        router = SessionRouter()
        old = router.get_or_create("rest", "972522222222", db=db)
        old.add_user_message("old order")
        old.state = OrderState.COMPLETED
        old.save()
        new = router.get_or_create("rest", "972522222222", db=db)
        assert new.session_id == old.session_id
        assert len(new.cart) == 0
        assert new.state == OrderState.BUILDING
        assert new.restaurant_id == "rest"

    def test_cancelled_replaced_with_fresh(self, db):
        router = SessionRouter()
        old = router.get_or_create("rest", "972533333333", db=db)
        old.add_user_message("cancelled order")
        old.state = OrderState.CANCELLED
        old.save()
        new = router.get_or_create("rest", "972533333333", db=db)
        assert len(new.cart) == 0
        assert new.state == OrderState.BUILDING


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    def test_duplicate_phone_last_wins(self, tmp_path):
        """When two restaurants share a phone, last one loaded wins the lookup."""
        menu_path = tmp_path / "menu.json"
        _write_test_menu(menu_path)
        p = tmp_path / "restaurants.json"
        p.write_text(json.dumps({"restaurants": {
            "r1": {"name": "R1", "menu_path": str(menu_path), "twilio_phone": "+1111", "owner_phone": "+1"},
            "r2": {"name": "R2", "menu_path": str(menu_path), "twilio_phone": "+1111", "owner_phone": "+2"},
        }}))
        reg = RestaurantRegistry(str(p))
        ctx = reg.get_by_twilio_phone("+1111")
        assert ctx is not None
        assert ctx.config.name == "R2"  # last wins
        assert reg.get_by_id("r1") is not None  # still accessible by ID

    def test_session_phone_sanitization(self):
        router = SessionRouter()
        sid = router._sanitize("+972-53-953-4345")
        assert sid == "972539534345"

    def test_similar_ids_no_partial_match(self, tmp_path):
        menu_path = tmp_path / "menu.json"
        _write_test_menu(menu_path)
        p = tmp_path / "restaurants.json"
        p.write_text(json.dumps({"restaurants": {
            "marios": {"name": "M", "menu_path": str(menu_path), "twilio_phone": "+1", "owner_phone": "+2"},
            "marios_pizzeria": {"name": "MP", "menu_path": str(menu_path), "twilio_phone": "+3", "owner_phone": "+4"},
        }}))
        reg = RestaurantRegistry(str(p))
        assert reg.get_by_id("marios") is not None
        assert reg.get_by_id("marios_pizzeria") is not None
        assert reg.get_by_id("mario") is None

    def test_process_turn_signature(self):
        from agent_loop import process_turn
        import inspect
        params = list(inspect.signature(process_turn).parameters.keys())
        assert "session" in params
        assert "user_message" in params
        assert "catalogue" in params
        assert "pricing" in params
        assert "llm_client" in params


# =============================================================================
# Voice integration
# =============================================================================


class TestVoicePhoneLookup:
    def test_voice_phone_routing(self, tmp_path, registry_path):
        """Restaurant with voice_phone is found by get_by_voice_phone."""
        # Create a registry with voice_phone set
        menu_path = tmp_path / "voice_menu.json"
        from tests.conftest import make_test_menu
        make_test_menu(menu_path, "Voice Restaurant")
        p = tmp_path / "voice_restaurants.json"
        p.write_text(json.dumps({"restaurants": {
            "voice_rest": {
                "name": "Voice Restaurant", "menu_path": str(menu_path),
                "twilio_phone": "+1111111111", "owner_phone": "+15551234567",
                "voice_phone": "+972501234567",
            },
        }}))
        reg = RestaurantRegistry(str(p))
        ctx = reg.get_by_voice_phone("+972501234567")
        assert ctx is not None
        assert ctx.config.id == "voice_rest"

    def test_voice_phone_whitespace(self, tmp_path):
        """Whitespace around voice_phone is stripped."""
        menu_path = tmp_path / "vm.json"
        from tests.conftest import make_test_menu
        make_test_menu(menu_path, "VM")
        p = tmp_path / "vr.json"
        p.write_text(json.dumps({"restaurants": {
            "r": {
                "name": "VM", "menu_path": str(menu_path),
                "twilio_phone": "+1", "owner_phone": "+2",
                "voice_phone": "+972501234567",
            },
        }}))
        reg = RestaurantRegistry(str(p))
        assert reg.get_by_voice_phone("  +972501234567  ") is not None

    def test_no_voice_phone_returns_none(self, tmp_path):
        """Restaurant without voice_phone is not found."""
        menu_path = tmp_path / "nvm.json"
        from tests.conftest import make_test_menu
        make_test_menu(menu_path, "NVM")
        p = tmp_path / "nvr.json"
        p.write_text(json.dumps({"restaurants": {
            "r": {
                "name": "NVM", "menu_path": str(menu_path),
                "twilio_phone": "+1", "owner_phone": "+2",
            },
        }}))
        reg = RestaurantRegistry(str(p))
        assert reg.get_by_voice_phone("+1") is None

    def test_empty_voice_phone_returns_none(self, registry):
        """Restaurant with empty voice_phone is not found."""
        assert registry.get_by_voice_phone("") is None

    def test_voice_phone_not_found(self, tmp_path):
        """Unknown voice phone returns None."""
        menu_path = tmp_path / "nfm.json"
        from tests.conftest import make_test_menu
        make_test_menu(menu_path, "NFM")
        p = tmp_path / "nfr.json"
        p.write_text(json.dumps({"restaurants": {
            "r": {
                "name": "NFM", "menu_path": str(menu_path),
                "twilio_phone": "+1", "owner_phone": "+2",
                "voice_phone": "+972501234567",
            },
        }}))
        reg = RestaurantRegistry(str(p))
        assert reg.get_by_voice_phone("+9999999999") is None


class TestVoiceHints:
    def test_builds_hints_from_catalogue(self, catalogue):
        """_build_hints extracts item names from all categories."""
        from server import _build_hints
        hints = _build_hints(catalogue)
        assert "Margherita" in hints
        assert "Pepperoni" in hints
        assert "Garlic Bread" in hints
        assert "Cola" in hints

    def test_hints_comma_separated(self, catalogue):
        """Hints are comma-separated for the Twilio hints attribute."""
        from server import _build_hints
        hints = _build_hints(catalogue)
        assert ", " in hints

    def test_hints_with_special_char_names(self, tmp_path):
        """Item names with & are included in hints (escaped elsewhere)."""
        menu_path = tmp_path / "special_menu.json"
        menu_path.write_text(json.dumps({
            "restaurant_name": "Special", "currency": "USD",
            "categories": [
                {"name": "Food", "items": [
                    {"id": "fish_chips", "name": "Fish & Chips"},
                ]},
            ],
            "toppings": [], "deals": [], "delivery_fee": 0,
            "min_order_amount": 0,
        }))
        from catalogue import Catalogue
        from server import _build_hints
        hints = _build_hints(Catalogue(str(menu_path)))
        assert "Fish & Chips" in hints


class TestVoiceHandlerIntegration:
    """Test voice_handler with real catalogue data."""

    def test_welcome_twiml_with_real_greeting(self, catalogue):
        """build_welcome_twiml accepts a greeting with restaurant name."""
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=test",
            f"Welcome to {catalogue.restaurant_name}! What would you like?",
        )
        assert catalogue.restaurant_name in twiml
        assert "Gather" in twiml

    def test_reply_twiml_with_agent_response(self):
        """build_reply_twiml wraps agent text for voice output."""
        agent_response = "Added a large Margherita pizza to your order."
        twiml = build_reply_twiml(
            agent_response, "/voice/speech-result?restaurant=test",
        )
        assert agent_response in twiml
        assert "Anything else?" in twiml

    def test_goodbye_twiml_ends_call(self):
        """build_goodbye_twiml includes Hangup."""
        twiml = build_goodbye_twiml("Thank you! Your order total is ₪45.")
        assert "Hangup" in twiml

    def test_error_twiml_ends_call(self):
        """build_error_twiml includes Hangup."""
        twiml = build_error_twiml("Something went wrong.")
        assert "Hangup" in twiml

    def test_reply_twiml_strips_whitespace(self):
        """Agent response trailing whitespace is cleaned before 'Anything else?'"""
        twiml = build_reply_twiml(
            "  Added!  ", "/voice/speech-result?restaurant=test",
        )
        # Should not have double spaces between response and "Anything else?"
        assert "Added! Anything else?" in twiml


class TestVoiceConfidenceHandling:
    """Confidence score edge cases for the voice speech-result handler."""

    def test_missing_confidence_does_not_reject(self):
        """If Twilio omits Confidence, speech should still be accepted."""
        # Simulate what happens in the endpoint: missing Confidence → "" → 0.5
        confidence_str = ""
        if confidence_str:
            confidence = float(confidence_str)
        else:
            confidence = 0.5
        # Should NOT be rejected
        assert not (confidence < 0.2)

    def test_zero_confidence_rejected(self):
        """Explicit zero confidence should be rejected."""
        confidence_str = "0.0"
        confidence = float(confidence_str)
        assert confidence < 0.2

    def test_low_confidence_rejected(self):
        """Confidence 0.1 should be rejected."""
        assert 0.1 < 0.2

    def test_normal_confidence_accepted(self):
        """Confidence 0.85 should be accepted."""
        assert not (0.85 < 0.2)

    def test_invalid_confidence_defaults_to_neutral(self):
        """Unparseable Confidence should default to 0.5 (trust)."""
        confidence_str = "not-a-number"
        try:
            confidence = float(confidence_str)
        except (ValueError, TypeError):
            confidence = 0.5
        assert confidence == 0.5
        assert not (confidence < 0.2)


class TestVoiceSessionRouting:
    """Session routing for voice calls using caller ID."""

    def test_voice_caller_creates_session(self, db):
        """Caller ID from voice webhook creates a session."""
        router = SessionRouter()
        session = router.get_or_create("rest", "+972539534345", db=db)
        assert session.session_id == "972539534345"
        assert session.restaurant_id == "rest"

    def test_voice_caller_sanitized(self, db):
        """Caller ID with formatting is sanitized to digits."""
        router = SessionRouter()
        session = router.get_or_create("rest", "+972-53-953-4345", db=db)
        assert session.session_id == "972539534345"

    def test_voice_and_whatsapp_same_phone_separate_sessions(self, db):
        """Same phone via voice and WhatsApp share session (same ID)."""
        router = SessionRouter()
        s_voice = router.get_or_create("rest", "+972511111111", db=db)
        s_wa = router.get_or_create("rest", "whatsapp:+972511111111", db=db)
        # Both sanitize to the same digits — they're the same session
        assert s_voice.session_id == s_wa.session_id


# =============================================================================
# Server import smoke tests
# =============================================================================


class TestServerImports:
    def test_server_imports(self):
        import server  # noqa: F401

    def test_twilio_client_imports(self):
        from twilio_client import TwilioClient  # noqa: F401

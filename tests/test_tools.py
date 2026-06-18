"""Tests for tools.py — tool implementations (no LLM required)."""

import pytest

from catalogue import Catalogue
from models import OrderState, OrderType
from pricing import PricingEngine
from session import OrderSession
from tools import (
    TOOLS_BY_STATE,
    add_to_cart,
    cancel_order,
    confirm_order,
    remove_from_cart,
    request_review,
    set_customer_info,
    tool,
    update_item,
    view_cart,
)


@pytest.fixture
def catalogue():
    return Catalogue("menus/marios_pizzeria.json")


@pytest.fixture
def pricing(catalogue):
    return PricingEngine(catalogue.menu_data)


@pytest.fixture
def session():
    return OrderSession()


# =============================================================================
# @tool Decorator
# =============================================================================


class TestToolDecorator:
    def test_stores_name(self):
        assert add_to_cart.__tool_name__ == "add_to_cart"

    def test_stores_description(self):
        assert "Add an item" in add_to_cart.__tool_description__

    def test_stores_schema(self):
        schema = add_to_cart.__tool_schema__
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "product_name" in schema["properties"]
        assert schema["properties"]["product_name"]["type"] == "string"

    def test_required_params_in_schema(self):
        schema = add_to_cart.__tool_schema__
        assert "product_name" in schema["required"]

    def test_optional_params_not_required(self):
        schema = add_to_cart.__tool_schema__
        assert "quantity" not in schema["required"]
        assert "size" not in schema["required"]

    def test_injected_params_excluded(self):
        schema = add_to_cart.__tool_schema__
        assert "session" not in schema["properties"]
        assert "catalogue" not in schema["properties"]
        assert "pricing" not in schema["properties"]


# =============================================================================
# add_to_cart
# =============================================================================


class TestAddToCart:
    def test_add_item_by_name(self, session, catalogue, pricing):
        result = add_to_cart(session, catalogue, pricing, product_name="Margherita")
        assert result.success is True
        assert result.item is not None
        assert result.item.product_id == "pizza_margherita"
        assert result.item.size == "medium"  # default
        assert len(session.cart) == 1

    def test_add_with_size(self, session, catalogue, pricing):
        result = add_to_cart(
            session, catalogue, pricing, product_name="Margherita", size="large"
        )
        assert result.success is True
        assert result.item.size == "large"

    def test_add_with_quantity(self, session, catalogue, pricing):
        result = add_to_cart(
            session, catalogue, pricing, product_name="Margherita", quantity=3
        )
        assert result.item.quantity == 3
        assert result.item.line_total == pytest.approx(38.97)  # 12.99 * 3

    def test_add_with_toppings(self, session, catalogue, pricing):
        result = add_to_cart(
            session,
            catalogue,
            pricing,
            product_name="Margherita",
            toppings=["Extra Cheese", "Bacon"],
        )
        assert result.success is True
        assert len(result.item.toppings) == 2
        assert any(t.topping_id == "extra_cheese" for t in result.item.toppings)
        assert any(t.topping_id == "bacon" for t in result.item.toppings)
        # price includes toppings
        assert result.item.line_total > result.item.base_price

    def test_not_found(self, session, catalogue, pricing):
        result = add_to_cart(session, catalogue, pricing, product_name="sushi")
        assert result.success is False
        assert result.item is None
        assert len(result.suggestions) == 0

    def test_fuzzy_catches_misspelling(self, session, catalogue, pricing):
        """difflib fuzzy matching catches common misspellings directly."""
        result = add_to_cart(session, catalogue, pricing, product_name="peproni")
        assert result.success is True
        assert result.item.product_id == "pizza_pepperoni"

    def test_invalid_size_warns(self, session, catalogue, pricing):
        result = add_to_cart(
            session, catalogue, pricing, product_name="Margherita", size="xlarge"
        )
        assert result.success is True  # still succeeds with default
        assert len(result.issues) > 0
        assert "not a valid size" in result.issues[0]
        assert result.item.size == "medium"

    def test_price_is_set(self, session, catalogue, pricing):
        result = add_to_cart(session, catalogue, pricing, product_name="Margherita")
        assert result.item.base_price > 0
        assert result.item.line_total > 0


# =============================================================================
# remove_from_cart
# =============================================================================


class TestRemoveFromCart:
    def test_remove_by_name(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        assert len(session.cart) == 1

        result = remove_from_cart(session, catalogue, pricing, item_reference="Margherita")
        assert result.success is True
        assert len(result.removed) == 1
        assert len(session.cart) == 0

    def test_remove_by_index(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        result = remove_from_cart(session, catalogue, pricing, item_reference="1")
        assert result.success is True

    def test_remove_by_uuid(self, session, catalogue, pricing):
        r = add_to_cart(session, catalogue, pricing, product_name="Margherita")
        item_id = r.item.id
        result = remove_from_cart(session, catalogue, pricing, item_reference=item_id)
        assert result.success is True

    def test_remove_not_found(self, session, catalogue, pricing):
        result = remove_from_cart(session, catalogue, pricing, item_reference="nonexistent")
        assert result.success is False
        assert len(result.removed) == 0


# =============================================================================
# update_item
# =============================================================================


class TestUpdateItem:
    def test_update_quantity(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        result = update_item(
            session, catalogue, pricing, item_reference="Margherita", quantity=5
        )
        assert result.success is True
        assert result.item.quantity == 5

    def test_update_size(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        result = update_item(
            session, catalogue, pricing, item_reference="Margherita", size="large"
        )
        assert result.success is True
        assert result.item.size == "large"
        assert result.item.base_price == 15.99

    def test_update_toppings(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        result = update_item(
            session,
            catalogue,
            pricing,
            item_reference="Margherita",
            toppings=["Bacon", "Mushrooms"],
        )
        assert result.success is True
        assert len(result.item.toppings) == 2

    def test_update_not_found(self, session, catalogue, pricing):
        result = update_item(
            session, catalogue, pricing, item_reference="nonexistent", quantity=2
        )
        assert result.success is False


# =============================================================================
# view_cart
# =============================================================================


class TestViewCart:
    def test_empty_cart(self, session, catalogue, pricing):
        result = view_cart(session, catalogue, pricing)
        assert result.items == []
        assert result.subtotal == 0.0
        assert result.item_count == 0

    def test_with_items(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        add_to_cart(session, catalogue, pricing, product_name="Caesar Salad")
        result = view_cart(session, catalogue, pricing)
        assert result.item_count == 2
        assert result.subtotal > 0


# =============================================================================
# set_customer_info
# =============================================================================


class TestSetCustomerInfo:
    def test_set_name(self, session, catalogue, pricing):
        result = set_customer_info(session, catalogue, pricing, name="John")
        assert result.success is True
        assert result.info.name == "John"

    def test_set_multiple_fields(self, session, catalogue, pricing):
        result = set_customer_info(
            session,
            catalogue,
            pricing,
            name="John",
            phone="555-0123",
            order_type="delivery",
        )
        assert result.info.name == "John"
        assert result.info.phone == "555-0123"
        assert result.info.order_type == OrderType.DELIVERY

    def test_merge_does_not_clear(self, session, catalogue, pricing):
        set_customer_info(session, catalogue, pricing, name="John", phone="555-0123")
        set_customer_info(session, catalogue, pricing, address="123 Main St")
        assert session.customer.name == "John"  # preserved
        assert session.customer.phone == "555-0123"  # preserved
        assert session.customer.address == "123 Main St"

    def test_missing_required_reported(self, session, catalogue, pricing):
        result = set_customer_info(session, catalogue, pricing, name="John")
        assert "phone" in result.missing_required

    def test_delivery_requires_address(self, session, catalogue, pricing):
        result = set_customer_info(
            session,
            catalogue,
            pricing,
            name="John",
            phone="555-0123",
            order_type="delivery",
        )
        assert "address" in result.missing_required

    def test_pickup_does_not_require_address(self, session, catalogue, pricing):
        result = set_customer_info(
            session,
            catalogue,
            pricing,
            name="John",
            phone="555-0123",
            order_type="pickup",
        )
        assert "address" not in result.missing_required


# =============================================================================
# request_review
# =============================================================================


class TestRequestReview:
    def test_blocked_empty_cart(self, session, catalogue, pricing):
        result = request_review(session, catalogue, pricing)
        assert result.success is False
        assert any("empty" in issue.lower() for issue in result.issues)

    def test_blocked_no_customer_info(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        result = request_review(session, catalogue, pricing)
        assert result.success is False
        assert any("name" in issue.lower() for issue in result.issues)

    def test_allows_with_items_and_name_phone(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        set_customer_info(session, catalogue, pricing, name="John", phone="555-0123")
        result = request_review(session, catalogue, pricing)
        assert result.success is True
        assert session._pending_transition == OrderState.REVIEW

    def test_allows_delivery_with_address(self, session, catalogue, pricing):
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        set_customer_info(
            session,
            catalogue,
            pricing,
            name="John",
            phone="555-0123",
            address="123 Main St",
            order_type="delivery",
        )
        result = request_review(session, catalogue, pricing)
        assert result.success is True


# =============================================================================
# confirm_order
# =============================================================================


class TestConfirmOrder:
    def test_confirm_cash_defaults_to_completed(self, session, catalogue, pricing):
        """Default payment_method='cash' goes straight to COMPLETED."""
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        set_customer_info(session, catalogue, pricing, name="John", phone="555-0123")
        result = confirm_order(session, catalogue, pricing)
        assert result.success is True
        assert result.order_id is not None
        assert result.total > 0
        assert session.payment_method == "cash"
        assert session._pending_transition == OrderState.COMPLETED

    def test_confirm_link_sets_payment_pending(self, session, catalogue, pricing):
        """payment_method='link' transitions to PAYMENT_PENDING."""
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        set_customer_info(session, catalogue, pricing, name="John", phone="555-0123")
        result = confirm_order(session, catalogue, pricing, payment_method="link")
        assert result.success is True
        assert session.payment_method == "link"
        assert session._pending_transition == OrderState.PAYMENT_PENDING

    def test_confirm_explicit_cash(self, session, catalogue, pricing):
        """Explicit payment_method='cash' same as default."""
        add_to_cart(session, catalogue, pricing, product_name="Margherita")
        set_customer_info(session, catalogue, pricing, name="John", phone="555-0123")
        result = confirm_order(session, catalogue, pricing, payment_method="cash")
        assert session.payment_method == "cash"
        assert session._pending_transition == OrderState.COMPLETED


# =============================================================================
# cancel_order
# =============================================================================


class TestCancelOrder:
    def test_cancel_sets_transition(self, session, catalogue, pricing):
        result = cancel_order(session, catalogue, pricing)
        assert result.success is True
        assert session._pending_transition == OrderState.CANCELLED


# =============================================================================
# TOOLS_BY_STATE
# =============================================================================


class TestToolsByState:
    def test_building_has_seven_tools(self):
        assert len(TOOLS_BY_STATE[OrderState.BUILDING]) == 7
        names = {f.__tool_name__ for f in TOOLS_BY_STATE[OrderState.BUILDING]}
        assert "add_to_cart" in names
        assert "request_review" in names
        assert "confirm_order" not in names  # only in REVIEW

    def test_review_has_seven_tools(self):
        assert len(TOOLS_BY_STATE[OrderState.REVIEW]) == 7
        names = {f.__tool_name__ for f in TOOLS_BY_STATE[OrderState.REVIEW]}
        assert "confirm_order" in names
        assert "request_review" not in names  # only in BUILDING

    def test_payment_pending_has_two_tools(self):
        assert len(TOOLS_BY_STATE[OrderState.PAYMENT_PENDING]) == 2
        names = {f.__tool_name__ for f in TOOLS_BY_STATE[OrderState.PAYMENT_PENDING]}
        assert "view_cart" in names
        assert "cancel_order" in names

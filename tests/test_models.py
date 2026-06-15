"""Tests for models.py — domain data structures."""

import pytest
from models import (
    OrderState,
    OrderType,
    MessageRole,
    CartTopping,
    CartItem,
    CustomerInfo,
    Message,
    ToolCallRequest,
    AddToCartResult,
    RemoveFromCartResult,
    UpdateItemResult,
    ViewCartResult,
    SetCustomerInfoResult,
    RequestReviewResult,
    ConfirmOrderResult,
    CancelOrderResult,
)


class TestEnums:
    def test_order_state_values(self):
        assert OrderState.BUILDING == "building"
        assert OrderState.REVIEW == "review"
        assert OrderState.PAYMENT_PENDING == "payment_pending"
        assert OrderState.COMPLETED == "completed"
        assert OrderState.CANCELLED == "cancelled"

    def test_order_type_values(self):
        assert OrderType.DELIVERY == "delivery"
        assert OrderType.PICKUP == "pickup"

    def test_message_role_values(self):
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"
        assert MessageRole.TOOL == "tool"


class TestCartTopping:
    def test_create(self):
        t = CartTopping(topping_id="extra_cheese", name="Extra Cheese", price=1.50)
        assert t.topping_id == "extra_cheese"
        assert t.name == "Extra Cheese"
        assert t.price == 1.50

    def test_price_non_negative(self):
        with pytest.raises(Exception):
            CartTopping(topping_id="bad", name="Bad", price=-1.0)


class TestCartItem:
    def test_create_minimal(self):
        item = CartItem(
            product_id="pizza_margherita",
            name="Margherita",
            category="Pizzas",
        )
        assert item.product_id == "pizza_margherita"
        assert item.name == "Margherita"
        assert item.category == "Pizzas"
        assert item.quantity == 1
        assert item.size is None
        assert item.toppings == []
        assert item.options == {}
        assert item.special_instructions is None
        assert item.base_price == 0.0
        assert item.line_total == 0.0
        assert item.missing_options == []
        assert item.id  # UUID auto-generated
        assert len(item.id) == 36  # Standard UUID format

    def test_create_full(self):
        item = CartItem(
            product_id="pizza_margherita",
            name="Margherita",
            category="Pizzas",
            quantity=2,
            size="large",
            toppings=[
                CartTopping(topping_id="extra_cheese", name="Extra Cheese", price=1.50)
            ],
            options={"sauce": "marinara"},
            special_instructions="well done",
            base_price=15.99,
            line_total=34.98,
            missing_options=["crust_type"],
        )
        assert item.quantity == 2
        assert item.size == "large"
        assert len(item.toppings) == 1
        assert item.options == {"sauce": "marinara"}
        assert item.special_instructions == "well done"
        assert item.missing_options == ["crust_type"]

    def test_id_is_unique(self):
        item1 = CartItem(product_id="a", name="A", category="Test")
        item2 = CartItem(product_id="b", name="B", category="Test")
        assert item1.id != item2.id

    def test_quantity_minimum(self):
        with pytest.raises(Exception):
            CartItem(product_id="a", name="A", category="Test", quantity=0)


class TestCustomerInfo:
    def test_empty(self):
        info = CustomerInfo()
        assert info.name is None
        assert info.phone is None
        assert info.address is None
        assert info.order_type is None

    def test_partial(self):
        info = CustomerInfo(name="John", phone="555-0123")
        assert info.name == "John"
        assert info.phone == "555-0123"
        assert info.address is None
        assert info.order_type is None

    def test_full(self):
        info = CustomerInfo(
            name="John",
            phone="555-0123",
            address="123 Main St",
            order_type=OrderType.DELIVERY,
        )
        assert info.order_type == OrderType.DELIVERY


class TestMessage:
    def test_user_message(self):
        msg = Message(role=MessageRole.USER, content="Hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello"
        assert msg.tool_calls is None
        assert msg.tool_call_id is None

    def test_assistant_with_tool_calls(self):
        tc = ToolCallRequest(id="call_1", name="add_to_cart", arguments={"product_name": "Margherita"})
        msg = Message(role=MessageRole.ASSISTANT, content=None, tool_calls=[tc])
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content is None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "add_to_cart"

    def test_assistant_text_only(self):
        msg = Message(role=MessageRole.ASSISTANT, content="Great choice!")
        assert msg.content == "Great choice!"
        assert msg.tool_calls is None

    def test_tool_result(self):
        msg = Message(
            role=MessageRole.TOOL,
            content='{"success": true}',
            tool_call_id="call_1",
            name="add_to_cart",
        )
        assert msg.role == MessageRole.TOOL
        assert msg.tool_call_id == "call_1"
        assert msg.name == "add_to_cart"


class TestToolCallRequest:
    def test_create(self):
        tc = ToolCallRequest(
            id="call_abc",
            name="add_to_cart",
            arguments={"product_name": "Pepperoni", "quantity": 2},
        )
        assert tc.id == "call_abc"
        assert tc.name == "add_to_cart"
        assert tc.arguments["product_name"] == "Pepperoni"
        assert tc.arguments["quantity"] == 2

    def test_empty_arguments_default(self):
        tc = ToolCallRequest(id="call_1", name="view_cart")
        assert tc.arguments == {}


class TestToolResultTypes:
    def test_add_to_cart_success(self):
        item = CartItem(product_id="p", name="N", category="C")
        result = AddToCartResult(success=True, item=item)
        assert result.success is True
        assert result.item is not None
        assert result.suggestions == []
        assert result.missing_options == []
        assert result.issues == []

    def test_add_to_cart_not_found(self):
        result = AddToCartResult(
            success=False, suggestions=["Pepperoni", "Pepperoni Feast"]
        )
        assert result.success is False
        assert result.item is None
        assert "Pepperoni" in result.suggestions

    def test_add_to_cart_with_issues(self):
        item = CartItem(product_id="p", name="N", category="C")
        result = AddToCartResult(success=True, item=item, issues=["Invalid size, used default"])
        assert len(result.issues) == 1

    def test_remove_from_cart_success(self):
        item = CartItem(product_id="p", name="N", category="C")
        result = RemoveFromCartResult(success=True, removed=[item])
        assert result.success is True
        assert len(result.removed) == 1
        assert result.matches == []

    def test_remove_from_cart_ambiguous(self):
        item1 = CartItem(product_id="p1", name="N1", category="C")
        item2 = CartItem(product_id="p2", name="N2", category="C")
        result = RemoveFromCartResult(success=False, matches=[item1, item2])
        assert result.success is False
        assert len(result.matches) == 2
        assert result.removed == []

    def test_view_cart_result(self):
        items = [CartItem(product_id="p", name="N", category="C")]
        result = ViewCartResult(items=items, subtotal=12.99, item_count=1)
        assert result.item_count == 1
        assert result.subtotal == 12.99

    def test_set_customer_info_result(self):
        info = CustomerInfo(name="John")
        result = SetCustomerInfoResult(
            success=True, info=info, missing_required=["phone", "address"]
        )
        assert result.success is True
        assert "phone" in result.missing_required

    def test_request_review_blocked(self):
        result = RequestReviewResult(
            success=False, issues=["Cart is empty"]
        )
        assert result.success is False
        assert "Cart is empty" in result.issues

    def test_request_review_allowed(self):
        result = RequestReviewResult(success=True, issues=[])
        assert result.success is True
        assert result.issues == []

    def test_confirm_order(self):
        result = ConfirmOrderResult(success=True, order_id="1234ABCD", total=45.97)
        assert result.success is True
        assert result.order_id == "1234ABCD"
        assert result.total == 45.97

    def test_cancel_order(self):
        result = CancelOrderResult(success=True, message="Order cancelled.")
        assert result.success is True
        assert "cancelled" in result.message.lower()

"""Tests for agent_loop.py — orchestration and transition logic."""

from unittest import mock

import pytest

from catalogue import Catalogue
from models import CartItem, CustomerInfo, Message, MessageRole, OrderState, OrderType
from pricing import PricingEngine
from session import OrderSession
from agent_loop import _apply_transition, _lookup_tool, process_turn


@pytest.fixture
def catalogue():
    return Catalogue("menu.json")


@pytest.fixture
def pricing(catalogue):
    return PricingEngine(catalogue.menu_data)


@pytest.fixture
def session():
    s = OrderSession()
    s.cart = [
        CartItem(
            product_id="pizza_margherita",
            name="Margherita",
            category="Pizzas",
            size="medium",
            quantity=1,
            base_price=12.99,
            line_total=12.99,
        )
    ]
    s.customer = CustomerInfo(
        name="John",
        phone="555-0123",
        order_type=OrderType.PICKUP,
    )
    return s


# =============================================================================
# Transitions
# =============================================================================


class TestApplyTransition:
    def test_no_pending_transition(self, session):
        original = session.state
        _apply_transition(session)
        assert session.state == original

    def test_cancel_always_wins(self, session):
        session._pending_transition = OrderState.CANCELLED
        _apply_transition(session)
        assert session.state == OrderState.CANCELLED
        assert session._pending_transition is None

    def test_cancel_even_with_empty_cart(self):
        session = OrderSession()  # no cart, no customer
        session._pending_transition = OrderState.CANCELLED
        _apply_transition(session)
        assert session.state == OrderState.CANCELLED

    def test_review_blocked_empty_cart(self):
        session = OrderSession()
        session._pending_transition = OrderState.REVIEW
        _apply_transition(session)
        assert session.state == OrderState.BUILDING  # unchanged
        assert session._pending_transition is None  # cleared

    def test_review_blocked_no_name(self, session):
        session.customer.name = None
        session._pending_transition = OrderState.REVIEW
        _apply_transition(session)
        assert session.state == OrderState.BUILDING
        assert session._pending_transition is None

    def test_review_blocked_no_phone(self, session):
        session.customer.phone = None
        session._pending_transition = OrderState.REVIEW
        _apply_transition(session)
        assert session.state == OrderState.BUILDING

    def test_review_blocked_delivery_no_address(self, session):
        session.customer.order_type = OrderType.DELIVERY
        session.customer.address = None
        session._pending_transition = OrderState.REVIEW
        _apply_transition(session)
        assert session.state == OrderState.BUILDING

    def test_review_allowed(self, session):
        session._pending_transition = OrderState.REVIEW
        _apply_transition(session)
        assert session.state == OrderState.REVIEW

    def test_payment_pending(self, session):
        session.state = OrderState.REVIEW
        session._pending_transition = OrderState.PAYMENT_PENDING
        _apply_transition(session)
        assert session.state == OrderState.PAYMENT_PENDING


# =============================================================================
# Tool Lookup
# =============================================================================


class TestLookupTool:
    def test_finds_tool_by_name(self):
        from tools import TOOLS_BY_STATE
        funcs = TOOLS_BY_STATE[OrderState.BUILDING]
        fn = _lookup_tool("add_to_cart", funcs)
        assert fn is not None
        assert fn.__tool_name__ == "add_to_cart"

    def test_returns_none_for_unknown(self):
        from tools import TOOLS_BY_STATE
        funcs = TOOLS_BY_STATE[OrderState.BUILDING]
        fn = _lookup_tool("nonexistent_tool", funcs)
        assert fn is None


# =============================================================================
# Full process_turn (with mock LLM)
# =============================================================================


class MockLLMClient:
    """Fake LLM client that returns canned responses."""

    def __init__(self, text="", tool_calls=None):
        self.text = text
        self.tool_calls = tool_calls or []

    def chat(self, messages, tools=None):
        return self.text, self.tool_calls


class TestProcessTurn:
    def test_greeting_flow(self, catalogue, pricing):
        """Simulate the greeting: user says Hi, LLM responds with text."""
        session = OrderSession()
        mock_client = MockLLMClient(
            text="Welcome to Mario's Pizzeria! What can I get for you today?"
        )

        text = process_turn(session, "Hi", catalogue, pricing, mock_client)

        assert "Welcome" in text
        assert len(session.conversation) == 2  # user + assistant
        assert session.conversation[0].role == MessageRole.USER
        assert session.conversation[1].role == MessageRole.ASSISTANT

    def test_add_item_flow(self, catalogue, pricing):
        """Simulate adding an item via tool call."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(
            text="Adding that for you!",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="add_to_cart",
                    arguments={"product_name": "Margherita", "quantity": 1},
                )
            ],
        )

        text = process_turn(
            session, "I want a Margherita pizza", catalogue, pricing, mock_client
        )

        assert "Adding" in text
        assert len(session.cart) == 1
        assert session.cart[0].product_id == "pizza_margherita"
        # Verify tool result message appended
        assert len(session.conversation) == 3  # user + assistant + tool result
        assert session.conversation[2].role == MessageRole.TOOL
        assert session.conversation[2].name == "add_to_cart"

    def test_add_and_review_flow(self, catalogue, pricing):
        """Add an item, then request review via two tool calls in one turn."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(
            text="Here's your order summary.",
            tool_calls=[
                ToolCallRequest(
                    id="c1",
                    name="add_to_cart",
                    arguments={"product_name": "Margherita"},
                ),
                ToolCallRequest(
                    id="c2",
                    name="set_customer_info",
                    arguments={"name": "John", "phone": "555-0123"},
                ),
                ToolCallRequest(
                    id="c3",
                    name="request_review",
                    arguments={},
                ),
            ],
        )

        text = process_turn(
            session,
            "I want a Margherita pizza, my name is John, phone 555-0123, that's all",
            catalogue,
            pricing,
            mock_client,
        )

        assert len(session.cart) == 1
        assert session.customer.name == "John"
        assert session.customer.phone == "555-0123"
        assert session.state == OrderState.REVIEW

    def test_unknown_tool_is_handled(self, catalogue, pricing):
        """LLM calls a tool that doesn't exist — should not crash."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(
            text="Let me try something.",
            tool_calls=[
                ToolCallRequest(
                    id="c1",
                    name="hack_the_planet",
                    arguments={},
                )
            ],
        )

        text = process_turn(
            session, "do something weird", catalogue, pricing, mock_client
        )
        # Should not raise
        assert len(session.conversation) == 3  # user + assistant + error tool result
        assert "Unknown tool" in session.conversation[2].content

    def test_tool_error_is_handled(self, catalogue, pricing):
        """Missing required argument — tool should raise, loop should catch."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(
            text="Let me try...",
            tool_calls=[
                ToolCallRequest(
                    id="c1",
                    name="add_to_cart",
                    arguments={},  # missing required 'product_name'
                )
            ],
        )

        text = process_turn(
            session, "add nothing", catalogue, pricing, mock_client
        )
        # Should not crash — error result appended
        assert session.conversation[-1].role == MessageRole.TOOL
        assert "error" in session.conversation[-1].content.lower()

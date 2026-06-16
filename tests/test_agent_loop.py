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
        session = OrderSession()
        session._pending_transition = OrderState.CANCELLED
        _apply_transition(session)
        assert session.state == OrderState.CANCELLED

    def test_review_blocked_empty_cart(self):
        session = OrderSession()
        session._pending_transition = OrderState.REVIEW
        _apply_transition(session)
        assert session.state == OrderState.BUILDING
        assert session._pending_transition is None

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

    def test_completed(self, session):
        session.state = OrderState.PAYMENT_PENDING
        session._pending_transition = OrderState.COMPLETED
        _apply_transition(session)
        assert session.state == OrderState.COMPLETED


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
    """Fake LLM client with multi-call support for the loop-based agent.

    Accepts a list of (text, tool_calls) tuples — one per expected chat() call.
    The loop consumes responses until it gets one without tool_calls, or until
    the response list is exhausted (returns empty string, no tools).
    """

    def __init__(self, responses=None):
        self._responses = responses or []
        self._idx = 0

    def chat(self, messages, tools=None):
        if self._idx < len(self._responses):
            text, tool_calls = self._responses[self._idx]
            self._idx += 1
            return text or "", tool_calls or []
        return "", []


class TestProcessTurn:
    def test_greeting_flow(self, catalogue, pricing):
        """No tool calls — one LLM call, text returned directly."""
        session = OrderSession()
        mock_client = MockLLMClient(responses=[
            ("Hey! What can I get for you?", []),
        ])

        text = process_turn(session, "Hi", catalogue, pricing, mock_client)

        assert "Hey" in text
        assert len(session.conversation) == 2  # user + assistant

    def test_add_item_flow(self, catalogue, pricing):
        """Tool call → Call 1 returns tools, Call 2 returns final text."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(responses=[
            ("", [ToolCallRequest(id="c1", name="add_to_cart",
                                  arguments={"product_name": "Margherita", "quantity": 1})]),
            ("Added medium Margherita — $12.99. Anything else?", []),
        ])

        text = process_turn(
            session, "I want a Margherita pizza", catalogue, pricing, mock_client
        )

        assert "Added" in text
        assert len(session.cart) == 1
        assert session.cart[0].product_id == "pizza_margherita"
        # Conversation: user + assistant(tool_calls) + tool_result + assistant(text)
        assert len(session.conversation) == 4
        assert session.conversation[1].role == MessageRole.ASSISTANT
        assert session.conversation[1].tool_calls is not None
        assert session.conversation[2].role == MessageRole.TOOL
        assert session.conversation[3].role == MessageRole.ASSISTANT
        assert "Added" in session.conversation[3].content

    def test_add_and_review_flow(self, catalogue, pricing):
        """Multiple tool calls → Call 1 returns tools, Call 2 responds."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(responses=[
            (
                "",
                [
                    ToolCallRequest(id="c1", name="add_to_cart",
                                   arguments={"product_name": "Margherita"}),
                    ToolCallRequest(id="c2", name="set_customer_info",
                                   arguments={"name": "John", "phone": "555-0123"}),
                    ToolCallRequest(id="c3", name="request_review",
                                   arguments={}),
                ],
            ),
            ("Here's your order summary, John!", []),
        ])

        text = process_turn(
            session,
            "I want a Margherita pizza, my name is John, phone 555-0123",
            catalogue, pricing, mock_client,
        )

        assert len(session.cart) == 1
        assert session.customer.name == "John"
        assert session.customer.phone == "555-0123"
        assert session.state == OrderState.REVIEW
        assert "summary" in text.lower()

    def test_unknown_tool_is_handled(self, catalogue, pricing):
        """Unknown tool → error appended, Call 2 responds."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(responses=[
            ("", [ToolCallRequest(id="c1", name="hack_the_planet", arguments={})]),
            ("Sorry, something went wrong.", []),
        ])

        text = process_turn(
            session, "do something weird", catalogue, pricing, mock_client
        )
        assert "Unknown tool" in session.conversation[2].content
        # user + assistant(tools) + tool(error) + assistant(text)
        assert len(session.conversation) == 4

    def test_tool_error_is_handled(self, catalogue, pricing):
        """Tool raises — error caught, loop continues to next iteration."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(responses=[
            ("", [ToolCallRequest(id="c1", name="add_to_cart", arguments={})]),
            ("I couldn't add that. Please try again.", []),
        ])

        text = process_turn(
            session, "add nothing", catalogue, pricing, mock_client
        )
        tool_msgs = [m for m in session.conversation if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 1
        assert "error" in tool_msgs[0].content.lower()

    def test_chained_tool_calls(self, catalogue, pricing):
        """Model chains tools across iterations — e.g. browse → add → respond."""
        session = OrderSession()
        from models import ToolCallRequest
        mock_client = MockLLMClient(responses=[
            # Iteration 1: browse the menu first
            ("", [ToolCallRequest(id="c1", name="browse_menu",
                                  arguments={"category": "Pizzas"})]),
            # Iteration 2: now add an item based on what was seen
            ("", [ToolCallRequest(id="c2", name="add_to_cart",
                                  arguments={"product_name": "Margherita", "quantity": 1})]),
            # Iteration 3: done — respond
            ("Added medium Margherita — $12.99. Anything else?", []),
        ])

        text = process_turn(
            session, "I want a pizza but what do you have? Margherita please",
            catalogue, pricing, mock_client,
        )

        assert "Added" in text
        assert len(session.cart) == 1
        # user + asst(tools) + tool + asst(tools) + tool + asst(text) = 6
        assert len(session.conversation) == 6

    def test_empty_text_without_tools_returns_fallback(self, catalogue, pricing):
        """Model returns empty text and no tools — fallback message used."""
        session = OrderSession()
        mock_client = MockLLMClient(responses=[
            ("", []),  # empty text, no tools — degenerate
        ])

        text = process_turn(session, "Hi", catalogue, pricing, mock_client)
        assert len(text) > 0
        assert "I've processed" in text

    def test_max_iterations_fallback(self, catalogue, pricing):
        """Model keeps calling tools forever — hits max, returns fallback."""
        session = OrderSession()
        from models import ToolCallRequest
        # Every response has tool calls — model never converges
        responses = [
            ("", [ToolCallRequest(id=f"c{i}", name="view_cart", arguments={})])
            for i in range(10)  # way more than MAX_ITERATIONS=5
        ]
        mock_client = MockLLMClient(responses=responses)

        text = process_turn(
            session, "something that loops", catalogue, pricing, mock_client
        )

        assert "I've processed" in text
        # Stopped at MAX_ITERATIONS (5), not all 10 responses consumed
        # user + 5*(asst+tool) + asst(fallback) = 1 + 10 + 1 = 12
        assert len(session.conversation) == 12

    def test_state_change_mid_loop(self, catalogue, pricing):
        """State changes during loop — next iteration gets new tool set."""
        session = OrderSession()
        # Place session in REVIEW state where confirm_order is available
        session.state = OrderState.REVIEW
        from models import ToolCallRequest
        mock_client = MockLLMClient(responses=[
            # Iteration 1: confirm the order
            ("", [ToolCallRequest(id="c1", name="confirm_order", arguments={})]),
            # Iteration 2: COMPLETED state — no tools available, must respond
            ("Your order is confirmed! Total: $0.00.", []),
        ])

        text = process_turn(
            session, "confirm my order", catalogue, pricing, mock_client
        )

        assert session.state == OrderState.COMPLETED
        assert "confirmed" in text.lower()

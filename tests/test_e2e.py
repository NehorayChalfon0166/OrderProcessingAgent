"""End-to-end tests — simulate real user conversations through process_turn.

These tests mock only the LLM client; every other component (session, catalogue,
pricing, tools, state machine, persistence) runs for real. They catch bugs that
unit tests miss: broken import chains, missing state transitions, tool-call
regressions, and conversation-flow edge cases.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from agent_loop import process_turn
from catalogue import Catalogue
from db import Database
from llm_client import LLMClient, build_tool_definitions, messages_to_openai
from models import Message, MessageRole, OrderState, OrderType, ToolCallRequest
from pricing import PricingEngine
from session import OrderSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_menu(path, name="Test Restaurant"):
    menu = {
        "restaurant_name": name,
        "currency": "USD",
        "categories": [
            {
                "name": "Pizza",
                "items": [
                    {
                        "id": "pizza_margherita", "name": "Margherita",
                        "sizes": {"small": 9.99, "medium": 12.99, "large": 15.99},
                        "default_size": "medium",
                    },
                    {
                        "id": "pizza_pepperoni", "name": "Pepperoni",
                        "sizes": {"small": 10.99, "medium": 13.99, "large": 16.99},
                        "default_size": "medium",
                    },
                ],
            },
            {
                "name": "Drinks",
                "items": [
                    {"id": "drink_cola", "name": "Cola", "price": 2.99},
                ],
            },
        ],
        "toppings": [
            {"id": "extra_cheese", "name": "Extra Cheese", "price": 1.50},
        ],
        "deals": [],
        "delivery_fee": 3.99,
        "min_order_amount": 10.00,
    }
    path.write_text(json.dumps(menu))


class MockLLMClient:
    """Mock LLM that returns scripted responses and tool calls.

    Each call to chat() consumes the next entry in the script.
    An entry is either a plain string (assistant text, no tool calls)
    or a list of (tool_name, tool_args) tuples.
    """

    def __init__(self, script: list):
        self._script = script
        self._calls: list[tuple[list[dict], list[dict] | None]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        self._calls.append((messages, tools))
        if not self._script:
            return "I don't know what to say.", []
        entry = self._script.pop(0)
        if isinstance(entry, str):
            return entry, []
        # List of (name, args) tool calls
        tool_calls = []
        for i, (name, args) in enumerate(entry):
            tool_calls.append(ToolCallRequest(
                id=f"call_{i}", name=name, arguments=args,
            ))
        return "", tool_calls


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_setup(tmp_path):
    """Create a fully wired test environment: catalogue, pricing, DB, session."""
    menu_path = tmp_path / "menu.json"
    _make_menu(menu_path)
    catalogue = Catalogue(str(menu_path))
    pricing = PricingEngine(catalogue.menu_data)
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    session = OrderSession(restaurant_id="test_restaurant")
    session._db = db
    session.save()
    return {
        "catalogue": catalogue,
        "pricing": pricing,
        "db": db,
        "session": session,
        "tmp": tmp_path,
    }


# ---------------------------------------------------------------------------
# E2E: Full order flow
# ---------------------------------------------------------------------------


class TestFullOrderFlow:
    def test_complete_cash_order(self, e2e_setup):
        """Simulate: add items → set info → review → confirm (cash)."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]

        mock_llm = MockLLMClient([
            # Turn 1: add margherita
            [("add_to_cart", {"product_name": "Margherita", "size": "large"})],
            "Added a large Margherita. Anything else?",
            # Turn 2: add pepperoni + set info
            [
                ("add_to_cart", {"product_name": "Pepperoni", "quantity": 1}),
                ("set_customer_info", {"name": "Test User", "phone": "+1234567890", "order_type": "pickup"}),
            ],
            "Added Pepperoni and saved your info.",
            # Turn 3: review + confirm
            [("request_review", {})],
            "Ready to review.",
            [("confirm_order", {"payment_method": "cash"})],
            "Order confirmed!",
        ])

        # Turn 1
        resp = process_turn(s, "I want a large Margherita", cat, prc, mock_llm)
        assert "Margherita" in resp
        assert len(s.cart) == 1
        assert s.cart[0].size == "large"
        assert s.state == OrderState.BUILDING

        # Turn 2
        resp = process_turn(s, "Add Pepperoni. Name: Test User, phone 1234567890, pickup", cat, prc, mock_llm)
        assert len(s.cart) == 2
        assert s.customer.name == "Test User"
        assert s.customer.order_type == OrderType.PICKUP

        # Turn 3 — review
        resp = process_turn(s, "I'm ready to order", cat, prc, mock_llm)
        assert s.state == OrderState.REVIEW

        # Turn 4 — confirm cash
        resp = process_turn(s, "Confirm, pay cash", cat, prc, mock_llm)
        assert s.state == OrderState.COMPLETED
        assert s.payment_method == "cash"

    def test_delivery_order_with_address(self, e2e_setup):
        """Delivery order with address → verify customer info is set correctly."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]

        mock_llm = MockLLMClient([
            [("add_to_cart", {"product_name": "Margherita"})],
            "Added Margherita.",
            [("set_customer_info", {
                "name": "Jane", "phone": "+9876543210",
                "address": "456 Oak St", "order_type": "delivery",
            })],
            "Info saved.",
            [("request_review", {})],
            "Review ready.",
            [("confirm_order", {"payment_method": "cash"})],
            "Confirmed!",
        ])

        process_turn(s, "Margherita please", cat, prc, mock_llm)
        process_turn(s, "Jane, 9876543210, 456 Oak St, delivery", cat, prc, mock_llm)
        assert s.customer.address == "456 Oak St"
        assert s.customer.order_type == OrderType.DELIVERY

        process_turn(s, "Ready", cat, prc, mock_llm)
        assert s.state == OrderState.REVIEW

        process_turn(s, "Confirm", cat, prc, mock_llm)
        assert s.state == OrderState.COMPLETED


class TestErrorRecovery:
    def test_unknown_tool_handled_gracefully(self, e2e_setup):
        """LLM calls a tool that doesn't exist — agent continues."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]

        mock_llm = MockLLMClient([
            # Turn 1: LLM calls a non-existent tool, then recovers
            [
                ("browse_menu", {}),  # does not exist
                ("add_to_cart", {"product_name": "Margherita"}),
            ],
            "Added Margherita despite the error.",
        ])

        resp = process_turn(s, "Show me the menu", cat, prc, mock_llm)
        # Should recover — the unknown tool is logged, add_to_cart still runs
        assert len(s.cart) == 1
        assert s.cart[0].name == "Margherita"

    def test_max_iterations_fallback(self, e2e_setup):
        """LLM keeps calling tools forever — agent hits max_iterations fallback."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]

        # Every response is a tool call — never clean text
        mock_llm = MockLLMClient([
            [("add_to_cart", {"product_name": "Margherita"})],
            [("add_to_cart", {"product_name": "Pepperoni"})],
            [("add_to_cart", {"product_name": "Cola"})],
            [("view_cart", {})],
            [("view_cart", {})],
            [("view_cart", {})],
        ])

        resp = process_turn(s, "Add many things", cat, prc, mock_llm, max_iterations=3)
        # Should hit max_iterations=3 and return fallback text
        assert len(resp) > 0  # got some fallback
        # Cart has items from the first 3 iterations
        assert len(s.cart) >= 1


class TestSessionPersistence:
    def test_session_survives_turn_boundary(self, e2e_setup):
        """Session state persists correctly between process_turn calls."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]
        db = e2e_setup["db"]

        mock_llm = MockLLMClient([
            [("add_to_cart", {"product_name": "Margherita", "size": "large"})],
            "Added.",
            [("set_customer_info", {"name": "Persist Test", "phone": "+111"})],
            "Saved.",
        ])

        process_turn(s, "Large Margherita", cat, prc, mock_llm)
        process_turn(s, "Name: Persist Test, phone: 111", cat, prc, mock_llm)

        # Reload from DB — should have same state
        loaded = db.load_session("test_restaurant", s.session_id)
        assert loaded is not None
        assert len(loaded.cart) == 1
        assert loaded.customer.name == "Persist Test"

    def test_terminal_session_replaced(self, e2e_setup):
        """After COMPLETED, next get_or_create returns a fresh session."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]
        db = e2e_setup["db"]

        mock_llm = MockLLMClient([
            [("add_to_cart", {"product_name": "Margherita"})],
            "Added.",
            [("set_customer_info", {"name": "X", "phone": "+972511111111"})],
            "Saved.",
            [("request_review", {})],
            "Review.",
            [("confirm_order", {"payment_method": "cash"})],
            "Done.",
        ])

        process_turn(s, "Margherita", cat, prc, mock_llm)
        process_turn(s, "Name X phone +972511111111", cat, prc, mock_llm)
        process_turn(s, "Ready", cat, prc, mock_llm)
        process_turn(s, "Confirm", cat, prc, mock_llm)
        assert s.state == OrderState.COMPLETED

        # Next contact from same phone → fresh session
        from session_router import SessionRouter
        router = SessionRouter()
        new_session = router.get_or_create("test_restaurant", "972511111111", db=db)
        # Same phone number → same session_id
        assert new_session.session_id == "972511111111"
        # But fresh cart and state
        assert new_session.cart == []
        assert new_session.state == OrderState.BUILDING


class TestStateMachine:
    def test_cancel_from_building(self, e2e_setup):
        """Cancel during BUILDING state."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]

        mock_llm = MockLLMClient([
            [("cancel_order", {})],
            "Order cancelled.",
        ])

        resp = process_turn(s, "Cancel my order", cat, prc, mock_llm)
        assert s.state == OrderState.CANCELLED
        assert "cancel" in resp.lower()

    def test_cannot_confirm_empty_cart(self, e2e_setup):
        """request_review fails on empty cart — agent should catch it."""
        s = e2e_setup["session"]
        cat = e2e_setup["catalogue"]
        prc = e2e_setup["pricing"]

        mock_llm = MockLLMClient([
            [("request_review", {})],
            "Cannot review — cart is empty.",
        ])

        resp = process_turn(s, "I'm ready to order", cat, prc, mock_llm)
        # request_review should fail (success=False), state stays BUILDING
        assert s.state == OrderState.BUILDING
        # The LLM got the failure result and responded accordingly
        assert "empty" in resp.lower()

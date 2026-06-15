"""Tests for session.py — order state and persistence."""

import tempfile
from pathlib import Path

import pytest

from models import (
    CartItem,
    CustomerInfo,
    MessageRole,
    OrderState,
    OrderType,
    ToolCallRequest,
)
from session import OrderSession


class TestOrderSession:
    def test_create_defaults(self):
        session = OrderSession()
        assert len(session.session_id) == 8
        assert session.state == OrderState.BUILDING
        assert session.cart == []
        assert session.customer.name is None
        assert session.conversation == []
        assert session.created_at is not None
        assert session.updated_at is not None
        assert session._pending_transition is None

    def test_create_with_items(self):
        item = CartItem(
            product_id="pizza_margherita",
            name="Margherita",
            category="Pizzas",
            size="medium",
        )
        session = OrderSession(cart=[item])
        assert len(session.cart) == 1

    def test_create_with_customer(self):
        customer = CustomerInfo(name="John", phone="555-0123")
        session = OrderSession(customer=customer)
        assert session.customer.name == "John"

    def test_is_complete(self):
        session = OrderSession(state=OrderState.COMPLETED)
        assert session.is_complete is True
        assert session.is_cancelled is False
        assert session.is_active is False

    def test_is_cancelled(self):
        session = OrderSession(state=OrderState.CANCELLED)
        assert session.is_cancelled is True
        assert session.is_complete is False
        assert session.is_active is False

    def test_is_active(self):
        session = OrderSession(state=OrderState.BUILDING)
        assert session.is_active is True

    def test_pending_transition_not_serialized(self):
        session = OrderSession()
        session._pending_transition = OrderState.REVIEW
        data = session.model_dump()
        assert "_pending_transition" not in data

    def test_pending_transition_survives_roundtrip(self):
        """_pending_transition is excluded from dump but still readable."""
        session = OrderSession()
        session._pending_transition = OrderState.REVIEW
        assert session._pending_transition == OrderState.REVIEW

    def test_session_id_unique(self):
        s1 = OrderSession()
        s2 = OrderSession()
        assert s1.session_id != s2.session_id


class TestConversationHelpers:
    def test_add_user_message(self):
        session = OrderSession()
        session.add_user_message("Hello")
        assert len(session.conversation) == 1
        assert session.conversation[0].role == MessageRole.USER
        assert session.conversation[0].content == "Hello"

    def test_add_assistant_text_only(self):
        session = OrderSession()
        session.add_assistant_message(content="Welcome!")
        assert len(session.conversation) == 1
        assert session.conversation[0].role == MessageRole.ASSISTANT
        assert session.conversation[0].content == "Welcome!"
        assert session.conversation[0].tool_calls is None

    def test_add_assistant_with_tool_calls(self):
        session = OrderSession()
        tc = ToolCallRequest(
            id="call_1", name="add_to_cart", arguments={"product_name": "Margherita"}
        )
        session.add_assistant_message(content=None, tool_calls=[tc])
        assert len(session.conversation) == 1
        msg = session.conversation[0]
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content is None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "add_to_cart"

    def test_add_tool_result(self):
        session = OrderSession()
        session.add_tool_result(
            tool_call_id="call_1",
            tool_name="add_to_cart",
            result_json='{"success": true}',
        )
        assert len(session.conversation) == 1
        msg = session.conversation[0]
        assert msg.role == MessageRole.TOOL
        assert msg.tool_call_id == "call_1"
        assert msg.name == "add_to_cart"
        assert msg.content == '{"success": true}'


class TestPersistence:
    def test_save_and_load(self):
        session = OrderSession()
        session.add_user_message("Hello")
        session.cart = [
            CartItem(
                product_id="pizza_margherita",
                name="Margherita",
                category="Pizzas",
                size="medium",
            )
        ]
        session.customer = CustomerInfo(name="John")

        with tempfile.TemporaryDirectory() as tmpdir:
            session.save(sessions_dir=tmpdir)
            loaded = OrderSession.load(session.session_id, sessions_dir=tmpdir)

        assert loaded.session_id == session.session_id
        assert loaded.state == session.state
        assert len(loaded.cart) == 1
        assert loaded.cart[0].product_id == "pizza_margherita"
        assert loaded.customer.name == "John"
        assert len(loaded.conversation) == 1
        assert loaded.conversation[0].content == "Hello"
        # _pending_transition is not persisted
        assert loaded._pending_transition is None

    def test_save_creates_directory(self):
        session = OrderSession()
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir) / "new_sessions"
            session.save(sessions_dir=str(sessions_dir))
            assert sessions_dir.exists()
            assert (sessions_dir / f"{session.session_id}.json").exists()

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                OrderSession.load("NONEXIST", sessions_dir=tmpdir)

    def test_updated_at_changes_on_save(self):
        session = OrderSession()
        original = session.updated_at
        with tempfile.TemporaryDirectory() as tmpdir:
            session.save(sessions_dir=tmpdir)
        assert session.updated_at != original

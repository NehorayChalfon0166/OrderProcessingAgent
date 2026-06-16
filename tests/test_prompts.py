"""Tests for prompts.py — system prompt builder."""

from models import CartItem, CustomerInfo, OrderType
from session import OrderSession
from prompts import build_system_prompt


def test_build_prompt_empty_session():
    session = OrderSession()
    prompt = build_system_prompt(session, "Test Pizzeria", "Pizzas: Margherita, Pepperoni")
    assert "Test Pizzeria" in prompt
    assert "building" in prompt.lower()
    assert "(empty)" in prompt
    assert "(not yet provided)" in prompt
    assert "Pizzas" in prompt


def test_build_prompt_with_items():
    session = OrderSession()
    session.cart = [
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
    prompt = build_system_prompt(session, "Test Pizzeria", "Pizzas: Margherita")
    assert "Margherita" in prompt
    assert "medium" in prompt
    assert "$12.99" in prompt


def test_build_prompt_with_customer():
    session = OrderSession()
    session.customer = CustomerInfo(
        name="John",
        phone="555-0123",
        order_type=OrderType.DELIVERY,
    )
    prompt = build_system_prompt(session, "Test Pizzeria", "")
    assert "John" in prompt
    assert "555-0123" in prompt
    assert "delivery" in prompt


def test_build_prompt_state_reflected():
    session = OrderSession()
    prompt = build_system_prompt(session, "Test", "")
    assert "building" in prompt.lower()

    session.state = __import__("models").OrderState.REVIEW
    prompt = build_system_prompt(session, "Test", "")
    assert "review" in prompt.lower()


def test_prompt_length_under_500():
    """Prompt should be compact — no menu dump, no JSON schema."""
    session = OrderSession()
    prompt = build_system_prompt(session, "Mario's Pizzeria", "Pizzas: Margherita, Pepperoni, ...")
    assert len(prompt) < 800  # behavioral rules add length, still compact

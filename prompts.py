"""System prompt builder for the order processing agent.

Radically simpler than v1 — no JSON schema, no full menu, no per-state
instruction blocks. Tool availability IS the instruction. The LLM discovers
the menu through tool results.
"""

from __future__ import annotations

from models import CustomerInfo, OrderState
from session import OrderSession


def build_system_prompt(
    session: OrderSession,
    restaurant_name: str,
    hints: str,
) -> str:
    """Build the system prompt for the current state.

    Args:
        session: Current order session.
        restaurant_name: Name from the menu (e.g. "Mario's Pizzeria").
        hints: Lightweight menu hints from catalogue.get_hints().

    Returns:
        A system prompt string, typically 200-400 chars.
    """
    cart_summary = _format_cart(session)
    customer_summary = _format_customer(session.customer)

    return (
        f"You are a friendly order-taking assistant for {restaurant_name}.\n"
        f"Current state: {session.state.value}\n"
        f"Use the available tools to help the customer. Do not invent menu "
        f"items — use add_to_cart and let the system validate the product.\n"
        f"\n"
        f"Cart:\n{cart_summary}\n"
        f"\n"
        f"Customer:\n{customer_summary}\n"
        f"\n"
        f"Menu categories: {hints}"
    )


def _format_cart(session: OrderSession) -> str:
    """Format current cart as text for the system prompt."""
    if not session.cart:
        return "(empty)"

    lines: list[str] = []
    for i, item in enumerate(session.cart, 1):
        line = f"{i}. {item.quantity}x {item.name}"
        if item.size:
            line += f" ({item.size})"
        if item.toppings:
            tops = ", ".join(t.name for t in item.toppings)
            line += f" + {tops}"
        if item.special_instructions:
            line += f" [{item.special_instructions}]"
        line += f" — ${item.line_total:.2f}"
        lines.append(line)

    return "\n".join(lines)


def _format_customer(info: CustomerInfo) -> str:
    """Format customer info as text for the system prompt."""
    parts: list[str] = []
    if info.name:
        parts.append(f"Name: {info.name}")
    if info.phone:
        parts.append(f"Phone: {info.phone}")
    if info.address:
        parts.append(f"Address: {info.address}")
    if info.order_type:
        parts.append(f"Type: {info.order_type.value}")

    if not parts:
        return "(not yet provided)"

    return "\n".join(parts)

"""System prompt builder for the order processing agent.

Single unified prompt for the loop-based agent. Tools are always available
— the LLM decides in each iteration whether to call tools or respond to the
customer. It naturally converges when it has everything it needs.
"""

from __future__ import annotations

from models import CustomerInfo
from session import OrderSession


def build_system_prompt(
    session: OrderSession,
    restaurant_name: str,
    hints: str,
) -> str:
    """Build the system prompt for the order-processing agent."""
    return (
        f"You are a friendly, brief order-taking assistant for {restaurant_name}.\n"
        f"Current state: {session.state.value}\n"
        f"\n"
        f"Respond in the same language the customer uses. If the customer\n"
        f"writes in English letters, respond in English. If they write in\n"
        f"Hebrew, respond in Hebrew. Product names in tool calls are always\n"
        f"in English.\n"
        f"\n"
        f"Use tools when you need to take action — add items, check the menu,\n"
        f"check the cart, set customer info, confirm orders, etc.\n"
        f"When you call a tool, you don't need to include text — you will see\n"
        f"the results and respond after. When a tool returns data the customer\n"
        f"asked for (menu, cart, etc.), present it directly — don't just\n"
        f"acknowledge it and wait. Do NOT re-greet mid-conversation.\n"
        f"If the customer ignores a clarification twice, pick a default and\n"
        f"move on. Keep other replies 1-3 sentences.\n"
        f"Only say an order is confirmed when the state IS \"completed\".\n"
        f"Let the system validate products. Do not guess the menu.\n"
        f"\n"
        f"Cart:\n{_format_cart(session)}\n"
        f"\n"
        f"Customer:\n{_format_customer(session.customer)}\n"
        f"\n"
        f"Menu (reference when asked, do not list in greeting):\n{hints}"
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

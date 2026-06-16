"""System prompt builder for the order processing agent.

Single unified prompt for the loop-based agent. Tools are always available
— the LLM decides in each iteration whether to call tools or respond to the
customer. It naturally converges when it has everything it needs.

The LLM discovers the menu through tool results — no menu dump in the prompt.
"""

from __future__ import annotations

from models import CustomerInfo
from session import OrderSession


def build_system_prompt(
    session: OrderSession,
    restaurant_name: str,
    hints: str,
) -> str:
    """Unified system prompt — LLM always has tools available.

    The loop lets the model call tools until it's ready to respond.
    No separate "you have tools" vs "you don't have tools" modes.
    """
    return (
        f"You are a friendly, brief order-taking assistant for {restaurant_name}.\n"
        f"Current state: {session.state.value}\n"
        f"\n"
        f"Use tools when you need to take action — add items, browse the menu,\n"
        f"check the cart, set customer info, confirm orders, etc.\n"
        f"When you call tools, you don't need to include text — you will see\n"
        f"the results and can respond after.\n"
        f"When you are ready to respond to the customer, keep it brief —\n"
        f"1 to 3 sentences.\n"
        f"Only say an order is confirmed when the state IS \"completed\".\n"
        f"Let the system validate products. Do not guess the menu.\n"
        f"\n"
        f"Cart:\n{_format_cart(session)}\n"
        f"\n"
        f"Customer:\n{_format_customer(session.customer)}\n"
        f"\n"
        f"Menu (reference when asked, do not list in greeting):\n{hints}"
    )


# Backward-compatible aliases — kept so existing imports don't break.
# Both point to the unified prompt since the loop pattern uses one prompt
# for all iterations.
build_tool_prompt = build_system_prompt
build_response_prompt = build_system_prompt


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

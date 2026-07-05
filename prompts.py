"""System prompt builder for the order processing agent.

Single unified prompt for the loop-based agent. Tools are always available
— the LLM decides in each iteration whether to call tools or respond to the
customer. It naturally converges when it has everything it needs.

Supports pluggable domains via domain.py. The default domain is 'order'.
"""

from __future__ import annotations

from models import CustomerInfo
from session import OrderSession


def build_system_prompt(
    session: OrderSession,
    restaurant_name: str,
    hints: str,
    domain_name: str = "order",
) -> str:
    """Build a system prompt. Uses domain template if available."""
    # Try domain-specific template first
    try:
        from domain import get_domain
    except ImportError:
        get_domain = None  # type: ignore[assignment]

    if get_domain is not None:
        try:
            domain = get_domain(domain_name)
            if domain.system_prompt_template:
                return domain.system_prompt_template.format(
                    restaurant_name=restaurant_name,
                    state=session.state.value,
                    cart_summary=_format_cart(session),
                    hints=hints,
                    customer_info=_format_customer(session.customer),
                )
        except KeyError as e:
            import logging
            logging.getLogger(__name__).warning(
                "Domain template missing key: %s — using default prompt", e
            )

    # Default order-domain prompt
    return (
        f"You are a friendly, brief order-taking assistant for {restaurant_name}.\n"
        f"Current state: {session.state.value}\n"
        f"\n"
        f"Respond in the same language the customer uses. The menu is in\n"
        f"English — use English for product names in tool calls, but always\n"
        f"converse with the customer in their language.\n"
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

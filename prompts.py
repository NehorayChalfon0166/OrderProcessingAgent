"""System prompt builder for the order processing agent.

Produces two variants for the two-call loop:
  - build_tool_prompt: Call 1 — LLM has tools, should use them silently
  - build_response_prompt: Call 2 — LLM has NO tools, responds naturally

The LLM discovers the menu through tool results — no menu dump in the prompt.
"""

from __future__ import annotations

from models import CustomerInfo
from session import OrderSession


def build_tool_prompt(
    session: OrderSession,
    restaurant_name: str,
    hints: str,
) -> str:
    """Prompt for Call 1 — LLM has tools available, should use them silently.

    The LLM should call tools without including text. The response to the
    customer will happen in Call 2 after tool results are available.
    """
    return (
        f"You are a friendly, brief order-taking assistant for {restaurant_name}.\n"
        f"Current state: {session.state.value}\n"
        f"\n"
        f"If the customer's request requires tool calls, call them — do not\n"
        f"include text, you will respond after seeing the results.\n"
        f"If no tools are needed, respond briefly (1-2 sentences).\n"
        f"\n"
        f"Cart:\n{_format_cart(session)}\n"
        f"\n"
        f"Customer:\n{_format_customer(session.customer)}\n"
        f"\n"
        f"Menu (reference when asked, do not list in greeting):\n{hints}"
    )


def build_response_prompt(
    session: OrderSession,
    restaurant_name: str,
    hints: str,
) -> str:
    """Prompt for Call 2 — LLM has NO tools, responds naturally to results.

    The conversation already contains the tool calls and their results.
    The LLM's job is to tell the customer what happened, concisely.
    Tools are NOT sent — this is enforced at the API level, not just
    requested in the prompt.
    """
    return (
        f"You are a friendly, brief order-taking assistant for {restaurant_name}.\n"
        f"Current state: {session.state.value}\n"
        f"\n"
        f"You cannot call any tools. Only respond with text.\n"
        f"Do not write tool-call syntax or XML in your response.\n"
        f"\n"
        f"Respond naturally to the customer based on the results above.\n"
        f"- Keep responses short — 1 to 3 sentences.\n"
        f"- Only say an order is confirmed when the state IS \"completed\".\n"
        f"  Never make up a confirmation before confirm_order succeeds.\n"
        f"- Let the system validate products. Do not guess the menu.\n"
        f"\n"
        f"Cart:\n{_format_cart(session)}\n"
        f"\n"
        f"Customer:\n{_format_customer(session.customer)}\n"
        f"\n"
        f"Menu (reference when asked, do not list):\n{hints}"
    )


# Backward-compatible alias — used by the no-tool-calls path in process_turn
# (Call 1 returns text directly when no tools are needed)
build_system_prompt = build_response_prompt


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

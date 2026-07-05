"""Domain abstraction — pluggable bot personalities.

A Domain defines what tools are available, what system prompt to use, and
what the initial conversation state is. The default (and currently only)
domain is 'order' — the restaurant order-processing bot.

To add a new domain (e.g. 'support', 'faq'), define a new DomainConfig
and register it in DOMAINS. The restaurant config can then set
``domain: support`` to use it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from models import OrderState


@dataclass(frozen=True)
class DomainConfig:
    """Configuration for one bot domain (personality + capabilities).

    Attributes:
        name: Short identifier (e.g. 'order', 'support').
        tools_by_state: Mapping of state → list of @tool-decorated functions.
            The state is a string (not OrderState) so domains can define
            their own state machines.
        initial_state: The state new sessions start in.
        system_prompt_template: Template string with {restaurant_name},
            {state}, {cart_summary}, {hints}, {customer_info} placeholders.
        requires_catalogue: Whether this domain needs a menu/catalogue.
            When False, catalogue and pricing are optional in process_turn.
    """

    name: str
    tools_by_state: dict[str, list[Callable]] = field(default_factory=dict)
    initial_state: str = "building"
    system_prompt_template: str = ""
    requires_catalogue: bool = True


# ---------------------------------------------------------------------------
# Order domain (default)
# ---------------------------------------------------------------------------

_ORDER_PROMPT = """You are an AI order-taking assistant for {restaurant_name}.

You help customers place orders for pickup or delivery. Be friendly,
efficient, and helpful. Always confirm details before finalizing.

## Current order state: {state}

{cart_summary}

## Customer info
{customer_info}

## Menu hints (use add_to_cart — don't guess names)
{hints}

## Rules
- Use the tools provided to manage the customer's order.
- Never guess menu item names. If a customer asks for something
  not in the menu hints, ask them to clarify.
- Match the customer's language — respond in whatever language they use.
- Only say an order is confirmed when the state IS 'completed'.
- After confirmation, thank the customer warmly."""


def _make_order_domain() -> DomainConfig:
    """Build the order domain — imported lazily to avoid circular imports."""
    from tools import TOOLS_BY_STATE

    # Convert OrderState keys to strings
    tools_by_state: dict[str, list[Callable]] = {}
    for state, tool_list in TOOLS_BY_STATE.items():
        tools_by_state[state.value] = list(tool_list)

    return DomainConfig(
        name="order",
        tools_by_state=tools_by_state,
        initial_state=OrderState.BUILDING.value,
        system_prompt_template=_ORDER_PROMPT,
        requires_catalogue=True,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DOMAINS: dict[str, DomainConfig] = {}


def _register_builtins() -> None:
    """Register built-in domains. Called once at module load."""
    DOMAINS["order"] = _make_order_domain()


def get_domain(name: str = "order") -> DomainConfig:
    """Return a domain by name. Falls back to 'order' if not found."""
    return DOMAINS.get(name, DOMAINS.get("order", _make_order_domain()))


_register_builtins()

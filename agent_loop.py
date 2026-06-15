"""Agent loop — orchestrates each turn of the conversation.

Ties together session, catalogue, pricing, tools, prompts, and the LLM client.
This is the only file that understands the full control flow.
"""

from __future__ import annotations

import logging

from catalogue import Catalogue
from llm_client import LLMClient, build_tool_definitions, messages_to_openai
from models import Message, MessageRole, OrderState, OrderType, ToolCallRequest
from pricing import PricingEngine
from prompts import build_system_prompt
from session import OrderSession
from tools import TOOLS_BY_STATE

logger = logging.getLogger(__name__)


def process_turn(
    session: OrderSession,
    user_message: str,
    catalogue: Catalogue,
    pricing: PricingEngine,
    llm_client: LLMClient,
) -> str:
    """Process one user message through the agent loop.

    Args:
        session: Current order session (mutated in place).
        user_message: The user's latest input.
        catalogue: Product catalogue.
        pricing: Pricing engine.
        llm_client: LLM API client.

    Returns:
        The assistant text response to display to the user.
    """
    # 1. Append user message
    session.add_user_message(user_message)
    logger.debug("Processing: %s", user_message[:200])

    # 2. Build prompt + tool definitions
    prompt = build_system_prompt(
        session,
        restaurant_name=catalogue.restaurant_name,
        hints=catalogue.get_hints(),
    )
    tool_funcs = TOOLS_BY_STATE.get(session.state, [])
    tool_defs = build_tool_definitions(tool_funcs) if tool_funcs else None

    # 3. Call LLM
    messages = messages_to_openai(session.conversation)
    full_msgs = [{"role": "system", "content": prompt}] + messages
    text, tool_calls = llm_client.chat(full_msgs, tool_defs)

    # 4. Record assistant response
    session.add_assistant_message(content=text, tool_calls=tool_calls)

    # 5. Execute tool calls sequentially
    if tool_calls:
        for tc in tool_calls[:5]:  # safety limit
            _execute_tool(session, catalogue, pricing, tc, tool_funcs)

    # 6. Evaluate and apply state transition
    _apply_transition(session)

    # 7. Save session
    session.save()

    # 8. Return assistant text
    return text


# =============================================================================
# Tool Execution
# =============================================================================


def _execute_tool(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
    tc: ToolCallRequest,
    tool_funcs: list,
) -> None:
    """Execute a single tool call and append the result to the conversation."""
    tool_fn = _lookup_tool(tc.name, tool_funcs)

    if tool_fn is None:
        result = '{"success": false, "error": "Unknown tool"}'
    else:
        try:
            result_obj = tool_fn(session, catalogue, pricing, **tc.arguments)
            result = result_obj.model_dump_json()
        except Exception as e:
            logger.error("Tool %s failed: %s", tc.name, e, exc_info=True)
            result = f'{{"success": false, "error": "{str(e)}"}}'

    session.add_tool_result(
        tool_call_id=tc.id,
        tool_name=tc.name,
        result_json=result,
    )


def _lookup_tool(name: str, tool_funcs: list):
    """Find a tool function by name from the available tools list."""
    for f in tool_funcs:
        if getattr(f, "__tool_name__", None) == name:
            return f
    return None


# =============================================================================
# State Transitions
# =============================================================================


def _apply_transition(session: OrderSession) -> None:
    """Evaluate and apply any pending state transition.

    Called after all tool calls have been processed for the turn.
    Cancel always wins. Other transitions require prevalidation.
    """
    target = session._pending_transition
    if target is None:
        return

    # Cancel always wins — no preconditions
    if target == OrderState.CANCELLED:
        session.state = OrderState.CANCELLED
        session._pending_transition = None
        logger.info("Order cancelled: %s", session.session_id)
        return

    # REVIEW transition — check preconditions
    if target == OrderState.REVIEW:
        issues: list[str] = []
        if not session.cart:
            issues.append("Cart is empty — add at least one item.")
        if not session.customer.name:
            issues.append("Name is required before checkout.")
        if not session.customer.phone:
            issues.append("Phone number is required before checkout.")
        if (
            session.customer.order_type == OrderType.DELIVERY
            and not session.customer.address
        ):
            issues.append("Delivery address is required for delivery orders.")

        if issues:
            session._pending_transition = None
            logger.info("REVIEW blocked: %s", issues)
            return

        session.state = OrderState.REVIEW
        session._pending_transition = None
        logger.info("State transition: BUILDING → REVIEW")
        return

    # PAYMENT_PENDING transition
    if target == OrderState.PAYMENT_PENDING:
        session.state = OrderState.PAYMENT_PENDING
        session._pending_transition = None
        logger.info("State transition: → PAYMENT_PENDING")
        return

    # Fallback — clear unknown transitions
    session._pending_transition = None

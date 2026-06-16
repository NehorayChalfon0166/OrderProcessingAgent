"""Agent loop — orchestrates each turn of the conversation.

Ties together session, catalogue, pricing, tools, prompts, and the LLM client.
This is the only file that understands the full control flow.

Uses a two-call pattern:
  Call 1: LLM with tools → tool calls executed silently
  Call 2: LLM without tools → natural response informed by tool results
"""

from __future__ import annotations

import logging

from catalogue import Catalogue
from llm_client import LLMClient, build_tool_definitions, messages_to_openai
from models import OrderState, OrderType, ToolCallRequest
from pricing import PricingEngine
from prompts import build_response_prompt, build_tool_prompt
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

    May make two LLM calls: one to execute tools, one to respond.

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

    # 2. Build Call 1 prompt (tool-only) + tool definitions
    prompt = build_tool_prompt(
        session,
        restaurant_name=catalogue.restaurant_name,
        hints=catalogue.get_hints(),
    )
    tool_funcs = TOOLS_BY_STATE.get(session.state, [])
    tool_defs = build_tool_definitions(tool_funcs) if tool_funcs else None

    # 3. CALL 1 — LLM with tools
    messages = messages_to_openai(session.conversation)
    full_msgs = [{"role": "system", "content": prompt}] + messages
    _text_1, tool_calls = llm_client.chat(full_msgs, tool_defs)

    if not tool_calls:
        # No tools called — record and return the text directly
        session.add_assistant_message(content=_text_1)
        session.save()
        return _text_1

    # 4. Tool calls present — execute them silently, then respond

    # 4a. Record assistant message (tool calls, no text — user won't see this)
    session.add_assistant_message(content=None, tool_calls=tool_calls)

    # 4b. Execute tool calls sequentially (max 5)
    for tc in tool_calls[:5]:
        _execute_tool(session, catalogue, pricing, tc, tool_funcs)

    # 4c. Apply state transitions (cart/state may have changed)
    _apply_transition(session)

    # 4d. CALL 2 — LLM WITHOUT tools, sees tool results, responds naturally
    prompt_2 = build_response_prompt(
        session,
        restaurant_name=catalogue.restaurant_name,
        hints=catalogue.get_hints(),
    )
    messages_2 = messages_to_openai(session.conversation)
    full_msgs_2 = [{"role": "system", "content": prompt_2}] + messages_2
    text_2, _ = llm_client.chat(full_msgs_2, tools=None)

    # 4e. Record the final text response
    session.add_assistant_message(content=text_2)

    # 5. Save session
    session.save()

    # 6. Return the natural-language response
    return text_2


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

    # PAYMENT_PENDING transition (reserved for future payment processing)
    if target == OrderState.PAYMENT_PENDING:
        session.state = OrderState.PAYMENT_PENDING
        session._pending_transition = None
        logger.info("State transition: → PAYMENT_PENDING")
        return

    # COMPLETED transition
    if target == OrderState.COMPLETED:
        session.state = OrderState.COMPLETED
        session._pending_transition = None
        logger.info("State transition: → COMPLETED")
        return

    # Fallback — clear unknown transitions
    session._pending_transition = None

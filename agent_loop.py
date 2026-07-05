"""Agent loop — orchestrates each turn of the conversation.

Ties together session, catalogue, pricing, tools, prompts, and the LLM client.
This is the only file that understands the full control flow.

Uses a loop pattern:
  LLM with tools → execute → repeat until the LLM responds without tools
  (or max iterations reached). Tools are always available — the model decides
  each iteration whether to call them or respond to the customer.
"""

from __future__ import annotations

import logging

from catalogue import Catalogue
from llm_client import LLMClient, build_tool_definitions, messages_to_openai
from models import OrderState, OrderType, ToolCallRequest
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
    max_iterations: int = 5,
) -> str:
    """Process one user message through the agent loop.

    Loops: LLM with tools → execute → repeat until the LLM responds
    without tool calls (or max iterations reached).

    Session must have a Database attached (session._db) — persistence
    is always via SQLite.

    Args:
        session: Current order session (mutated in place).
        user_message: The user's latest input.
        catalogue: Product catalogue.
        pricing: Pricing engine.
        llm_client: LLM API client.
        max_iterations: Safety cap for tool-calling loop iterations.

    Returns:
        The assistant text response to display to the user.
    """
    # 1. Append user message
    session.add_user_message(user_message)
    logger.debug("Processing: %s", user_message[:200])

    # 2. Loop: LLM → tools → repeat until clean text or max iterations
    for iteration in range(max_iterations):
        # Refresh tool availability each iteration (state may change)
        tool_funcs = TOOLS_BY_STATE.get(session.state, [])
        tool_defs = build_tool_definitions(tool_funcs) if tool_funcs else None

        prompt = build_system_prompt(
            session,
            restaurant_name=catalogue.restaurant_name,
            hints=catalogue.get_hints(),
        )

        messages = messages_to_openai(session.conversation)
        full_msgs = [{"role": "system", "content": prompt}] + messages

        text, tool_calls = llm_client.chat(full_msgs, tool_defs)

        if not tool_calls:
            # Model is responding to the customer — we're done
            content = text or "I've processed your request. Is there anything else?"
            session.add_assistant_message(content=content)
            session.save()
            return content

        # Tool calls present — snapshot, execute, then loop
        logger.debug(
            "Iteration %d: %d tool calls", iteration + 1, len(tool_calls)
        )

        # Snapshot before modifying session — enables rollback if tool batch crashes
        session.save()

        session.add_assistant_message(content=None, tool_calls=tool_calls)

        try:
            for tc in tool_calls[:5]:
                _execute_tool(session, catalogue, pricing, tc, tool_funcs)

            _apply_transition(session)
        except Exception:
            logger.exception(
                "Tool batch failed for session %s — rolling back",
                session.session_id,
            )
            _restore_session_from_snapshot(session)
            continue

    # 3. Fallback — max iterations exhausted (should be extremely rare)
    logger.warning(
        "Max iterations (%d) exhausted for session %s",
        max_iterations, session.session_id,
    )
    fallback = "I've processed your request. Is there anything else?"
    session.add_assistant_message(content=fallback)
    session.save()
    return fallback


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


# =============================================================================
# Session Restore (for tool atomicity rollback)
# =============================================================================


def _restore_session_from_snapshot(session: OrderSession) -> None:
    """Reload session state from the last save point and copy it in-place.

    Used when a tool batch crashes after the snapshot was saved but before
    all tools completed. The snapshot is taken before the assistant message
    with tool calls, so restoring discards that message — the LLM retries
    cleanly on the next iteration.
    """
    if session._db is None:
        logger.error(
            "Cannot roll back session %s/%s — no Database attached",
            session.restaurant_id, session.session_id,
        )
        return

    restored = session._db.load_session(
        session.restaurant_id, session.session_id,
    )

    if restored is None:
        logger.error(
            "Failed to load snapshot for session %s/%s — rollback skipped",
            session.restaurant_id, session.session_id,
        )
        return

    session.cart = restored.cart
    session.customer = restored.customer
    session.conversation = restored.conversation
    session.state = restored.state
    session._pending_transition = None
    session.updated_at = restored.updated_at
    logger.info("Session %s/%s rolled back to snapshot", session.restaurant_id, session.session_id)

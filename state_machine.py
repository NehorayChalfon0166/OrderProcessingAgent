"""Core state machine for order processing.

Manages a single order conversation session through the phases:
    GREETING → ASSEMBLY → DETAILS → VERIFICATION → CONFIRMED / CANCELLED

Key responsibilities:
    - Maintain conversation history for the LLM context window.
    - Track current state and enforce valid transitions.
    - Hold the running order (items + customer info).
    - Validate all LLM outputs before applying them to state.
    - Generate pricing summaries at verification time.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from llm_client import LLMClient
from menu_manager import MenuManager
from models import (
    CustomerInfo,
    ExtractedCustomerInfo,
    LLMResponse,
    OrderItem,
    OrderState,
    OrderSummary,
    OrderType,
)
from pricing import PricingEngine
from prompts import build_system_prompt

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Transition rules
# ------------------------------------------------------------------

# Each key maps to the set of states reachable from it.
VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.GREETING: {OrderState.ASSEMBLY},
    OrderState.ASSEMBLY: {OrderState.DETAILS, OrderState.CANCELLED},
    OrderState.DETAILS: {
        OrderState.VERIFICATION,
        OrderState.ASSEMBLY,
        OrderState.CANCELLED,
    },
    OrderState.VERIFICATION: {
        OrderState.CONFIRMED,
        OrderState.ASSEMBLY,
        OrderState.CANCELLED,
    },
}

# Map LLM action strings → target ``OrderState``.
ACTION_TO_STATE: dict[str, OrderState] = {
    "move_to_details": OrderState.DETAILS,
    "move_to_verification": OrderState.VERIFICATION,
    "confirm_order": OrderState.CONFIRMED,
    "cancel_order": OrderState.CANCELLED,
    "back_to_assembly": OrderState.ASSEMBLY,
}


class OrderSession:
    """Manages a single order conversation session.

    This is the core state machine. It:
        1. Maintains conversation history.
        2. Tracks the current state.
        3. Holds the running order (items + customer info).
        4. Validates all LLM outputs before applying them.
        5. Owns all state transitions.

    Usage::

        session = OrderSession(menu_mgr, pricing_eng, llm)
        greeting = session.start()          # Initial greeting
        reply = session.process_message("I want a large pepperoni pizza")
        ...
        if session.is_complete:
            payload = session.build_final_payload()
    """

    def __init__(
        self,
        menu_manager: MenuManager,
        pricing_engine: PricingEngine,
        llm_client: LLMClient,
    ) -> None:
        # Public state
        self.state: OrderState = OrderState.GREETING
        self.items: list[OrderItem] = []
        self.customer: CustomerInfo = CustomerInfo()
        self.conversation_history: list[dict[str, str]] = []
        self.order_id: str = str(uuid.uuid4())[:8].upper()

        # Private dependencies
        self._menu = menu_manager
        self._pricing = pricing_engine
        self._llm = llm_client
        self._restaurant_name: str = menu_manager.menu_data.get(
            "restaurant_name", "Restaurant"
        )
        self._menu_text: str = menu_manager.format_menu_for_prompt()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> str:
        """Generate the initial greeting. Called once at session start.

        The method sends a synthetic ``"Hi"`` user message so the LLM
        produces a natural greeting, then immediately transitions to the
        ASSEMBLY state so the next real user message is handled there.

        Returns:
            The greeting text to display to the customer.
        """
        system_prompt = build_system_prompt(
            state=OrderState.GREETING.value,
            menu_text=self._menu_text,
            current_order_text="",
            restaurant_name=self._restaurant_name,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Hi"},
        ]

        llm_response = self._llm.chat_structured(messages)
        greeting = llm_response.response_text

        # Transition directly to assembly — the greeting phase is a
        # single-shot step; the customer hasn't placed an order yet.
        self.state = OrderState.ASSEMBLY
        logger.info("Session %s started – moved to ASSEMBLY", self.order_id)

        self.conversation_history.append({"role": "assistant", "content": greeting})
        return greeting

    def process_message(self, user_message: str) -> str:
        """Process a user message and return the agent's response.

        This is the main entry point for each conversation turn. It:
            1. Builds the state-aware system prompt.
            2. Calls the LLM.
            3. Validates and applies the LLM's suggestions.
            4. Returns the response text to show the user.

        Args:
            user_message: The customer's latest message.

        Returns:
            The agent's response string (may include appended pricing or
            validation warnings).
        """
        # Record user message
        self.conversation_history.append({"role": "user", "content": user_message})

        # Build state-aware system prompt
        system_prompt = build_system_prompt(
            state=self.state.value,
            menu_text=self._menu_text,
            current_order_text=self._format_current_order(),
            restaurant_name=self._restaurant_name,
        )

        # Assemble full message list: system + conversation history
        messages = [
            {"role": "system", "content": system_prompt},
            *self.conversation_history,
        ]

        # Call LLM
        llm_response = self._llm.chat_structured(messages)

        # Validate & apply the LLM's output
        response_text = self._apply_response(llm_response)

        # Record assistant response
        self.conversation_history.append(
            {"role": "assistant", "content": response_text}
        )
        return response_text

    def build_final_payload(self) -> OrderSummary:
        """Build the final validated ``OrderSummary`` payload.

        Should only be called after ``is_complete`` is True.

        Returns:
            A fully populated ``OrderSummary`` ready for downstream
            processing (kitchen display, payment, etc.).
        """
        order_type = self.customer.order_type or OrderType.PICKUP
        subtotal, delivery_fee, total = self._pricing.compute_totals(
            self.items, order_type
        )

        return OrderSummary(
            order_id=self.order_id,
            restaurant=self._restaurant_name,
            items=self.items,
            customer=self.customer,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            total=total,
            order_type=order_type,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
        )

    @property
    def is_complete(self) -> bool:
        """True when the order has been confirmed by the customer."""
        return self.state == OrderState.CONFIRMED

    @property
    def is_cancelled(self) -> bool:
        """True when the customer has cancelled the order."""
        return self.state == OrderState.CANCELLED

    # ------------------------------------------------------------------
    # Response application
    # ------------------------------------------------------------------

    def _apply_response(self, llm_response: LLMResponse) -> str:
        """Validate and apply the LLM's structured response.

        Handles item addition/removal, customer info extraction, state
        transitions, and pricing summary generation.

        Args:
            llm_response: The parsed ``LLMResponse`` from the LLM.

        Returns:
            The final response text to show the customer (may include
            appended pricing summary and/or validation warnings).
        """
        response_text = llm_response.response_text
        issues: list[str] = []

        # --- Handle item addition (ASSEMBLY) ---
        if llm_response.action != "modify_item" and llm_response.extracted_items:
            for extracted in llm_response.extracted_items:
                validated, item_issues = self._menu.validate_extracted_item(extracted)
                if validated:
                    priced = self._pricing.price_item(validated)
                    self.items.append(priced)
                    logger.info(
                        "Added item: %sx %s (%s)",
                        priced.quantity,
                        priced.item_name,
                        priced.size or "default",
                    )
                else:
                    issues.extend(item_issues)

        # --- Handle item removal ---
        if llm_response.removed_items:
            for name in llm_response.removed_items:
                name_lower = name.strip().lower()
                before_count = len(self.items)
                self.items = [
                    item
                    for item in self.items
                    if item.item_name.lower() != name_lower
                ]
                removed_count = before_count - len(self.items)
                if removed_count > 0:
                    logger.info("Removed %d item(s) matching '%s'", removed_count, name)
                else:
                    issues.append(f"Item '{name}' not found in your current order")

        # --- Handle item modification ---
        if llm_response.action == "modify_item" and llm_response.extracted_items:
            for extracted in llm_response.extracted_items:
                name_lower = extracted.name.strip().lower()
                # Remove the old version of the item
                self.items = [
                    item
                    for item in self.items
                    if item.item_name.lower() != name_lower
                ]
                # Add the updated version
                validated, item_issues = self._menu.validate_extracted_item(extracted)
                if validated:
                    priced = self._pricing.price_item(validated)
                    self.items.append(priced)
                    logger.info("Modified item: %s", priced.item_name)
                else:
                    issues.extend(item_issues)

        # --- Handle customer info extraction (DETAILS) ---
        if llm_response.customer_info:
            self._merge_customer_info(llm_response.customer_info)

        # --- Handle state transitions ---
        target_state = ACTION_TO_STATE.get(llm_response.action)
        if target_state:
            transition_issues = self._validate_transition(
                target_state,
                order_complete=llm_response.order_complete,
            )
            if transition_issues:
                issues.extend(transition_issues)
            else:
                old_state = self.state
                self.state = target_state
                logger.info(
                    "State transition: %s → %s", old_state.value, target_state.value
                )

        # --- Append pricing summary in verification state ---
        if self.state == OrderState.VERIFICATION:
            pricing_summary = self._format_pricing_summary()
            response_text = f"{response_text}\n\n{pricing_summary}"

        # --- Append validation issues if any ---
        if issues:
            issues_text = "\n".join(f"⚠️ {issue}" for issue in issues)
            response_text = f"{response_text}\n\n{issues_text}"

        return response_text

    # ------------------------------------------------------------------
    # Customer info merging
    # ------------------------------------------------------------------

    def _merge_customer_info(self, info: ExtractedCustomerInfo) -> None:
        """Merge partially-collected customer info into session state.

        Supports the user providing info across multiple messages — each
        field is only overwritten when a non-empty value is provided.
        """
        if info.name:
            self.customer.name = info.name
        if info.phone:
            self.customer.phone = info.phone
        if info.address:
            self.customer.address = info.address
        if info.order_type:
            # Normalise to OrderType enum, ignoring invalid values
            try:
                self.customer.order_type = OrderType(info.order_type.lower())
            except ValueError:
                logger.warning("Ignoring invalid order_type: %s", info.order_type)

    # ------------------------------------------------------------------
    # Transition validation
    # ------------------------------------------------------------------

    def _validate_transition(
        self,
        target: OrderState,
        *,
        order_complete: bool = False,
    ) -> list[str]:
        """Check whether a state transition is allowed.

        Validates both the transition graph and any preconditions (e.g.
        items in cart before checkout, customer info before verification).

        Args:
            target: The desired next state.
            order_complete: Whether the LLM flagged the order as
                explicitly confirmed by the customer.

        Returns:
            A list of human-readable issue strings. Empty means the
            transition is allowed.
        """
        issues: list[str] = []

        # Check the transition graph
        valid_targets = VALID_TRANSITIONS.get(self.state, set())
        if target not in valid_targets:
            issues.append(
                f"Cannot move from '{self.state.value}' to '{target.value}' right now"
            )
            return issues  # Early return — no point checking preconditions

        # ------ Precondition checks per target state ------

        if target == OrderState.DETAILS:
            if not self.items:
                issues.append(
                    "You haven't added any items yet. "
                    "Please add at least one item before checking out."
                )

        elif target == OrderState.VERIFICATION:
            if not self.customer.name:
                issues.append("We still need your name before we can review the order.")
            if not self.customer.phone:
                issues.append(
                    "We still need your phone number before we can review the order."
                )
            if (
                self.customer.order_type == OrderType.DELIVERY
                and not self.customer.address
            ):
                issues.append(
                    "A delivery address is required for delivery orders."
                )

        elif target == OrderState.CONFIRMED:
            if not order_complete:
                issues.append(
                    "The order hasn't been explicitly confirmed by the customer yet."
                )

        return issues

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_current_order(self) -> str:
        """Format current order items as text for prompt injection.

        This text is included in the system prompt so the LLM knows
        what the customer has ordered so far.
        """
        if not self.items:
            return "(No items yet)"

        lines: list[str] = []
        for i, item in enumerate(self.items, 1):
            line = f"{i}. {item.quantity}x {item.item_name}"
            if item.size:
                line += f" ({item.size})"
            if item.toppings:
                topping_names = [t.topping_name for t in item.toppings]
                line += f" + {', '.join(topping_names)}"
            if item.special_instructions:
                line += f" [{item.special_instructions}]"
            lines.append(line)

        # Add running subtotal
        order_type = self.customer.order_type or OrderType.PICKUP
        subtotal, _, _ = self._pricing.compute_totals(self.items, order_type)
        lines.append(f"\nCurrent Running Subtotal: ${subtotal:.2f}")

        # Append customer info if partially collected
        if self.customer.name or self.customer.phone:
            lines.append("\nCustomer Info:")
            if self.customer.name:
                lines.append(f"  Name: {self.customer.name}")
            if self.customer.phone:
                lines.append(f"  Phone: {self.customer.phone}")
            if self.customer.address:
                lines.append(f"  Address: {self.customer.address}")
            if self.customer.order_type:
                lines.append(f"  Type: {self.customer.order_type.value}")

        return "\n".join(lines)

    def _format_pricing_summary(self) -> str:
        """Format a pricing summary for the verification state.

        This is appended to the LLM's response text so the customer
        sees accurate, system-computed prices (the LLM is never trusted
        with arithmetic).
        """
        order_type = self.customer.order_type or OrderType.PICKUP
        subtotal, delivery_fee, total = self._pricing.compute_totals(
            self.items, order_type
        )

        lines = ["--- Order Summary ---"]
        for item in self.items:
            price_str = f"${item.line_total:.2f}"
            line = f"  {item.quantity}x {item.item_name}"
            if item.size:
                line += f" ({item.size})"
            if item.toppings:
                topping_names = [t.topping_name for t in item.toppings]
                line += f" + {', '.join(topping_names)}"
            line += f" — {price_str}"
            lines.append(line)

        lines.append(f"\n  Subtotal: ${subtotal:.2f}")
        if delivery_fee > 0:
            lines.append(f"  Delivery Fee: ${delivery_fee:.2f}")
        lines.append(f"  Total: ${total:.2f}")

        return "\n".join(lines)

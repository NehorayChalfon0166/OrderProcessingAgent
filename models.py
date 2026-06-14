"""
models.py — Domain models for the pizzeria order processing agent.

All data structures used throughout the application are defined here.
Uses Pydantic v2 for validation and serialization.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums
# =============================================================================


class OrderState(str, Enum):
    """
    Finite states of the order conversation flow.

    The state machine moves through these states sequentially,
    with some allowed backward transitions (e.g. VERIFICATION -> ASSEMBLY).
    """

    GREETING = "greeting"
    ASSEMBLY = "assembly"
    DETAILS = "details"
    VERIFICATION = "verification"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class OrderType(str, Enum):
    """Whether the customer wants delivery or pickup."""

    DELIVERY = "delivery"
    PICKUP = "pickup"


class LLMAction(str, Enum):
    """
    Actions the LLM can suggest each conversational turn.

    The LLM proposes an action, but Python validates it against the current
    state before executing. This keeps the LLM advisory-only — it never
    directly mutates state.
    """

    CONTINUE = "continue"  # Stay in current state, no data changes
    ADD_ITEMS = "add_items"  # Items extracted, add to order
    REMOVE_ITEM = "remove_item"  # User wants to remove something
    MODIFY_ITEM = "modify_item"  # User wants to change an item (size, toppings, etc.)
    MOVE_TO_DETAILS = "move_to_details"  # Order assembly done, collect customer info
    MOVE_TO_VERIFICATION = "move_to_verification"  # Details collected, show summary
    CONFIRM_ORDER = "confirm_order"  # User confirmed the final summary
    CANCEL_ORDER = "cancel_order"  # User wants to cancel the entire order
    BACK_TO_ASSEMBLY = "back_to_assembly"  # User wants to modify order after summary


# =============================================================================
# Menu-related models (for internal use, loaded from JSON)
# =============================================================================


class ToppingSelection(BaseModel):
    """A single topping selected for a pizza, with its price resolved from the menu."""

    topping_id: str = Field(description="Topping identifier matching menu.json")
    topping_name: str = Field(description="Human-readable topping name")
    price: float = Field(ge=0, description="Price of this topping from the menu")


# =============================================================================
# Order Items
# =============================================================================


class OrderItem(BaseModel):
    """
    A single line item in the order.

    Prices are ALWAYS computed by the PricingEngine — never by the LLM.
    The LLM extracts item names and preferences; Python resolves them to
    validated menu items with correct prices.
    """

    item_id: str = Field(description="Menu item ID (e.g. 'pizza_margherita')")
    item_name: str = Field(description="Human-readable item name")
    category: str = Field(description="Menu category (e.g. 'Pizzas', 'Sides')")
    size: Optional[str] = Field(
        default=None, description="Size selection, if applicable (e.g. 'small', 'medium', 'large')"
    )
    quantity: int = Field(default=1, ge=1, description="Number of this item")
    base_price: float = Field(
        default=0.0, ge=0, description="Unit price before toppings, set by PricingEngine"
    )
    toppings: list[ToppingSelection] = Field(
        default_factory=list, description="Extra toppings added to this item"
    )
    special_instructions: Optional[str] = Field(
        default=None, description="Free-text special requests (e.g. 'well done', 'cut in squares')"
    )
    line_total: float = Field(
        default=0.0, ge=0, description="(base_price + topping_prices) * quantity, set by PricingEngine"
    )


# =============================================================================
# Customer Info
# =============================================================================


class CustomerInfo(BaseModel):
    """Customer details collected during the 'details' state."""

    name: Optional[str] = Field(default=None, description="Customer's name")
    phone: Optional[str] = Field(default=None, description="Contact phone number")
    address: Optional[str] = Field(
        default=None, description="Delivery address (required for delivery orders)"
    )
    order_type: Optional[OrderType] = Field(
        default=None, description="Delivery or pickup"
    )


# =============================================================================
# LLM Response Schema (what the LLM returns every turn)
# =============================================================================


class ExtractedItem(BaseModel):
    """
    Raw item extraction from the LLM's interpretation of user input.

    These are NOT validated yet — they need to be checked against the menu
    by MenuManager.validate_extracted_item() before becoming OrderItems.
    """

    name: str = Field(description="Item name as understood from user input")
    size: Optional[str] = Field(
        default=None, description="Size if mentioned (e.g. 'large', 'small')"
    )
    quantity: int = Field(default=1, ge=1, description="How many of this item")
    toppings: list[str] = Field(
        default_factory=list,
        description="Topping names mentioned by the user (raw strings)",
    )
    special_instructions: Optional[str] = Field(
        default=None, description="Any special requests for this item"
    )


class ExtractedCustomerInfo(BaseModel):
    """
    Raw customer info extraction from the LLM.

    Fields are all Optional because the user may provide info incrementally
    across multiple turns.
    """

    name: Optional[str] = Field(default=None, description="Customer name if mentioned")
    phone: Optional[str] = Field(default=None, description="Phone number if mentioned")
    address: Optional[str] = Field(default=None, description="Address if mentioned")
    order_type: Optional[str] = Field(
        default=None, description="'delivery' or 'pickup' if mentioned"
    )


class LLMResponse(BaseModel):
    """
    Unified response schema the LLM returns every conversational turn.

    The LLM fills this out as structured output. Python then validates the
    action and extracted data before applying any changes to the order state.
    """

    response_text: str = Field(
        description="The natural-language message to display to the customer"
    )
    action: str = Field(
        default="continue",
        description="Suggested action for the state machine (must be a valid LLMAction value)",
    )
    extracted_items: list[ExtractedItem] = Field(
        default_factory=list,
        description="Items the LLM extracted from the user's message",
    )
    removed_items: list[str] = Field(
        default_factory=list,
        description="Item names the user wants to remove from the order",
    )
    modified_items: list[ExtractedItem] = Field(
        default_factory=list,
        description="Items the user wants to modify (with updated fields)",
    )
    customer_info: Optional[ExtractedCustomerInfo] = Field(
        default=None,
        description="Customer details extracted from this turn's message",
    )
    order_complete: bool = Field(
        default=False,
        description="True when user explicitly confirms the final order summary",
    )

    @field_validator("extracted_items", "removed_items", "modified_items", mode="before")
    @classmethod
    def default_none_to_empty_list(cls, v: list | None) -> list:
        return [] if v is None else v


# =============================================================================
# Final Order Payload
# =============================================================================


class OrderSummary(BaseModel):
    """
    The final validated order that gets persisted to disk.

    This is the complete, priced, validated order ready for fulfillment.
    All prices have been computed by PricingEngine and verified.
    """

    order_id: str = Field(description="Unique order identifier (e.g. 'ORD-20260614-001')")
    restaurant: str = Field(description="Restaurant name from menu.json")
    items: list[OrderItem] = Field(description="All validated and priced line items")
    customer: CustomerInfo = Field(description="Validated customer information")
    subtotal: float = Field(ge=0, description="Sum of all line_totals before fees")
    delivery_fee: float = Field(ge=0, description="Delivery fee (0 for pickup)")
    total: float = Field(ge=0, description="Final total: subtotal + delivery_fee")
    order_type: OrderType = Field(description="Delivery or pickup")
    estimated_time: str = Field(
        default="", description="Estimated delivery/pickup time"
    )
    timestamp: str = Field(description="ISO 8601 timestamp of order confirmation")
    notes: Optional[str] = Field(
        default=None, description="General order notes, if any"
    )

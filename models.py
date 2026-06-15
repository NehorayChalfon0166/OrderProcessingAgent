"""Domain models for the order processing agent.

All data structures used throughout the application. Pydantic v2 for validation
and serialization. Prices are ALWAYS computed by the PricingEngine — never by
the LLM or set manually here.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class OrderState(str, Enum):
    """States of the order conversation flow."""

    BUILDING = "building"
    REVIEW = "review"
    PAYMENT_PENDING = "payment_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OrderType(str, Enum):
    """Whether the customer wants delivery or pickup."""

    DELIVERY = "delivery"
    PICKUP = "pickup"


class MessageRole(str, Enum):
    """Roles in the conversation message list."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# =============================================================================
# Cart Models
# =============================================================================


class CartTopping(BaseModel):
    """A single topping selected for a pizza, with its price resolved from the menu."""

    topping_id: str = Field(description="Topping identifier matching menu.json")
    name: str = Field(description="Human-readable topping name")
    price: float = Field(ge=0, description="Price of this topping from the menu")


class CartItem(BaseModel):
    """
    A single line item in the order.

    Prices are ALWAYS computed by the PricingEngine — never by the LLM.
    The LLM provides intent via tool calls; Python resolves to validated
    menu items with correct prices.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable UUID for remove/update reference",
    )
    product_id: str = Field(description="Menu product ID (e.g. 'pizza_margherita')")
    name: str = Field(description="Human-readable item name")
    category: str = Field(description="Menu category (e.g. 'Pizzas', 'Sides')")
    quantity: int = Field(default=1, ge=1, description="Number of this item")
    size: Optional[str] = Field(
        default=None,
        description="Size selection, if applicable (e.g. 'small', 'medium', 'large')",
    )
    toppings: list[CartTopping] = Field(
        default_factory=list, description="Extra toppings added to this item"
    )
    options: dict[str, str] = Field(
        default_factory=dict,
        description="Item-specific add-ons (e.g. {'sauce': 'marinara'})",
    )
    special_instructions: Optional[str] = Field(
        default=None,
        description="Free-text special requests (e.g. 'well done', 'cut in squares')",
    )
    base_price: float = Field(
        default=0.0, ge=0, description="Unit price before toppings, set by PricingEngine"
    )
    line_total: float = Field(
        default=0.0,
        ge=0,
        description="(base_price + topping_prices) * quantity, set by PricingEngine",
    )
    missing_options: list[str] = Field(
        default_factory=list,
        description="Required options not yet provided by the user",
    )


# =============================================================================
# Customer Info
# =============================================================================


class CustomerInfo(BaseModel):
    """Customer details collected during order building.

    Merge semantics: each set_customer_info call fills whatever fields are
    provided, overwriting previous values. Never clears fields that aren't
    explicitly set.
    """

    name: Optional[str] = Field(default=None, description="Customer's name")
    phone: Optional[str] = Field(default=None, description="Contact phone number")
    address: Optional[str] = Field(
        default=None, description="Delivery address (required for delivery orders)"
    )
    order_type: Optional[OrderType] = Field(
        default=None, description="Delivery or pickup"
    )


# =============================================================================
# Conversation Messages
# =============================================================================


class ToolCallRequest(BaseModel):
    """One tool call the LLM requested in its response."""

    id: str = Field(description="Tool call ID from the API response")
    name: str = Field(description="Tool function name")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Tool function arguments"
    )


class Message(BaseModel):
    """A single message in the conversation history.

    Provider-agnostic — converted to OpenAI format by llm_client.messages_to_openai().
    """

    role: MessageRole = Field(description="Who sent this message")
    content: Optional[str] = Field(
        default=None, description="Text content (None when tool calls present)"
    )
    tool_calls: Optional[list[ToolCallRequest]] = Field(
        default=None, description="Tool calls made by the assistant"
    )
    tool_call_id: Optional[str] = Field(
        default=None, description="Which tool call this result answers (TOOL messages only)"
    )
    name: Optional[str] = Field(
        default=None, description="Tool function name (TOOL messages only)"
    )


# =============================================================================
# Tool Result Types
# =============================================================================


class AddToCartResult(BaseModel):
    """Result from the add_to_cart tool."""

    success: bool = Field(description="Whether the item was added")
    item: Optional[CartItem] = Field(
        default=None, description="The added cart item (if successful)"
    )
    suggestions: list[str] = Field(
        default_factory=list, description="Product name suggestions if not found"
    )
    missing_options: list[str] = Field(
        default_factory=list, description="Required options still needed"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Non-blocking warnings (e.g. invalid size, used default)",
    )


class RemoveFromCartResult(BaseModel):
    """Result from the remove_from_cart tool."""

    success: bool = Field(description="Whether the item was removed")
    removed: list[CartItem] = Field(
        default_factory=list, description="The removed cart items"
    )
    matches: list[CartItem] = Field(
        default_factory=list,
        description="Ambiguous matches — multiple items matched the reference",
    )


class UpdateItemResult(BaseModel):
    """Result from the update_item tool."""

    success: bool = Field(description="Whether the item was updated")
    item: Optional[CartItem] = Field(
        default=None, description="The updated cart item (if successful)"
    )
    matches: list[CartItem] = Field(
        default_factory=list,
        description="Ambiguous matches — multiple items matched the reference",
    )


class ViewCartResult(BaseModel):
    """Result from the view_cart tool."""

    items: list[CartItem] = Field(description="All items in the cart")
    subtotal: float = Field(ge=0, description="Cart subtotal (before delivery fee)")
    item_count: int = Field(ge=0, description="Total number of line items")


class SetCustomerInfoResult(BaseModel):
    """Result from the set_customer_info tool."""

    success: bool = Field(description="Whether the info was applied")
    info: CustomerInfo = Field(description="Current customer info after merge")
    missing_required: list[str] = Field(
        default_factory=list,
        description="Required fields still needed (e.g. ['phone'])",
    )


class RequestReviewResult(BaseModel):
    """Result from the request_review tool."""

    success: bool = Field(description="Whether the review transition is allowed")
    issues: list[str] = Field(
        default_factory=list,
        description="Preconditions not met (e.g. 'Cart is empty', 'Phone required')",
    )


class ConfirmOrderResult(BaseModel):
    """Result from the confirm_order tool."""

    success: bool = Field(description="Whether the order was confirmed")
    order_id: Optional[str] = Field(
        default=None, description="The confirmed order ID"
    )
    total: float = Field(default=0.0, ge=0, description="Final order total")


class CancelOrderResult(BaseModel):
    """Result from the cancel_order tool."""

    success: bool = Field(description="Whether the order was cancelled")
    message: str = Field(description="Confirmation message")

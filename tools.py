"""Tool implementations — pure Python functions that bridge LLM intent to state.

Each tool:
- Takes typed parameters (some injected by the agent loop, some from the LLM)
- Validates against the catalogue
- Mutates session state
- Returns a typed Pydantic result

The @tool decorator stores metadata for LLM function-calling schema generation.
TOOLS_BY_STATE is the single source of truth for which tools are available when.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from functools import wraps
from typing import get_type_hints

logger = logging.getLogger(__name__)

from catalogue import Catalogue
from models import (
    AddToCartResult,
    BrowseMenuResult,
    CancelOrderResult,
    CartItem,
    ConfirmOrderResult,
    OrderState,
    OrderType,
    RemoveFromCartResult,
    RequestReviewResult,
    SetCustomerInfoResult,
    UpdateItemResult,
    ViewCartResult,
)
from pricing import PricingEngine
from session import OrderSession
from utils import generate_order_id


# =============================================================================
# @tool Decorator
# =============================================================================


def tool(description: str):
    """Decorator that stores tool metadata for LLM function-calling schema generation.

    Introspects type hints to build a JSON Schema for parameters. Injected
    parameters (session, catalogue, pricing) are excluded from the schema.
    Parameters with defaults are omitted from 'required'.
    """

    def decorator(fn):
        hints = get_type_hints(fn)
        hints.pop("return", None)

        properties: dict[str, dict] = {}
        required: list[str] = []

        sig = inspect.signature(fn)
        for name, param in sig.parameters.items():
            if name in ("session", "catalogue", "pricing"):
                continue  # injected by agent loop, not LLM-visible

            py_type = hints.get(name, str)
            properties[name] = _type_to_json_schema(py_type)

            if param.default is inspect.Parameter.empty:
                required.append(name)

        schema: dict = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper.__tool_name__ = fn.__name__  # type: ignore[attr-defined]
        wrapper.__tool_description__ = description  # type: ignore[attr-defined]
        wrapper.__tool_schema__ = schema  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _type_to_json_schema(py_type) -> dict:
    """Map a Python type annotation to a JSON Schema type dict."""
    origin = getattr(py_type, "__origin__", None)

    # Handle Optional[T] / T | None (Union types, Python 3.10+)
    args = getattr(py_type, "__args__", ())
    if type(None) in args:
        inner = [a for a in args if a is not type(None)][0]
        schema = _type_to_json_schema(inner)
        schema["nullable"] = True
        return schema

    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    # list[str]
    if origin is list:
        item_type = args[0] if args else str
        return {"type": "array", "items": _type_to_json_schema(item_type)}

    # dict[str, str]
    if origin is dict:
        return {"type": "object", "additionalProperties": {"type": "string"}}

    # Fallback
    return {"type": "string"}


# =============================================================================
# Tools
# =============================================================================


@tool(description="Add an item to the order cart")
def add_to_cart(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
    product_name: str,
    quantity: int = 1,
    size: str | None = None,
    toppings: list[str] | None = None,
    options: dict[str, str] | None = None,
    special_instructions: str | None = None,
) -> AddToCartResult:
    toppings = toppings or []
    options = options or {}
    issues: list[str] = []

    # Coerce numeric args — LLMs may emit them as strings
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        quantity = 1
    quantity = min(max(1, quantity), 100)  # cap at 100

    # 1. Try product (includes deals — they're registered as products)
    product = catalogue.find_product(product_name)
    if product is None:
        suggestions = catalogue.get_product_suggestions(product_name)
        return AddToCartResult(success=False, suggestions=suggestions)

    # 2. Resolve size
    resolved_size, size_issues = catalogue.resolve_size(product, size)
    issues.extend(size_issues)

    # 3. Resolve toppings
    resolved_toppings, topping_issues = catalogue.resolve_toppings(product, toppings)
    issues.extend(topping_issues)

    # 4. Create cart item
    cart_item = CartItem(
        product_id=product.id,
        name=product.name,
        category=product.category,
        quantity=max(1, quantity),
        size=resolved_size,
        toppings=resolved_toppings,
        options=options,
        special_instructions=special_instructions,
    )

    # 5. Price it
    cart_item = pricing.price_item(cart_item)
    session.cart.append(cart_item)

    return AddToCartResult(
        success=True,
        item=cart_item,
        missing_options=[],  # no required-options system yet, placeholder
        issues=issues,
    )


@tool(description="Remove an item from the order cart")
def remove_from_cart(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
    item_reference: str,
) -> RemoveFromCartResult:
    matches = _find_cart_items(session, item_reference)

    if len(matches) == 0:
        return RemoveFromCartResult(success=False)
    if len(matches) > 1:
        return RemoveFromCartResult(success=False, matches=matches)

    session.cart.remove(matches[0])
    return RemoveFromCartResult(success=True, removed=[matches[0]])


@tool(description="Modify an existing cart item's quantity, size, or toppings")
def update_item(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
    item_reference: str,
    quantity: int | None = None,
    size: str | None = None,
    toppings: list[str] | None = None,
    options: dict[str, str] | None = None,
    special_instructions: str | None = None,
) -> UpdateItemResult:
    matches = _find_cart_items(session, item_reference)

    if len(matches) == 0:
        return UpdateItemResult(success=False)
    if len(matches) > 1:
        return UpdateItemResult(success=False, matches=matches)

    item = matches[0]
    product = catalogue.get_product(item.product_id)
    issues: list[str] = []

    # Coerce numeric args — LLMs may emit them as strings
    if quantity is not None:
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1

    # Apply changes
    if quantity is not None:
        item.quantity = max(1, quantity)
    if size is not None and product is not None:
        resolved, size_issues = catalogue.resolve_size(product, size)
        item.size = resolved
        issues.extend(size_issues)
    if toppings is not None and product is not None:
        resolved, topping_issues = catalogue.resolve_toppings(product, toppings)
        item.toppings = resolved
        issues.extend(topping_issues)
    if options is not None:
        item.options = {**item.options, **options}
    if special_instructions is not None:
        item.special_instructions = special_instructions

    # Re-price
    updated = pricing.price_item(item)
    # Replace in cart
    idx = session.cart.index(item)
    session.cart[idx] = updated

    return UpdateItemResult(success=True, item=updated, issues=issues)


@tool(description="Show the current cart contents and prices")
def view_cart(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
) -> ViewCartResult:
    order_type = session.customer.order_type or OrderType.PICKUP
    subtotal, _, _ = pricing.compute_totals(session.cart, order_type)
    return ViewCartResult(
        items=session.cart,
        subtotal=subtotal,
        item_count=len(session.cart),
    )


@tool(description="Set or update customer delivery/pickup information")
def set_customer_info(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    order_type: str | None = None,
) -> SetCustomerInfoResult:
    # Merge — only non-None values overwrite
    missing: list[str] = []
    if name is not None:
        session.customer.name = name
    if phone is not None:
        session.customer.phone = phone
    if address is not None:
        session.customer.address = address
    if order_type is not None:
        try:
            session.customer.order_type = OrderType(order_type.lower())
        except ValueError:
            logger.warning(
                "set_customer_info: invalid order_type '%s', ignoring", order_type
            )
            missing.append(
                f"order_type must be 'delivery' or 'pickup', got '{order_type}'"
            )

    # What's still required?
    if not session.customer.name:
        missing.append("name")
    if not session.customer.phone:
        missing.append("phone")
    if (
        session.customer.order_type == OrderType.DELIVERY
        and not session.customer.address
    ):
        missing.append("address")

    return SetCustomerInfoResult(
        success=True,
        info=session.customer,
        missing_required=missing,
    )


@tool(description="Request order review — shows summary and asks for confirmation")
def request_review(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
) -> RequestReviewResult:
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

    # Check minimum order amount
    subtotal, _, _ = pricing.compute_totals(session.cart, session.customer.order_type or OrderType.PICKUP)
    if not pricing.check_minimum_order(subtotal):
        issues.append(
            f"Order minimum is ${pricing.min_order_amount:.2f}. "
            f"Your current subtotal is ${subtotal:.2f}."
        )

    if issues:
        return RequestReviewResult(success=False, issues=issues)

    session._pending_transition = OrderState.REVIEW
    return RequestReviewResult(success=True, issues=[])


@tool(description="Confirm the order. Payment is cash on delivery/pickup only.")
def confirm_order(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
    payment_method: str = "cash",
) -> ConfirmOrderResult:
    order_type = session.customer.order_type or OrderType.PICKUP
    subtotal, _, total = pricing.compute_totals(session.cart, order_type)

    if not pricing.check_minimum_order(subtotal):
        return ConfirmOrderResult(
            success=False,
            order_id=None,
            total=total,
            issues=[
                f"Order minimum is ${pricing.min_order_amount:.2f}. "
                f"Your current subtotal is ${subtotal:.2f}."
            ],
        )

    order_id = generate_order_id(session.restaurant_id)

    session.payment_method = payment_method
    session._confirmed_order_id = order_id

    if payment_method == "link":
        session._pending_transition = OrderState.PAYMENT_PENDING
    else:
        session._pending_transition = OrderState.COMPLETED

    return ConfirmOrderResult(success=True, order_id=order_id, total=total)


@tool(description="Browse the full restaurant menu — see categories, items, sizes, prices, and available toppings")
def browse_menu(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
) -> BrowseMenuResult:
    summary = catalogue.get_menu_summary()
    return BrowseMenuResult(
        restaurant_name=catalogue.restaurant_name,
        categories=summary["categories"],
        deals=summary["deals"],
        delivery_fee=summary["delivery_fee"],
    )


@tool(description="Cancel the entire order")
def cancel_order(
    session: OrderSession,
    catalogue: Catalogue,
    pricing: PricingEngine,
) -> CancelOrderResult:
    session._pending_transition = OrderState.CANCELLED
    return CancelOrderResult(success=True, message="Order has been cancelled.")


# =============================================================================
# Tool Availability
# =============================================================================

TOOLS_BY_STATE: dict[OrderState, list] = {
    OrderState.BUILDING: [
        browse_menu,
        add_to_cart,
        remove_from_cart,
        update_item,
        view_cart,
        set_customer_info,
        request_review,
        cancel_order,
    ],
    OrderState.REVIEW: [
        browse_menu,
        add_to_cart,
        remove_from_cart,
        update_item,
        view_cart,
        set_customer_info,
        confirm_order,
        cancel_order,
    ],
    OrderState.PAYMENT_PENDING: [
        view_cart,
        cancel_order,
    ],
}

# =============================================================================
# Helpers
# =============================================================================


def _find_cart_items(session: OrderSession, ref: str) -> list[CartItem]:
    """Find cart items matching a user-facing reference.

    Priority: UUID match → index match → name fuzzy match.
    """
    ref_stripped = ref.strip()

    # 1. UUID match
    for item in session.cart:
        if item.id == ref_stripped:
            return [item]

    # 2. Index match (1-based)
    try:
        idx = int(ref_stripped) - 1
        if 0 <= idx < len(session.cart):
            return [session.cart[idx]]
    except ValueError:
        pass

    # 3. Name fuzzy match
    query = ref_stripped.lower()
    matches: list[CartItem] = []

    # Exact match
    for item in session.cart:
        if item.name.lower() == query:
            matches.append(item)
    if matches:
        return matches

    # Substring
    for item in session.cart:
        if query in item.name.lower():
            matches.append(item)
    if matches:
        return matches

    # Reverse substring
    for item in session.cart:
        if item.name.lower() in query:
            matches.append(item)

    return matches

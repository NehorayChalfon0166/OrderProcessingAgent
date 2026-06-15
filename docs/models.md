# Component: `models.py`

Status: **SETTLED**

## Enums

```python
class OrderState(str, Enum):
    BUILDING = "building"
    REVIEW = "review"
    PAYMENT_PENDING = "payment_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class OrderType(str, Enum):
    DELIVERY = "delivery"
    PICKUP = "pickup"
```

## Cart Models

```python
class CartTopping(BaseModel):
    topping_id: str
    name: str
    price: float

class CartItem(BaseModel):
    id: str                          # stable UUID for remove/update reference
    product_id: str                  # "pizza_margherita"
    name: str                        # "Margherita Pizza"
    category: str                    # "Pizzas"
    quantity: int = 1
    size: str | None = None
    toppings: list[CartTopping] = []
    options: dict[str, str] = {}     # e.g. {"sauce": "marinara"} — item-specific add-ons
    special_instructions: str | None = None
    base_price: float = 0.0          # set by PricingEngine
    line_total: float = 0.0          # set by PricingEngine
    missing_options: list[str] = []  # required options not yet provided by user
```

## CustomerInfo

```python
class CustomerInfo(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None
    order_type: OrderType | None = None
```

**Merge semantics:** Each `set_customer_info` call fills whatever fields are provided, overwriting previous values. Never clears fields that aren't explicitly set.

## Tool Result Types

Every tool returns a typed Pydantic model — not a generic dict. The LLM sees structured data in tool results. Python gets type-safe access.

```python
class AddToCartResult(BaseModel):
    success: bool
    item: CartItem | None = None
    suggestions: list[str] = []       # product name suggestions if not found
    missing_options: list[str] = []   # required options still needed
    issues: list[str] = []            # non-blocking warnings (e.g. invalid size, used default)

class RemoveFromCartResult(BaseModel):
    success: bool
    removed: list[CartItem] = []
    matches: list[CartItem] = []      # ambiguous — multiple items matched

class UpdateItemResult(BaseModel):
    success: bool
    item: CartItem | None = None
    matches: list[CartItem] = []      # ambiguous — multiple items matched

class ViewCartResult(BaseModel):
    items: list[CartItem]
    subtotal: float
    item_count: int

class SetCustomerInfoResult(BaseModel):
    success: bool
    info: CustomerInfo
    missing_required: list[str]       # e.g. ["phone"] — what's still needed

class RequestReviewResult(BaseModel):
    success: bool
    issues: list[str]                 # preconditions not met, e.g. ["Cart is empty", "Phone required"]

class ConfirmOrderResult(BaseModel):
    success: bool
    order_id: str | None = None
    total: float = 0.0

class CancelOrderResult(BaseModel):
    success: bool
    message: str
```

## What Was Dropped from v1

| v1 Model | Why dropped |
|---|---|
| `LLMResponse` | Replaced by tool calls — text and function calls are separate channels |
| `ExtractedItem` | Tools use typed parameters; no extraction/parsing step needed |
| `ExtractedCustomerInfo` | Same — `set_customer_info` takes typed params directly |
| `LLMAction` enum | Actions ARE tool calls now |
| `OrderSummary` | Will be rebuilt differently for persistence if needed |
| `ToppingSelection` | Renamed to `CartTopping` (clearer name, same purpose) |
| `OrderItem` | Renamed to `CartItem` (clearer name, added: id, options, missing_options) |

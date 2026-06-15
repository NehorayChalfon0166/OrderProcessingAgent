# Component: `tools.py`

Status: **SETTLED**

Bridge between LLM intent and application state. Each tool is a function that takes
typed parameters, validates against the catalogue, mutates session state, and returns
a typed result model.

## Design Decisions

- **Mutation over pure functions.** Tools hold references to session, catalogue, and
  pricing. They mutate session directly rather than returning modified copies. Simpler
  call sites; we extract pure functions later if testing demands it.
- **Warn-and-continue on invalid options.** A bad size/topping gets resolved to the
  default with a warning in `issues: list[str]`, rather than rejecting the item.
  Better UX — the LLM doesn't have to retry the tool call.
- **`_transition` via session.** Tools set `session._pending_transition` when they
  want a state change. The agent loop reads and clears this after execution, so it
  never reaches the LLM.
- **Separate line items.** Adding the same product twice creates two cart items.
  Merging would hide different special instructions. `update_item` handles quantity changes.

## Tool Implementations

### `add_to_cart`
```
Available in: BUILDING, REVIEW
Parameters:   product_name (str), quantity (int)=1, size (str|None)=None,
              toppings (list[str])=[], options (dict[str,str])={},
              special_instructions (str|None)=None

1. catalogue.find_product(name)
   → If None: return AddToCartResult(success=False, suggestions=[...])
2. If product is a deal: catalogue.expand_deal() → add all items → return
3. catalogue.resolve_size(product, size) → (resolved_size, issues)
4. catalogue.resolve_toppings(product, names) → (cart_toppings, issues)
5. Check product.required_options against provided options → missing list
6. Create CartItem with UUID
7. pricing.price_item(cart_item) → fill base_price + line_total
8. Append to session.cart
9. Return AddToCartResult(success=True, item=..., missing_options=[...], issues=[...])

Return type: AddToCartResult
```

### `remove_from_cart`
```
Available in: BUILDING, REVIEW
Parameters:   item_reference (str)

1. Try UUID match (exact on CartItem.id)
2. Try index match (int → nth cart item, 1-based)
3. Try name match (fuzzy: exact → substring → reverse substring)
4. 1 match → remove, return RemoveFromCartResult(success=True, removed=[item])
5. Multiple matches → return RemoveFromCartResult(success=False, matches=[...])
6. Zero matches → return RemoveFromCartResult(success=False)

Return type: RemoveFromCartResult
```

### `update_item`
```
Available in: BUILDING, REVIEW
Parameters:   item_reference (str), quantity (int|None)=None,
              size (str|None)=None, toppings (list[str]|None)=None,
              options (dict[str,str]|None)=None,
              special_instructions (str|None)=None

Same disambiguation as remove_from_cart. Once found:
- quantity changed → re-price
- size changed → catalogue.resolve_size → re-price
- toppings changed → catalogue.resolve_toppings → re-price
- options changed → merge with existing, check required
- special_instructions changed → replace

Return type: UpdateItemResult
```

### `view_cart`
```
Available in: BUILDING, REVIEW, PAYMENT_PENDING
Parameters:   none

Reads session.cart + session.customer. Computes subtotal via pricing engine.

Return type: ViewCartResult
```

### `set_customer_info`
```
Available in: BUILDING, REVIEW
Parameters:   name (str|None)=None, phone (str|None)=None,
              address (str|None)=None, order_type (str|None)=None

Merge semantics: only non-None values overwrite. Normalizes order_type to
OrderType enum (invalid values → ignored with warning).

After merge, compute which required fields are still missing:
- name always required
- phone always required
- address required if order_type is DELIVERY

Return type: SetCustomerInfoResult
```

### `request_review`
```
Available in: BUILDING
Parameters:   none

Preconditions (checked in order):
1. Cart not empty
2. name present
3. phone present
4. If order_type is DELIVERY: address present

If any fail: return RequestReviewResult(success=False, issues=[...])
If all pass: session._pending_transition = OrderState.REVIEW

Return type: RequestReviewResult
```

### `confirm_order`
```
Available in: REVIEW
Parameters:   none

Generates a fake payment ID (UUID). Records order total.
session._pending_transition = OrderState.PAYMENT_PENDING

Return type: ConfirmOrderResult
```

### `cancel_order`
```
Available in: BUILDING, REVIEW, PAYMENT_PENDING
Parameters:   none

session._pending_transition = OrderState.CANCELLED

Return type: CancelOrderResult
```

## State Availability Matrix

| Tool | BUILDING | REVIEW | PAYMENT_PENDING |
|---|---|---|---|
| add_to_cart | ✓ | ✓ | — |
| remove_from_cart | ✓ | ✓ | — |
| update_item | ✓ | ✓ | — |
| view_cart | ✓ | ✓ | ✓ |
| set_customer_info | ✓ | ✓ | — |
| request_review | ✓ | — | — |
| confirm_order | — | ✓ | — |
| cancel_order | ✓ | ✓ | ✓ |

## Configuration: `TOOLS_BY_STATE`

```python
TOOLS_BY_STATE: dict[OrderState, list[callable]] = {
    OrderState.BUILDING: [
        add_to_cart, remove_from_cart, update_item, view_cart,
        set_customer_info, request_review, cancel_order,
    ],
    OrderState.REVIEW: [
        add_to_cart, remove_from_cart, update_item, view_cart,
        set_customer_info, confirm_order, cancel_order,
    ],
    OrderState.PAYMENT_PENDING: [
        view_cart, cancel_order,
    ],
}
```

This dict is the single source of truth for state gating. The prompt builder reads
it to generate tool definitions for the LLM. The agent loop reads it to dispatch
tool calls. No other file defines tool availability.

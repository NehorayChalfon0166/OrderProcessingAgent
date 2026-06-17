# Payment Architecture

Status: **DESIGN — dormant infrastructure, not yet active**

## Current State (master)

`confirm_order` transitions directly to COMPLETED. No payment processing.
`PAYMENT_PENDING` exists in the enum and state machine but is never used.

## Target Architecture

### Layers

```
CORE (master)              CHANNEL (integration)       EXTERNAL
────────────────           ─────────────────────       ────────
State machine              Link delivery               Payment processor
payment_method field       "We texted you a link"      Webhook → us
Webhook endpoint           WhatsApp button / SMS / CLI
```

The core knows nothing about WhatsApp, voice, SMS, or the payment processor.
It only tracks: cash = done now, link = wait for external confirmation.

### State Machine

```
BUILDING → REVIEW → confirm_order(cash) → COMPLETED
                  → confirm_order(link) → PAYMENT_PENDING → [webhook] → COMPLETED
                                               ↓
                                           CANCELLED (anytime)
```

### Dormant Infrastructure (on master, backward-compatible)

1. **`OrderSession.payment_method`** — `None` by default. Set by `confirm_order`.
   Serialized with the session so the webhook can read it.

2. **`confirm_order` takes optional `payment_method`** — defaults to `"cash"`
   (current behavior preserved — COMPLETED directly). `"link"` → PAYMENT_PENDING.

3. **Everything else already exists** — `_apply_transition` handles both
   PAYMENT_PENDING and COMPLETED. `TOOLS_BY_STATE` has PAYMENT_PENDING tools.
   `cancel_order` works from any state. Session router treats PAYMENT_PENDING
   as active (not terminal).

### Activation (future, when payment processor is ready)

**On master**: Add `POST /payment/webhook` endpoint.
- Receives `order_id` + payment processor signature
- Looks up session via `SessionRouter`
- Verifies state is PAYMENT_PENDING
- Transitions to COMPLETED
- Idempotent (already COMPLETED → 200 OK)
- One endpoint for all channels

**On integration branches**: Deliver payment link via channel-specific method.

| Channel | Cash | Link |
|---|---|---|
| CLI | "Order confirmed!" → done | Prints URL → waits for webhook |
| WhatsApp | "Pay at pickup" → done | Sends link message → waits for webhook |
| Voice | "Pay at pickup. Goodbye!" → hang up | "We texted link." → hang up → SMS → webhook → confirmation text |

### Payment Methods

Restaurant-configurable. Default: both cash and link available.
LLM offers choices based on what the restaurant supports.

- **Cash**: straight to COMPLETED. Customer pays at pickup/delivery.
- **Link**: transitions to PAYMENT_PENDING. Channel delivers link. External
  webhook completes. Link URL contains encoded `order_id` (session.session_id).

### Edge Cases

- **Customer silent during PAYMENT_PENDING**: Session stale after 2 hours →
  router replaces on next contact. Order abandoned. No charge.
- **Double payment**: Webhook checks state. Already COMPLETED → 200 OK idempotently.
- **Cancel during PAYMENT_PENDING**: `cancel_order` already available in
  PAYMENT_PENDING tools.
- **Payment reference**: `session.session_id` (phone number in server mode)
  serves as the order ID. The link encodes it. The webhook resolves via
  `SessionRouter`.
- **Cash ↔ link change of mind**: Cancel and re-order for MVP. Future:
  `change_payment_method` tool.

### Why the LLM never touches payment

Payment is a security boundary. The LLM can suggest, but only Python code:
- Sets the payment method on `confirm_order`
- Validates the webhook signature
- Transitions state
- Delivers the link (channel layer, deterministic)

No `process_payment` tool. No LLM webhook. Payment confirmation comes from
an external processor with cryptographic signature verification.

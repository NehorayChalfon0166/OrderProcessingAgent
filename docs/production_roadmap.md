# Production Roadmap — From Demo to Business

Six components needed to make the Order Processing Agent a viable product.
Listed in implementation order (each builds on the previous).

## 1. Database (SQLite + Peewee)

**Status:** Fully designed

Move sessions and orders from JSON files to SQLite with WAL mode. Menus
and restaurant config stay as JSON files.

### Schema

Two tables. Complex nested data (cart items with toppings, customer info,
conversation history) stored as JSON columns — Pydantic handles serialization:

```sql
CREATE TABLE sessions (
    session_id    TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'building',
    cart          TEXT NOT NULL DEFAULT '[]',       -- JSON: list[CartItem]
    customer      TEXT NOT NULL DEFAULT '{}',       -- JSON: CustomerInfo
    conversation  TEXT NOT NULL DEFAULT '[]',       -- JSON: list[Message]
    payment_method TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (restaurant_id, session_id)
);

CREATE TABLE orders (
    id              TEXT NOT NULL PRIMARY KEY,
    restaurant_id   TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    customer_name   TEXT,
    customer_phone  TEXT,
    items           TEXT NOT NULL DEFAULT '[]',     -- JSON: list[CartItem]
    subtotal        REAL NOT NULL,
    delivery_fee    REAL NOT NULL,
    total           REAL NOT NULL,
    order_type      TEXT NOT NULL,
    payment_method  TEXT,
    printed         INTEGER NOT NULL DEFAULT 0,     -- for printer agent (component 3)
    created_at      TEXT NOT NULL
);
```

`restaurant_id` is part of the sessions primary key — same phone can order
from two restaurants, two rows. `printed` column is forward-looking for
the printer agent.

### Database class (new file: `db.py`)

Thin module that owns the connection and provides CRUD. The rest of the
codebase never touches SQL directly:

```python
class Database:
    def __init__(self, path: str = "order_agent.db"):
        self._db = SqliteDatabase(path, pragmas={
            "journal_mode": "wal",
            "foreign_keys": 1,
        })
        self._db.connect()
        self._create_tables()

    # Session CRUD
    def save_session(self, session: OrderSession) -> None: ...
    def load_session(self, restaurant_id: str, session_id: str) -> OrderSession | None: ...
    def delete_session(self, restaurant_id: str, session_id: str) -> None: ...

    # Order CRUD
    def save_order(self, order_data: dict) -> None: ...
    def get_orders(self, restaurant_id: str, limit: int = 50) -> list[dict]: ...
    def get_unprinted_orders(self, restaurant_id: str) -> list[dict]: ...
    def mark_printed(self, order_id: str) -> None: ...
```

### How OrderSession changes (`session.py`)

A `_db` private attribute is added. When set, `save()` and `load()` use
the database. When not set, they fall back to JSON files. This means
existing tests that don't configure a database continue to work unchanged.

**`save()`** — detects DB vs filesystem:
```python
def save(self, sessions_dir: str = "sessions") -> Path:
    self.updated_at = datetime.now(timezone.utc).isoformat()
    if self._db is not None:
        self._db.save_session(self)
        return Path()  # no filepath when DB-backed
    # Fallback: JSON file (existing logic unchanged)
    ...

@classmethod
def load(cls, session_id: str, sessions_dir: str = "sessions") -> "OrderSession":
    # Unchanged — filesystem-only. DB loads go through Database.load_session().
    # The SessionRouter dispatches between them.
    ...
```

**Why `load()` stays filesystem-only:**

`OrderSession.load()` currently takes `(session_id, sessions_dir)`. Adding
`restaurant_id` and `db` parameters would change the signature for every
caller — tests, SessionRouter, migration scripts. Instead, the SessionRouter
handles the dispatch:

- If `db` is provided → router calls `db.load_session(restaurant_id, sid)`
- If not → router calls `OrderSession.load(sid, session_dir)` (unchanged)

This keeps `OrderSession.load()` backward-compatible. New code that needs
the database goes through the router or `Database` directly, never through
`OrderSession.load()`.

### How SessionRouter changes (`session_router.py`)

`get_or_create` gets an optional `db` parameter:

```python
def get_or_create(self, restaurant_id: str, phone_number: str,
                  db: Database | None = None) -> OrderSession:
    sid = self._sanitize(phone_number)

    if db is not None:
        # DB path
        try:
            session = db.load_session(restaurant_id, sid)
            if session is not None:
                session._db = db
                if session.state in (OrderState.CANCELLED, OrderState.COMPLETED):
                    session = OrderSession(restaurant_id=restaurant_id)
                    session.session_id = sid
                    session._db = db
                    session.save()
                return session
        except Exception:
            pass
        session = OrderSession(restaurant_id=restaurant_id)
        session.session_id = sid
        session._db = db
        session.save()
        return session

    # Filesystem path (existing logic unchanged)
    ...
```

Once the router sets `session._db = db`, `session.save()` automatically
routes to the database — no extra parameter needed.

### Connection lifecycle

`Database` is created once at startup and shared throughout the process:

```python
# server.py lifespan (twilio-integration branch)
db = Database(cfg.db_path)

# CLI (main.py — master branch)
db = Database(config.db_path)

# process_turn doesn't change — session.save() does the right thing internally
```

### Migration

A one-time check in `Database.__init__`: if the sessions table is empty
and JSON session files exist, migrate them:

```python
def _migrate_if_needed(self):
    if Session.select().count() > 0:
        return  # already migrated
    # Scan sessions/ and orders/ directories, import into DB
    for restaurant_dir in Path("sessions").iterdir():
        ...
    for restaurant_dir in Path("orders").iterdir():
        ...
```

### Branch breakdown

| Branch | Changes |
|---|---|
| **master** | `db.py` (new), `session.py` (_db attr, save() DB path), `session_router.py` (optional db param), `config.py` (DB_PATH env var), `main.py` (create Database in CLI) |
| **twilio-integration** | `server.py` (create Database in lifespan, pass to router), `_save_order_file()` → use `db.save_order()` |

### What doesn't change

`process_turn`, tools, catalogue, pricing, prompts, agent_loop, models —
none of these touch storage directly.

## 2. Payment Processing (Stripe)

**Status:** Fully designed

Add Stripe Checkout for online payment. Cash-on-delivery already works
(`payment_method="cash"` → `COMPLETED` directly).

### Architecture

The entire payment flow lives on the channel layer. The core
(`process_turn`, tools, state machine) already supports it — `confirm_order`
with `payment_method="link"` transitions to `PAYMENT_PENDING`. What's
missing is the actual link generation and webhook reception.

```
Customer: "I confirm" (via WhatsApp)
  → LLM calls confirm_order(payment_method="link")
  → State: PAYMENT_PENDING
  → process_turn() returns
  → server.py detects PAYMENT_PENDING
  → Creates Stripe Checkout Session
  → Sends WhatsApp: "Pay here: https://checkout.stripe.com/..."
  → Customer taps link, pays on Stripe's hosted page
  → Stripe POSTs to POST /payment/webhook
  → Verify signature, load session, transition to COMPLETED
  → Send WhatsApp: "Payment received! Order confirmed."
```

The CLI path never generates payment links (no WhatsApp to send them to).
If a CLI session enters PAYMENT_PENDING, the user sees a message telling
them to complete payment through WhatsApp.

### New file: `payment.py`

Two functions, pure stateless logic:

```python
import stripe

def create_checkout_session(
    session_id: str,
    restaurant_id: str,
    restaurant_name: str,
    items: list[CartItem],
    total: float,
    currency: str = "ils",
) -> str:
    """Create a Stripe Checkout Session. Returns the payment URL."""
    stripe.api_key = _get_stripe_key()
    checkout = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": currency,
                "product_data": {"name": item.name},
                "unit_amount": int(item.line_total * 100),  # agorot
            },
            "quantity": item.quantity,
        } for item in items],
        metadata={
            "session_id": session_id,
            "restaurant_id": restaurant_id,
        },
        # No success_url/cancel_url needed — customer stays in WhatsApp
    )
    return checkout.url


def verify_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify Stripe webhook signature. Returns the event data."""
    stripe.api_key = _get_stripe_key()
    event = stripe.Webhook.construct_event(
        payload, sig_header, _get_webhook_secret()
    )
    return event
```

`metadata` carries `session_id` and `restaurant_id` — everything needed
to find the session when the webhook arrives.

### New endpoint: `POST /payment/webhook`

Added to `server.py` (twilio-integration branch):

```python
@app.post("/payment/webhook")
async def receive_payment(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = verify_webhook(payload, sig_header)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] != "checkout.session.completed":
        return {"status": "ignored"}

    # Extract session identity from metadata
    metadata = event["data"]["object"]["metadata"]
    restaurant_id = metadata["restaurant_id"]
    session_id = metadata["session_id"]

    # Load session and complete
    session = _router.get_or_create(restaurant_id, session_id, db=_db)
    if session.state == OrderState.COMPLETED:
        return {"status": "already_completed"}  # idempotent

    session.state = OrderState.COMPLETED
    session.save()

    # Send confirmation via WhatsApp
    _twilio.send_whatsapp_message(
        session.session_id,  # session_id = phone digits
        f"Payment received! Your order is confirmed. "
        f"Estimated {_estimate_delivery_time(session)} minutes."
    )
    return {"status": "ok"}
```

### Payment link generation in the webhook flow

After `process_turn()` returns in `receive_whatsapp`, check state:

```python
# After process_turn() returns, before sending reply:
response_text = await asyncio.to_thread(process_turn, ...)

if session.state == OrderState.PAYMENT_PENDING:
    # Generate Stripe link instead of plain text response
    payment_url = create_checkout_session(
        session_id=session.session_id,
        restaurant_id=restaurant_id,
        restaurant_name=restaurant_ctx.config.name,
        items=session.cart,
        total=pricing.compute_totals(session.cart, order_type)[2],
    )
    response_text = f"Pay here to confirm your order: {payment_url}"

session.save(...)
await asyncio.to_thread(_twilio.send_whatsapp_message, wa_id, response_text)
```

### Config additions (`config.py`)

```python
stripe_secret_key: str = ""          # STRIPE_SECRET_KEY env var
stripe_webhook_secret: str = ""      # STRIPE_WEBHOOK_SECRET env var
```

These are channel-layer fields but per CLAUDE.md, config.py on master
can hold dormant defaults for channel fields.

### Edge cases

| Scenario | Handling |
|---|---|
| **Double webhook** (Stripe retries) | Check `session.state == COMPLETED` → return `"already_completed"` |
| **Stale session** (customer abandons payment) | Session stays in PAYMENT_PENDING. Stripe Checkout expires after 24h, no charge. Staleness check in server.py handles cleanup. |
| **Cancel during PAYMENT_PENDING** | `cancel_order` is already in `TOOLS_BY_STATE["payment_pending"]` — customer can cancel any time |
| **Payment for wrong amount** | Cannot happen — Stripe enforces the exact amount from line items |
| **Webhook before session save** | Unlikely (link sent AFTER save). If it happens, `get_or_create` will find the session or create a new one — but the metadata IDs ensure it finds the right one. |

### What doesn't change

The core (`process_turn`, tools, models, state machine) is untouched.
`confirm_order(payment_method="link")` already transitions to `PAYMENT_PENDING`.
The payment layer only REACTS to that state — it doesn't modify the core.

### PCI scope

SAQ A — the lowest level. Stripe hosts the payment page, we never touch
card data. The webhook carries metadata, not payment details.

### Branch breakdown

| Branch | Changes |
|---|---|
| **master** | `payment.py` (new), `config.py` (stripe fields as dormant defaults) |
| **twilio-integration** | `server.py` (payment link generation after process_turn, POST /payment/webhook endpoint) |

### Testing strategy

- Stripe test mode is free and unlimited — full end-to-end testing possible
- Test webhook delivery via `stripe trigger checkout.session.completed`
- Unit tests for `payment.py` with mocked Stripe API
- Integration test: mock `process_turn` to return PAYMENT_PENDING, verify link generation

## 3. Thermal Printer Agent

**Status:** Fully designed

Automatically print kitchen tickets (bones) on the restaurant's thermal
printer when an order is confirmed. The agent runs on the restaurant's
existing POS computer — no new hardware except the printer (which every
restaurant already has).

### Architecture

Pull-based polling model. The cloud never reaches into the restaurant's
network — the agent reaches OUT:

```
Cloud (FastAPI)                        Restaurant (POS computer)
┌──────────────────┐                  ┌──────────────────────┐
│  orders table    │ ←── GET /api/    │  Python agent (.exe) │
│  printed=false   │     orders?      │  polls every 5-10s   │
│                  │     printed=false│                      │
│  mark printed    │ ─── POST /api/   │  formats ESC/POS     │
│  printed=true    │     orders/{id}/ │  sends to printer    │
│                  │     printed      │  (port 9100)         │
└──────────────────┘                  └──────────────────────┘
```

### API endpoints (added to `server.py`)

```
GET  /api/orders?restaurant_id=X&printed=false&token=Y
     → Returns JSON list of unprinted orders for that restaurant.
       Auth: bearer token per restaurant (stored in restaurants.json).

POST /api/orders/{order_id}/printed?token=Y
     → Marks an order as printed=true. Idempotent.
```

These are on the channel layer (twilio-integration) — the core has no
concept of "printed." The `printed` column already exists in the orders
table schema (from component 1).

### ESC/POS formatting (`printer.py`)

New module that converts an order dict to ESC/POS bytes. Uses the
`python-escpos` library. The real work is the ticket layout — designing
what the chef sees on 80mm thermal paper:

```
══════════════════════════════
        MARIO'S PIZZERIA
        KITCHEN TICKET
══════════════════════════════
Order: #972539534345
Type:  DELIVERY
Time:  18:42

Customer: John
Phone:   055-1234567
Address: 123 Main St, Apt 4

──────────────────────────────
 1x Margherita          LARGE
     + Extra Cheese
     + Mushrooms
     [well done]

 2x Pepperoni           MEDIUM

 1x Garlic Bread

 2x Coca Cola          REGULAR
──────────────────────────────
TOTAL:           ₪154.50
──────────────────────────────
    "Extra sauce on the side"
══════════════════════════════
```

`printer.py` exposes a single function:
```python
def format_order(order: dict, restaurant_name: str) -> bytes:
    """Convert an order dict to ESC/POS bytes ready for the printer."""
```

### Printer transport — pluggable backend

Port 9100 (RAW TCP) is the universal printing port on every network
thermal printer — Epson, Star, Bixolon, Xprinter all support it. Open
a TCP socket, send ESC/POS bytes, done. This covers the vast majority
of restaurant printers.

But not all restaurants use network printers. The agent uses a pluggable
backend so the transport can be swapped without changing the ESC/POS
formatting:

| Backend | Protocol | When |
|---|---|---|
| **RAW TCP** (default) | TCP socket to `printer_ip:9100` | Network printer — most common |
| **Windows spooler** | `win32print` — prints via OS driver | USB printer plugged into POS |
| **ePOS-Print** (future) | HTTP/XML to printer's built-in server | Newer Epson cloud-capable models |

The ESC/POS bytes produced by `printer.py` are identical regardless of
backend. Only the delivery step changes. The agent tries RAW TCP first
(saved IP), falls back to Windows spooler if no IP is configured, and
can add ePOS as a third option in the future.

### Virtual printer for development

`printer.py` supports a `mode="file"` backend that outputs an HTML file
styled to look like thermal paper (80mm width, monospace, black on white).
The entire flow can be built and tested without a physical printer.
Swap to `mode="escpos"` when a real printer is connected.

### Error recovery

- **Printer offline / out of paper:** Retry 3 times with 30-second backoff.
  After 3 failures, leave `printed=false` — retried on next poll cycle.
- **Network down:** Agent logs error locally, retries next poll. Cloud is
  unaffected — orders stay `printed=false` until agent reconnects.
- **Crash safety:** `printed` is set to `true` only AFTER printer confirms.
  Crash mid-print → order stays `printed=false` → retried.

Thermal printers support codepages for non-ASCII characters. Hebrew
codepage support is inconsistent across printer models — some support
it, some don't, some require firmware-specific configuration.

**V1 approach:** Render the ticket text to a monochrome raster image
using PIL/Pillow, then print as graphics via ESC/POS bitmap commands.
This works on every printer regardless of codepage support. It adds
~200ms to print time (image rendering) — negligible for a kitchen ticket.

**Future:** If codepage testing with the actual printer model confirms
Hebrew support, switch to direct text mode. This is faster and produces
sharper text. The `python-escpos` library supports codepage switching
via `printer.charcode('CP862')` (Hebrew). The formatting function
accepts a `mode` parameter for future switching.

### Printer auto-discovery

When the agent starts, it finds the printer on the local network:

1. Check `config.json` for a saved printer IP → try connecting on port 9100
2. If saved IP fails or is absent → scan local subnet on port 9100
3. If scan finds nothing → prompt for manual IP entry
4. Save successful IP to `config.json` for next startup

Port 9100 is the RAW ESC/POS standard port — every network thermal
printer uses it.

### Agent packaging

Single `.exe` via PyInstaller. No Python, no dependencies, no install.
Runs silently in the system tray. Shortcut in Windows `Startup` folder
so it launches on boot.

### Virtual printer for development

`printer.py` has a `mode="file"` that outputs an HTML file styled to
look like thermal paper (80mm width, monospace, black on white). This
lets us build and test the entire flow without a physical printer.
Swap to `mode="escpos"` when a real printer is connected.

### Error recovery

- **Printer offline:** Retry 3 times with 30-second backoff. After 3
  failures, leave `printed=false` — the order will be retried on the
  next poll cycle.
- **Out of paper:** Same retry logic. Printers signal this via ESC/POS
  status commands.
- **Network down:** Agent logs the error locally, retries on next poll.
  The cloud is unaffected — orders just stay `printed=false` until the
  agent reconnects.
- **Never drop an order:** `printed` is only set to `true` AFTER the
  printer confirms success. If the agent crashes mid-print, the order
  stays `printed=false` and is retried.

### Branch breakdown

| Branch | Changes |
|---|---|
| **master** | `printer.py` (new, order→ESC/POS formatting — reusable across channels) |
| **twilio-integration** | `server.py` (GET/POST /api/orders endpoints, token auth) |
| **Standalone** | Agent `.exe` — separate repo or directory, not part of the server. It's a client that happens to be written in Python. |

### Testing

- Virtual printer mode: `format_order(order, mode="file")` → open HTML
- API endpoints: standard pytest + FastAPI TestClient
- Agent: run as a script pointed at a test server, verify it fetches,
  "prints," and marks complete

## 4. Order Fulfillment (WhatsApp Notifications)

**Status:** Fully designed

When an order completes, send the order summary to the restaurant's own
WhatsApp number. No dashboard, no new interface — the restaurant gets
orders the same way customers send them.

### How it works

After `save_order()` writes the completed order, send a second WhatsApp
message to the restaurant:

```python
restaurant_phone = restaurant_ctx.config.twilio_phone
message = format_order_for_restaurant(order, session)
_twilio.send_whatsapp_message(restaurant_phone.removeprefix("+"), message)
```

The restaurant phone is already in `restaurants.json`. One extra API
call per order. Zero new infrastructure.

### Message format

A readable summary, not a receipt:

```
🔔 New Order!
Order #972539534345
Customer: John (055-1234567)
Type: Delivery
Address: 123 Main St, Apt 4
──────────────────
2x Margherita (large) + Extra Cheese
1x Garlic Bread
2x Coca Cola (regular)
──────────────────
Total: ₪154.50
```

### Future

This is superseded by the thermal printer agent (component 3). Once the
printer is live, WhatsApp notifications become a backup — the kitchen
staff work from printed bones, not phone notifications.

### Branch

`twilio-integration` only — the change is in `server.py`, adding one
Twilio send after `_save_order_file()`.

## 5. Restaurant Onboarding

**Status:** Designed (V1 manual, V2 WhatsApp)

How new restaurants get set up in the system.

### V1 — Manual (current)

Edit `restaurants.json`, create a menu file in `menus/`, restart server.
Works for the first few restaurants. The developer does it.

### V2 — WhatsApp-based flow

The restaurant owner messages a dedicated setup bot number. The bot
guides them through onboarding:

1. **Greeting:** The bot introduces itself, asks for the restaurant name
2. **Menu:** The owner sends the menu — as text, a photo, or a PDF
3. **Parsing:** The LLM extracts items, prices, categories, deals into
   the JSON menu format. It validates the structure and shows a preview:
   ```
   Here's what I understood:

   Pizzas:
     Margherita — S:₪40 M:₪55 L:₪65
     Pepperoni — S:₪45 M:₪60 L:₪70
   Sides:
     Garlic Bread — ₪25
   ...

   Does this look right?
   ```
4. **Corrections:** The owner can correct mistakes conversationally:
   "Margherita large is ₪70, not ₪65"
   The LLM updates and re-shows.
5. **Confirm:** Owner confirms. The system writes the menu JSON,
   registers the restaurant in `restaurants.json`, and the restaurant
   is live.
6. **Twilio setup:** The owner provides their Twilio WhatsApp number
   (or the setup bot helps them provision one).

### Challenges

- **Menu parsing accuracy:** The LLM is good at structured extraction
  but will make mistakes. The confirmation loop is critical — the owner
  must review before going live.
- **Photo menu quality:** A clear photo of a printed menu works well.
  Handwritten menus, bad lighting, Hebrew-only menus — success drops.
  Text input is more reliable.
- **One at a time:** The onboarding flow is a stateful conversation
  (like the ordering flow). A separate session for "setup" mode.

### Branch

Core changes on master (menu validation, `manage_menu` tool for creating
restaurants). The WhatsApp conversation flow is channel logic on
twilio-integration.

## 6. Menu Management + Error Recovery

**Status:** Fully designed

### Menu Management

Restaurant owners update their menu via WhatsApp. The LLM interprets
intent and calls a `manage_menu` tool:

| Command | Intent → Tool Call |
|---|---|
| "/86 Margherita" | `manage_menu(action="out_of_stock", item="Margherita")` |
| "Pepperoni is sold out today" | Same — natural language |
| "/price Pepperoni large 60" | `manage_menu(action="set_price", item="Pepperoni", size="large", price=60)` |
| "Add a new side: French Fries ₪18" | `manage_menu(action="add_item", category="Sides", name="French Fries", price=18)` |
| "Margherita is back" | `manage_menu(action="in_stock", item="Margherita")` |

The `manage_menu` tool updates the in-memory `Catalogue` (marks items
unavailable) and persists changes to the menu JSON file. Reloading the
server picks up changes automatically.

Menu JSON files are simple structures — the tool can modify them directly
without database involvement.

### Error Recovery

Three layers of protection against the system losing or corrupting orders.

**LLM failures (transient):**

`process_turn` gets retry logic around the LLM call:

```python
for attempt in range(3):
    try:
        text, tool_calls = llm_client.chat(full_msgs, tool_defs)
        break
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        if attempt < 2 and _is_retryable(e):
            time.sleep(backoff[attempt])  # 1s, 3s, 9s
            continue
        raise
```

Transient: timeout, 503, 429. Permanent: 401, 403, 400 → no retry.

Session is saved before the LLM call (see atomicity below), so a retry
continues from the same state.

**Twilio send failures:**

If `send_whatsapp_message()` fails, the session stays in its current
state. The response text is saved on the session. On the customer's
next message, `process_turn` detects the unsent response and sends it
first before processing the new message.

No background queue, no complex retry infrastructure. Customer messaging
again naturally triggers the re-send.

**Tool execution atomicity (mid-call crashes):**

If any tool in a batch crashes, the entire turn's changes are rolled
back via database reload:

```python
# agent_loop.py — before executing tool batch
session.save(sessions_dir)  # snapshot

try:
    for tc in tool_calls[:5]:
        _execute_tool(session, catalogue, pricing, tc, tool_funcs)
    _apply_transition(session)
except Exception:
    # Reload clean state from DB — discards all mutations from this turn
    session = OrderSession.load(
        session.restaurant_id, session.session_id,
        sessions_dir=sessions_dir, db=db
    )
    raise
```

One extra save per turn creates the snapshot (~1ms in SQLite WAL). The
database guarantees the reload is complete — cart, state, customer,
conversation all roll back to the snapshot point. No shallow-copy bugs,
no partial state.

### Branch

Both menu management and error recovery are 100% core logic — on master.
No channel-specific code involved.

"""FastAPI server — Twilio WhatsApp webhook receiver.

Receives webhook POSTs from Twilio, routes them through the agent loop,
and sends responses back via the Twilio REST API.

Usage:
    python main.py server --port 8080
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time_module
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agent_loop import process_turn
from config import AppConfig
from db import Database
from llm_client import LLMClient
from models import OrderState, OrderType
from payment import create_checkout_session, verify_webhook
from restaurant import RestaurantContext, RestaurantRegistry
from session_router import SessionRouter
from twilio_client import TwilioClient

logger = logging.getLogger(__name__)

# ── Initialization ─────────────────────────────────────────────────────────────

_registry: RestaurantRegistry | None = None
_db: Database | None = None
_llm: LLMClient | None = None
_twilio: TwilioClient | None = None
_router: SessionRouter | None = None
_orders_dir: str = "orders"
_locks: dict[str, asyncio.Lock] = {}
_lock_access: dict[str, float] = {}  # identity → monotonic timestamp

# Empty TwiML — we reply asynchronously via REST
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Session staleness threshold
_STALE_HOURS: float = 2.0

# Lock eviction
_LOCK_TTL_SECONDS: float = 3600.0  # 1 hour
_MAX_LOCKS_BEFORE_SWEEP: int = 1000


def _get_lock(identity: str) -> asyncio.Lock:
    """Return (and lazily create) a per-identity asyncio.Lock."""
    lock = _locks.get(identity)
    if lock is None:
        lock = asyncio.Lock()
        _locks[identity] = lock
    _lock_access[identity] = _time_module.monotonic()
    return lock


def _sweep_stale_locks() -> None:
    """Remove locks not accessed within TTL. Only triggers above threshold."""
    if len(_locks) < _MAX_LOCKS_BEFORE_SWEEP:
        return
    now = _time_module.monotonic()
    stale = [
        phone for phone, ts in _lock_access.items()
        if now - ts > _LOCK_TTL_SECONDS
    ]
    for phone in stale:
        _locks.pop(phone, None)
        _lock_access.pop(phone, None)
    if stale:
        logger.info("Swept %d stale locks (%d remaining)", len(stale), len(_locks))


def _is_session_stale(session) -> bool:
    """True if the session's last update was more than _STALE_HOURS ago."""
    try:
        updated = datetime.fromisoformat(session.updated_at)
    except (ValueError, TypeError):
        return False
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age > _STALE_HOURS * 3600


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Load configuration and initialise all dependencies at startup."""
    global _registry, _db, _llm, _twilio, _router, _orders_dir

    cfg = AppConfig.from_env()

    _registry = RestaurantRegistry(cfg.restaurants_path)
    _db = Database(cfg.db_path)
    _llm = LLMClient(cfg)
    _twilio = TwilioClient(
        account_sid=cfg.twilio_account_sid,
        auth_token=cfg.twilio_auth_token,
        whatsapp_number=cfg.twilio_whatsapp_number,
    )
    _router = SessionRouter(cfg.sessions_dir)
    _orders_dir = cfg.orders_dir

    logger.info(
        "Twilio server started — %d restaurant(s) loaded",
        len(_registry.list_restaurants()),
    )
    yield
    logger.info("Server shutting down")


def _save_order_file(session, restaurant_ctx: RestaurantContext) -> None:
    """Persist a completed order to the restaurant-scoped orders directory."""
    ot = session.customer.order_type or OrderType.PICKUP
    subtotal, delivery_fee, total = restaurant_ctx.pricing.compute_totals(
        session.cart, ot
    )

    payload = {
        "order_id": session.session_id,
        "restaurant_id": session.restaurant_id,
        "restaurant": restaurant_ctx.config.name,
        "items": [item.model_dump() for item in session.cart],
        "customer": session.customer.model_dump(),
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total": total,
        "order_type": ot.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    order_dir = Path(_orders_dir) / session.restaurant_id
    order_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{session.session_id}_{ts}.json"
    filepath = order_dir / filename
    filepath.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Also save to database if available
    if _db is not None:
        _db.save_order(payload)

    logger.info("Order saved: %s", filepath)


def _notify_restaurant(session, restaurant_ctx: RestaurantContext) -> None:
    """Send a WhatsApp notification to the restaurant about a new order."""
    restaurant_phone = restaurant_ctx.config.owner_phone.removeprefix("+")
    items_text = "\n".join(
        f"  {i.quantity}x {i.name}"
        + (f" ({i.size})" if i.size else "")
        + (f" + {', '.join(t.name for t in i.toppings)}" if i.toppings else "")
        for i in session.cart
    )
    ot = session.customer.order_type.value if session.customer.order_type else "pickup"
    name = session.customer.name or "N/A"
    phone = session.customer.phone or "N/A"
    address = session.customer.address or ""

    message = (
        f"🔔 New Order!\n"
        f"Order #{session.session_id}\n"
        f"Customer: {name} ({phone})\n"
        f"Type: {ot}"
    )
    if address:
        message += f"\nAddress: {address}"

    # Compute total
    pricing = restaurant_ctx.pricing
    _, _, total = pricing.compute_totals(
        session.cart,
        session.customer.order_type or OrderType.PICKUP,
    )

    message += (
        f"\n{'─' * 20}\n"
        f"{items_text}\n"
        f"{'─' * 20}\n"
        f"Total: ₪{total:.2f}"
    )

    try:
        _twilio.send_whatsapp_message(restaurant_phone, message)
        logger.info(
            "Restaurant notified for order %s/%s",
            restaurant_ctx.config.id, session.session_id,
        )
    except Exception as e:
        logger.error(
            "Failed to notify restaurant %s: %s",
            restaurant_ctx.config.id, e,
        )


app = FastAPI(title="Order Processing Agent — Twilio", lifespan=_lifespan)

# ── WhatsApp Webhook ───────────────────────────────────────────────────────────

# Endpoint paths that are allowed through signature validation (no trailing
# slash variants, etc.). The validator uses the exact request URL.
@app.post("/whatsapp/webhook")
async def receive_whatsapp(request: Request) -> PlainTextResponse:
    """Receive an incoming WhatsApp message from Twilio, process, respond."""
    # Read form-encoded body
    form_body: bytes = await request.body()

    # ── Guard: server must be initialised ──────────────────────────────
    if _twilio is None:
        logger.error("Twilio client not initialised — server misconfiguration")
        raise HTTPException(status_code=500, detail="Server not configured")

    # ── Signature validation ───────────────────────────────────────────
    flat_params: dict[str, str] = {}
    try:
        parsed = parse_qs(form_body.decode("utf-8"))
        for k, v in parsed.items():
            flat_params[k] = v[0] if len(v) == 1 else v[-1]
    except Exception:
        pass

    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)

    if not TwilioClient.validate_webhook(
        url, flat_params, signature, _twilio.auth_token
    ):
        logger.warning("Invalid Twilio signature for %s", url)
        raise HTTPException(status_code=403, detail="Invalid signature")

    # ── Extract message ───────────────────────────────────────────────────
    extracted = TwilioClient.extract_whatsapp_message(form_body)
    if extracted is None:
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    wa_id, text = extracted
    logger.info("WhatsApp from %s: %s", wa_id[:6], text[:100])

    # ── Route to restaurant by To number ──────────────────────────────────
    to_raw = flat_params.get("To", "")           # e.g. "whatsapp:+14155238886"
    to_clean = to_raw.removeprefix("whatsapp:")   # "+14155238886"
    restaurant_ctx = _registry.get_by_twilio_phone(to_clean)
    if restaurant_ctx is None:
        logger.error("No restaurant configured for To=%s", to_raw)
        raise HTTPException(status_code=500, detail="Unknown restaurant")

    assert _twilio is not None
    assert _router is not None
    assert _registry is not None
    assert _llm is not None

    # ── Process (offloaded to thread to avoid blocking the event loop) ──
    session = _router.get_or_create(restaurant_ctx.config.id, wa_id, db=_db)
    sessions_dir = f"{_router.sessions_dir}/{restaurant_ctx.config.id}"
    lock_key = f"{restaurant_ctx.config.id}:{wa_id}"
    async with _get_lock(lock_key):
        try:
            if _is_session_stale(session):
                text = (
                    f"[The customer had an unfinished order from "
                    f"{session.updated_at}. Ask if they want to continue "
                    f"or start fresh before proceeding.]\n\n{text}"
                )

            response = await asyncio.to_thread(
                process_turn,
                session, text,
                restaurant_ctx.catalogue, restaurant_ctx.pricing, _llm,
                sessions_dir=sessions_dir,
            )
            session.save(sessions_dir)

            # Payment link generation — if PAYMENT_PENDING, create Stripe URL
            if session.state == OrderState.PAYMENT_PENDING:
                payment_url = create_checkout_session(
                    session_id=session.session_id,
                    restaurant_id=restaurant_ctx.config.id,
                    restaurant_name=restaurant_ctx.config.name,
                    items=[item.model_dump() for item in session.cart],
                    total=restaurant_ctx.pricing.compute_totals(
                        session.cart,
                        session.customer.order_type or OrderType.PICKUP,
                    )[2],
                )
                response = f"Pay here to confirm your order: {payment_url}"

            if session.is_complete:
                _save_order_file(session, restaurant_ctx)
                # Notify restaurant via WhatsApp
                _notify_restaurant(session, restaurant_ctx)
        except Exception as e:
            logger.error("process_turn failed for %s: %s", wa_id[:6], e)
            response = (
                "Sorry, something went wrong while processing your order. "
                "Please try again in a moment."
            )

    # ── Reply (also in thread — Twilio REST is blocking) ────────────────
    if response.strip():
        try:
            await asyncio.to_thread(
                _twilio.send_whatsapp_message, wa_id, response
            )
        except Exception as e:
            logger.error(
                "Failed to send WhatsApp message to %s: %s", wa_id[:6], e
            )

    _sweep_stale_locks()
    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")


# ── Payment Webhook ────────────────────────────────────────────────────────────


@app.post("/payment/webhook")
async def receive_payment(request: Request):
    """Receive Stripe webhook events — payment completion."""
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = verify_webhook(payload, sig_header)
    except Exception:
        logger.warning("Invalid Stripe webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event.get("type") != "checkout.session.completed":
        return {"status": "ignored"}

    # Extract session identity from metadata
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {})
    restaurant_id = metadata.get("restaurant_id", "")
    session_id = metadata.get("session_id", "")

    if not restaurant_id or not session_id:
        logger.error("Stripe webhook missing metadata: %s", metadata)
        raise HTTPException(status_code=400, detail="Missing order metadata")

    # Load session and complete
    session = _router.get_or_create(restaurant_id, session_id, db=_db)
    if session.state == OrderState.COMPLETED:
        return {"status": "already_completed"}  # idempotent

    session.state = OrderState.COMPLETED
    session.save()

    # Send confirmation via WhatsApp
    await asyncio.to_thread(
        _twilio.send_whatsapp_message,
        session.session_id,
        "Payment received! Your order is confirmed.",
    )

    logger.info(
        "Payment completed for %s/%s", restaurant_id, session_id,
    )
    return {"status": "ok"}


# ── Printer Agent API ──────────────────────────────────────────────────────────


def _check_printer_token(token: str) -> bool:
    """Validate the printer agent API token."""
    expected = os.environ.get("API_TOKEN", "")
    return bool(expected) and token == expected


@app.get("/api/orders")
async def get_unprinted_orders(request: Request):
    """Return unprinted orders for a restaurant. Used by the printer agent."""
    restaurant_id = request.query_params.get("restaurant_id", "")
    token = request.query_params.get("token", "")

    if not _check_printer_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")

    if _db is None:
        return {"orders": []}

    orders = _db.get_unprinted_orders(restaurant_id)
    return {"orders": orders}


@app.post("/api/orders/{order_id}/printed")
async def mark_order_printed(order_id: str, request: Request):
    """Mark an order as printed. Used by the printer agent after printing."""
    token = request.query_params.get("token", "")

    if not _check_printer_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")

    if _db is not None:
        _db.mark_printed(order_id)

    return {"status": "ok"}

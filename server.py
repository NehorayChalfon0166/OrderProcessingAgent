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
import re
import time as _time_module
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from agent_loop import process_turn
from config import AppConfig
from db import Database
from llm_client import LLMClient
from models import OrderState, OrderType
from payment import create_checkout_session, verify_webhook
from restaurant import RestaurantContext, RestaurantRegistry
from session_router import SessionRouter
from twilio_client import TwilioClient
from voice_handler import (
    build_error_twiml,
    build_goodbye_twiml,
    build_reply_twiml,
    build_welcome_twiml,
)

logger = logging.getLogger(__name__)

# ── Module-level state (initialised in lifespan) ──────────────────────────

_registry: RestaurantRegistry | None = None
_db: Database | None = None
_llm: LLMClient | None = None
_twilio: TwilioClient | None = None
_router: SessionRouter | None = None
_orders_dir: str = "orders"
_api_token: str = ""
_cfg: AppConfig | None = None
_locks: dict[str, asyncio.Lock] = {}
_lock_access: dict[str, float] = {}
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Rate limiting
_rate_window: dict[str, list[float]] = {}

# Idempotency — recently processed Twilio MessageSids
_processed_sids: dict[str, float] = {}
_MAX_SIDS = 10000

# Metrics
_metrics: dict[str, int] = {
    "orders_today": 0, "llm_calls": 0, "llm_errors": 0,
    "webhooks_received": 0, "webhooks_throttled": 0, "webhooks_duplicate": 0,
}
_metrics_lock = asyncio.Lock()

# Input validation
_MAX_MESSAGE_LENGTH = 2000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ── Helpers ───────────────────────────────────────────────────────────────

def _flatten_form(form_body: bytes) -> dict[str, str]:
    """Parse form-encoded POST body into a flat string dict."""
    flat: dict[str, str] = {}
    try:
        parsed = parse_qs(form_body.decode("utf-8"))
        for k, v in parsed.items():
            flat[k] = v[0] if len(v) == 1 else v[-1]
    except Exception:
        pass
    return flat


def _check_rate_limit(phone: str) -> bool:
    if _cfg is None:
        return True
    now = _time_module.monotonic()
    timestamps = [t for t in _rate_window.get(phone, []) if now - t < 60.0]
    if len(timestamps) >= _cfg.rate_limit_max_per_minute:
        _rate_window[phone] = timestamps
        return False
    timestamps.append(now)
    _rate_window[phone] = timestamps
    return True


def _validate_message(text: str) -> str:
    """Sanitise and validate incoming WhatsApp message text."""
    text = text.strip()[: _MAX_MESSAGE_LENGTH]
    text = _CONTROL_CHARS.sub("", text)
    return text


def _is_duplicate(message_sid: str) -> bool:
    """Return True if this MessageSid was already processed."""
    now = _time_module.monotonic()
    # Purge expired entries (1hr TTL) in place
    expired = [k for k, v in _processed_sids.items() if now - v > 3600]
    for k in expired:
        del _processed_sids[k]
    if message_sid in _processed_sids:
        return True
    if len(_processed_sids) > _MAX_SIDS:
        oldest = min(_processed_sids, key=lambda k: _processed_sids[k])
        del _processed_sids[oldest]
    _processed_sids[message_sid] = now
    return False


async def _inc_metric(key: str) -> None:
    async with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + 1


def _get_lock(identity: str) -> asyncio.Lock:
    lock = _locks.get(identity)
    if lock is None:
        lock = asyncio.Lock()
        _locks[identity] = lock
    _lock_access[identity] = _time_module.monotonic()
    return lock


def _sweep_stale_locks() -> None:
    threshold = 1000
    ttl = _cfg.lock_ttl_seconds if _cfg else 3600.0
    if len(_locks) < threshold:
        return
    now = _time_module.monotonic()
    stale = [p for p, ts in _lock_access.items() if now - ts > ttl]
    for phone in stale:
        _locks.pop(phone, None)
        _lock_access.pop(phone, None)
    if stale:
        logger.info("Swept %d stale locks (%d remaining)", len(stale), len(_locks))


def _is_session_stale(session) -> bool:
    stale_hours = _cfg.session_stale_hours if _cfg else 2.0
    try:
        updated = datetime.fromisoformat(session.updated_at)
    except (ValueError, TypeError):
        return False
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age > stale_hours * 3600


def _build_hints(catalogue) -> str:
    """Build comma-separated STT hints from top-level menu item names."""
    items: list[str] = []
    for cat in catalogue.menu_data.get("categories", []):
        for item in cat.get("items", []):
            name = item.get("name", "")
            if name:
                items.append(name)
    return ", ".join(items[:30])


# ── Lifespan ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global _registry, _db, _llm, _twilio, _router, _orders_dir, _api_token, _cfg

    cfg = AppConfig.from_env()
    _cfg = cfg
    _registry = RestaurantRegistry(cfg.restaurants_path)
    _db = Database(cfg.db_path)
    _llm = LLMClient(cfg)
    _twilio = TwilioClient(
        account_sid=cfg.twilio_account_sid,
        auth_token=cfg.twilio_auth_token,
        whatsapp_number=cfg.twilio_whatsapp_number,
    )
    _router = SessionRouter()
    _orders_dir = cfg.orders_dir
    _api_token = cfg.api_token

    if not cfg.stripe_secret_key or not cfg.stripe_webhook_secret:
        logger.warning(
            "Stripe keys not set — online payment disabled."
        )

    # Background session cleanup task
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(21600)  # every 6 hours
            if _db is not None and _cfg is not None:
                try:
                    deleted = _db.cleanup_old_sessions(_cfg.session_cleanup_days)
                    if deleted:
                        logger.info("Session cleanup: removed %d old sessions", deleted)
                except Exception as e:
                    logger.warning("Session cleanup failed: %s", e)

    cleanup_task = asyncio.create_task(_cleanup_loop())

    logger.info(
        "Twilio server started — %d restaurant(s) loaded",
        len(_registry.list_restaurants()),
    )
    yield
    cleanup_task.cancel()
    logger.info("Server shutting down")


def _save_order_file(session, restaurant_ctx: RestaurantContext) -> str:
    if _db is not None:
        return _db.persist_completed_order(
            session, restaurant_ctx.pricing,
            restaurant_ctx.config.name, _orders_dir,
        )
    logger.error("Database not initialised — cannot save order")
    return ""


def _notify_restaurant(session, restaurant_ctx: RestaurantContext, order_id: str = "") -> None:
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

    order_ref = order_id or session.session_id
    message = (
        f"🔔 New Order!\nOrder #{order_ref}\n"
        f"Customer: {name} ({phone})\nType: {ot}"
    )
    if address:
        message += f"\nAddress: {address}"

    pricing = restaurant_ctx.pricing
    _, _, total = pricing.compute_totals(
        session.cart, session.customer.order_type or OrderType.PICKUP,
    )
    message += f"\n{'─' * 20}\n{items_text}\n{'─' * 20}\nTotal: ₪{total:.2f}"

    try:
        _twilio.send_whatsapp_message(restaurant_phone, message)
        logger.info("Restaurant notified for %s/%s", restaurant_ctx.config.id, session.session_id)
    except Exception as e:
        logger.error("Failed to notify restaurant %s: %s", restaurant_ctx.config.id, e)


app = FastAPI(title="Order Processing Agent — Twilio", lifespan=_lifespan)


@app.middleware("http")
async def _add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ── Health ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    checks: dict[str, str] = {}
    healthy = True
    if _db is not None:
        try:
            _db._db.execute_sql("SELECT 1")
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {e}"
            healthy = False
    else:
        checks["database"] = "not_initialised"
        healthy = False
    checks["llm"] = "ok" if _llm is not None else "not_initialised"
    if _llm is None:
        healthy = False
    checks["restaurants"] = str(len(_registry.list_restaurants())) if _registry else "0"
    status_code = 200 if healthy else 503
    return JSONResponse(
        content={"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=status_code,
    )


# ── Metrics ───────────────────────────────────────────────────────────────


@app.get("/metrics")
async def metrics(request: Request):
    """Expose internal metrics as JSON."""
    token = request.query_params.get("token", "")
    if not _api_token or token != _api_token:
        raise HTTPException(status_code=403, detail="Invalid token")
    async with _metrics_lock:
        return dict(_metrics)


# ── WhatsApp Webhook ──────────────────────────────────────────────────────


@app.post("/whatsapp/webhook")
async def receive_whatsapp(request: Request) -> PlainTextResponse:
    form_body: bytes = await request.body()
    await _inc_metric("webhooks_received")

    if _twilio is None:
        logger.error("Twilio client not initialised")
        raise HTTPException(status_code=500, detail="Server not configured")

    # Signature validation
    flat_params: dict[str, str] = {}
    try:
        parsed = parse_qs(form_body.decode("utf-8"))
        for k, v in parsed.items():
            flat_params[k] = v[0] if len(v) == 1 else v[-1]
    except Exception:
        pass

    signature = request.headers.get("X-Twilio-Signature", "")
    if not TwilioClient.validate_webhook(
        str(request.url), flat_params, signature, _twilio.auth_token
    ):
        logger.warning("Invalid Twilio signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Idempotency — skip duplicate webhook deliveries
    message_sid = flat_params.get("MessageSid", "")
    if message_sid and _is_duplicate(message_sid):
        await _inc_metric("webhooks_duplicate")
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    # Extract message
    extracted = TwilioClient.extract_whatsapp_message(form_body)
    if extracted is None:
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    wa_id, text = extracted
    text = _validate_message(text)
    if not text:
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")
    logger.info("WhatsApp from %s: %s", wa_id[:6], text[:100])

    # Rate limit
    if not _check_rate_limit(wa_id):
        await _inc_metric("webhooks_throttled")
        logger.warning("Rate limit hit for %s", wa_id[:6])
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    # Route to restaurant
    to_raw = flat_params.get("To", "")
    to_clean = to_raw.removeprefix("whatsapp:")
    restaurant_ctx = _registry.get_by_twilio_phone(to_clean)
    if restaurant_ctx is None:
        logger.error("No restaurant for To=%s", to_raw)
        raise HTTPException(status_code=500, detail="Unknown restaurant")

    if _twilio is None or _router is None or _registry is None or _llm is None:
        raise HTTPException(status_code=500, detail="Server not initialised")

    # Process
    session = _router.get_or_create(restaurant_ctx.config.id, wa_id, db=_db)
    lock_key = f"{restaurant_ctx.config.id}:{wa_id}"
    async with _get_lock(lock_key):
        try:
            if _is_session_stale(session):
                text = (
                    f"[The customer had an unfinished order from "
                    f"{session.updated_at}. Ask if they want to continue "
                    f"or start fresh before proceeding.]\n\n{text}"
                )

            timeout = _cfg.agent_timeout_seconds if _cfg else 45.0
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    process_turn,
                    session, text,
                    restaurant_ctx.catalogue, restaurant_ctx.pricing, _llm,
                    _cfg.max_iterations if _cfg else 5,
                ),
                timeout=timeout,
            )
            session.save()
            await _inc_metric("llm_calls")

            if session.state == OrderState.PAYMENT_PENDING:
                if not (_cfg and _cfg.stripe_secret_key and _cfg.stripe_webhook_secret):
                    logger.warning("PAYMENT_PENDING but Stripe not configured — cannot create payment link")
                    response = "Sorry, online payment is not available. Pay cash on delivery."
                else:
                    base_url = f"{request.url.scheme}://{request.url.netloc}"
                    payment_url = create_checkout_session(
                        session_id=session.session_id,
                        restaurant_id=restaurant_ctx.config.id,
                        restaurant_name=restaurant_ctx.config.name,
                        items=[item.model_dump() for item in session.cart],
                        total=restaurant_ctx.pricing.compute_totals(
                            session.cart,
                            session.customer.order_type or OrderType.PICKUP,
                        )[2],
                        success_url=f"{base_url}/payment/success",
                        cancel_url=f"{base_url}/payment/cancel",
                    )
                    response = f"Pay here to confirm your order: {payment_url}"

            if session.is_complete:
                order_id = _save_order_file(session, restaurant_ctx)
                _notify_restaurant(session, restaurant_ctx, order_id)
                await _inc_metric("orders_today")
        except asyncio.TimeoutError:
            await _inc_metric("llm_errors")
            logger.error("Agent timeout for %s", wa_id[:6])
            response = "Sorry, processing took too long. Please try again."
        except Exception as e:
            await _inc_metric("llm_errors")
            logger.error("process_turn failed for %s: %s", wa_id[:6], e)
            response = (
                "Sorry, something went wrong while processing your order. "
                "Please try again in a moment."
            )

    # Reply
    if response.strip():
        try:
            await asyncio.to_thread(_twilio.send_whatsapp_message, wa_id, response)
        except Exception as e:
            logger.error("Failed to send to %s: %s", wa_id[:6], e)

    _sweep_stale_locks()
    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")


# ── Voice Webhooks ──────────────────────────────────────────────────────────


@app.post("/voice/incoming")
async def voice_incoming(request: Request) -> PlainTextResponse:
    """Handle an incoming phone call — greet the caller and listen."""
    if _twilio is None or _registry is None:
        raise HTTPException(status_code=500, detail="Server not configured")

    # Parse form data
    form_body: bytes = await request.body()
    flat_params: dict[str, str] = _flatten_form(form_body)

    # Signature validation
    signature = request.headers.get("X-Twilio-Signature", "")
    if not TwilioClient.validate_webhook(
        str(request.url), flat_params, signature, _twilio.auth_token
    ):
        logger.warning("Invalid Twilio signature on /voice/incoming")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Route by dialed number
    dialed = flat_params.get("To", "").strip()
    restaurant_ctx = _registry.get_by_voice_phone(dialed)
    if restaurant_ctx is None:
        logger.warning("No restaurant for voice To=%s", dialed)
        return PlainTextResponse(
            build_error_twiml("Sorry, this number is not configured for ordering."),
            media_type="application/xml",
        )

    hints = _build_hints(restaurant_ctx.catalogue)
    action_url = f"/voice/speech-result?restaurant={restaurant_ctx.config.id}"
    greeting = (
        f"Welcome to {restaurant_ctx.config.name}! "
        "What would you like to order?"
    )

    twiml = build_welcome_twiml(action_url, greeting, hints)
    logger.info(
        "Voice call from %s to %s (%s)",
        flat_params.get("From", "?"), dialed, restaurant_ctx.config.id,
    )
    return PlainTextResponse(twiml, media_type="application/xml")


@app.post("/voice/speech-result")
async def voice_speech_result(request: Request) -> PlainTextResponse:
    """Receive transcribed speech from Twilio and run the agent loop."""
    if _twilio is None or _registry is None or _router is None or _llm is None:
        raise HTTPException(status_code=500, detail="Server not initialised")

    # Parse form data
    form_body: bytes = await request.body()
    flat_params: dict[str, str] = _flatten_form(form_body)

    # Signature validation
    signature = request.headers.get("X-Twilio-Signature", "")
    if not TwilioClient.validate_webhook(
        str(request.url), flat_params, signature, _twilio.auth_token
    ):
        logger.warning("Invalid Twilio signature on /voice/speech-result")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Extract fields
    speech_result = flat_params.get("SpeechResult", "").strip()
    confidence_str = flat_params.get("Confidence", "")
    caller_id = flat_params.get("From", "").strip()

    # Restaurant routing via query param (set in <Gather action="...">)
    restaurant_id = request.query_params.get("restaurant", "")
    restaurant_ctx = _registry.get_by_id(restaurant_id)
    if restaurant_ctx is None:
        logger.error("Unknown restaurant '%s' in voice speech result", restaurant_id)
        return PlainTextResponse(
            build_error_twiml("Sorry, something went wrong. Please call back."),
            media_type="application/xml",
        )

    hints = _build_hints(restaurant_ctx.catalogue)
    action_url = f"/voice/speech-result?restaurant={restaurant_id}"

    # Handle empty or low-confidence speech.
    # Confidence is not guaranteed to be present — default to neutral trust.
    if confidence_str:
        try:
            confidence = float(confidence_str)
        except (ValueError, TypeError):
            confidence = 0.5
    else:
        confidence = 0.5

    if not speech_result or confidence < 0.2:
        logger.info(
            "Low-confidence speech (%.2f) from %s, retrying", confidence, caller_id,
        )
        return PlainTextResponse(
            build_reply_twiml(
                "I didn't quite catch that. Could you repeat your order?",
                action_url, hints,
            ),
            media_type="application/xml",
        )

    logger.info("Voice speech from %s: %s", caller_id[:6], speech_result[:100])

    # Rate limit
    if not _check_rate_limit(caller_id):
        await _inc_metric("webhooks_throttled")
        logger.warning("Voice rate limit hit for %s", caller_id[:6])
        return PlainTextResponse(
            build_error_twiml("Too many requests. Please call back later."),
            media_type="application/xml",
        )

    # Process
    session = _router.get_or_create(restaurant_id, caller_id, db=_db)
    lock_key = f"{restaurant_id}:{caller_id}"
    async with _get_lock(lock_key):
        try:
            if _is_session_stale(session):
                speech_result = (
                    f"[The customer had an unfinished order from "
                    f"{session.updated_at}. Ask if they want to continue "
                    f"or start fresh before proceeding.]\n\n{speech_result}"
                )

            voice_timeout = min(_cfg.agent_timeout_seconds, 12.0) if _cfg else 12.0
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    process_turn,
                    session, speech_result,
                    restaurant_ctx.catalogue, restaurant_ctx.pricing, _llm,
                    _cfg.max_iterations if _cfg else 5,
                ),
                timeout=voice_timeout,
            )
            session.save()
            await _inc_metric("llm_calls")

            # Voice orders are always cash — no payment link generation
            if session.state == OrderState.PAYMENT_PENDING:
                session.state = OrderState.COMPLETED
                session.save()

            if session.is_complete:
                order_id = _save_order_file(session, restaurant_ctx)
                _notify_restaurant(session, restaurant_ctx, order_id)
                await _inc_metric("orders_today")
                twiml = build_goodbye_twiml(response)
            else:
                twiml = build_reply_twiml(response, action_url, hints)

        except asyncio.TimeoutError:
            await _inc_metric("llm_errors")
            logger.error("Voice agent timeout for %s", caller_id[:6])
            twiml = build_error_twiml(
                "Sorry, processing took too long. Please call back to try again."
            )
        except Exception as e:
            await _inc_metric("llm_errors")
            logger.error("process_turn failed for voice %s: %s", caller_id[:6], e)
            twiml = build_error_twiml(
                "Sorry, something went wrong while processing your order. "
                "Please call back in a moment."
            )

    _sweep_stale_locks()
    return PlainTextResponse(twiml, media_type="application/xml")


# ── Payment Webhook ───────────────────────────────────────────────────────


@app.post("/payment/webhook")
async def receive_payment(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = verify_webhook(payload, sig_header)
    except Exception:
        logger.warning("Invalid Stripe webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event.get("type") != "checkout.session.completed":
        return {"status": "ignored"}

    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {})
    restaurant_id = metadata.get("restaurant_id", "")
    session_id = metadata.get("session_id", "")

    if not restaurant_id or not session_id:
        logger.error("Stripe webhook missing metadata")
        raise HTTPException(status_code=400, detail="Missing order metadata")

    if _registry is None or _twilio is None or _router is None:
        raise HTTPException(status_code=500, detail="Server not initialised")

    restaurant_ctx = _registry.get_by_id(restaurant_id)
    if restaurant_ctx is None:
        raise HTTPException(status_code=400, detail="Unknown restaurant")

    session = _router.get_or_create(restaurant_id, session_id, db=_db)
    if session.state == OrderState.COMPLETED:
        return {"status": "already_completed"}

    session.state = OrderState.COMPLETED
    session.save()

    order_id = _save_order_file(session, restaurant_ctx)
    _notify_restaurant(session, restaurant_ctx, order_id)

    await asyncio.to_thread(
        _twilio.send_whatsapp_message,
        session.session_id,
        "Payment received! Your order is confirmed.",
    )
    logger.info("Payment completed for %s/%s", restaurant_id, session_id)
    return {"status": "ok"}


# ── Printer Agent API ─────────────────────────────────────────────────────


def _check_printer_token(token: str) -> bool:
    return bool(_api_token) and token == _api_token


@app.get("/api/orders")
async def get_unprinted_orders(request: Request):
    restaurant_id = request.query_params.get("restaurant_id", "")
    token = request.query_params.get("token", "")
    if not _check_printer_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")
    if _db is None:
        return {"orders": []}
    return {"orders": _db.get_unprinted_orders(restaurant_id)}


@app.post("/api/orders/{order_id}/printed")
async def mark_order_printed(order_id: str, request: Request):
    token = request.query_params.get("token", "")
    if not _check_printer_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")
    if _db is not None:
        _db.mark_printed(order_id)
    return {"status": "ok"}


# ── Payment UX Pages ──────────────────────────────────────────────────────


@app.get("/payment/success")
async def payment_success():
    return PlainTextResponse(
        "Payment successful! You can return to WhatsApp now.",
        media_type="text/plain",
    )


@app.get("/payment/cancel")
async def payment_cancel():
    return PlainTextResponse(
        "Payment cancelled. Return to WhatsApp to continue your order.",
        media_type="text/plain",
    )

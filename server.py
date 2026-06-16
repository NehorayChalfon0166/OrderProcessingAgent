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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agent_loop import process_turn
from catalogue import Catalogue
from config import AppConfig
from llm_client import LLMClient
from models import OrderType
from pricing import PricingEngine
from session_router import SessionRouter
from twilio_client import TwilioClient

logger = logging.getLogger(__name__)

# ── Initialization ─────────────────────────────────────────────────────────────

_catalogue: Catalogue | None = None
_pricing: PricingEngine | None = None
_llm: LLMClient | None = None
_twilio: TwilioClient | None = None
_router: SessionRouter | None = None
_orders_dir: str = "orders"
_locks: dict[str, asyncio.Lock] = {}

# Empty TwiML — we reply asynchronously via REST
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Session staleness threshold — if a session hasn't been touched in this many
# hours, the agent asks whether to continue or start fresh.
_STALE_HOURS: float = 2.0


def _get_lock(identity: str) -> asyncio.Lock:
    """Return (and lazily create) a per-identity asyncio.Lock."""
    lock = _locks.get(identity)
    if lock is None:
        lock = asyncio.Lock()
        _locks[identity] = lock
    return lock


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
    global _catalogue, _pricing, _llm, _twilio, _router, _orders_dir

    cfg = AppConfig.from_env()

    _catalogue = Catalogue(cfg.menu_path)
    _pricing = PricingEngine(_catalogue.menu_data)
    _llm = LLMClient(cfg)
    _twilio = TwilioClient(
        account_sid=cfg.twilio_account_sid,
        auth_token=cfg.twilio_auth_token,
        whatsapp_number=cfg.twilio_whatsapp_number,
    )
    _router = SessionRouter(cfg.sessions_dir)
    _orders_dir = cfg.orders_dir

    logger.info("Twilio server started — menu loaded, WhatsApp sandbox ready")
    yield
    logger.info("Server shutting down")


def _save_order_file(session) -> None:
    """Persist a completed order to the orders directory."""
    assert _catalogue is not None
    assert _pricing is not None

    ot = session.customer.order_type or OrderType.PICKUP
    subtotal, delivery_fee, total = _pricing.compute_totals(session.cart, ot)

    payload = {
        "order_id": session.session_id,
        "restaurant": _catalogue.restaurant_name,
        "items": [item.model_dump() for item in session.cart],
        "customer": session.customer.model_dump(),
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total": total,
        "order_type": ot.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    Path(_orders_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"order_{session.session_id}_{ts}.json"
    filepath = Path(_orders_dir) / filename
    filepath.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Order saved: %s", filepath)


app = FastAPI(title="Order Processing Agent — Twilio", lifespan=_lifespan)

# ── WhatsApp Webhook ───────────────────────────────────────────────────────────

# Endpoint paths that are allowed through signature validation (no trailing
# slash variants, etc.). The validator uses the exact request URL.
@app.post("/whatsapp/webhook")
async def receive_whatsapp(request: Request) -> PlainTextResponse:
    """Receive an incoming WhatsApp message from Twilio, process, respond."""
    # Read form-encoded body
    form_body: bytes = await request.body()

    # ── Signature validation ──────────────────────────────────────────────
    # Parse form params into a flat dict for the validator
    flat_params: dict[str, str] = {}
    try:
        parsed = parse_qs(form_body.decode("utf-8"))
        for k, v in parsed.items():
            flat_params[k] = v[0] if len(v) == 1 else v[-1]
    except Exception:
        pass

    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)

    if _twilio is not None and not TwilioClient.validate_webhook(
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

    assert _twilio is not None
    assert _router is not None
    assert _catalogue is not None
    assert _pricing is not None
    assert _llm is not None

    # ── Process ───────────────────────────────────────────────────────────
    session = _router.get_or_create(wa_id)
    async with _get_lock(wa_id):
        try:
            # If the session is stale, prepend a context note so the LLM
            # asks whether to continue or start fresh.
            if _is_session_stale(session):
                text = (
                    f"[The customer had an unfinished order from "
                    f"{session.updated_at}. Ask if they want to continue "
                    f"or start fresh before proceeding.]\n\n{text}"
                )

            response = process_turn(session, text, _catalogue, _pricing, _llm)
            session.save(_router.sessions_dir)
            if session.is_complete:
                _save_order_file(session)
        except Exception as e:
            logger.error("process_turn failed for %s: %s", wa_id[:6], e)
            response = (
                "Sorry, something went wrong while processing your order. "
                "Please try again in a moment."
            )

    # ── Reply ─────────────────────────────────────────────────────────────
    if response.strip():
        try:
            _twilio.send_whatsapp_message(wa_id, response)
        except Exception as e:
            logger.error("Failed to send WhatsApp message to %s: %s", wa_id[:6], e)

    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

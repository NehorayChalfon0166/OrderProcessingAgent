"""Stripe payment integration — checkout sessions and webhook verification.

STATUS: DORMANT. Stripe does not currently operate in Israel. Online payment
is disabled — the server warns on startup and only "cash" payment method
is available. This module is kept for future expansion.

When Stripe becomes available or you expand to supported markets, set
STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET in .env to enable it.

For Israel online payment, the leading alternative is Grow (formerly Meshulam):
  https://grow.business
  API docs: https://grow-il.readme.io
  Key API: createPaymentLink — generates a URL to send via WhatsApp
  Requirements: business registration (עוסק מורשה or עוסק פטור)

Architecture (when active):
- create_checkout_session() is called by the channel layer (server.py) after
  process_turn() returns PAYMENT_PENDING. It generates a Stripe-hosted
  payment page URL that gets sent to the customer via WhatsApp.
- verify_webhook() is called by POST /payment/webhook to validate
  incoming Stripe events and extract the session identity.

Both functions are pure stateless logic — no database access, no session
manipulation. The channel layer handles all I/O.
"""

from __future__ import annotations

import logging
import os

import stripe

logger = logging.getLogger(__name__)


def create_checkout_session(
    session_id: str,
    restaurant_id: str,
    restaurant_name: str,
    items: list[dict],
    total: float,
    currency: str = "ils",
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> str:
    """Create a Stripe Checkout Session and return the payment URL.

    The URL is sent to the customer via WhatsApp. Stripe hosts the
    payment page — no frontend needed.

    Args:
        session_id: The order's session ID (customer phone digits).
        restaurant_id: Restaurant slug (e.g. "marios_pizzeria").
        restaurant_name: Display name for the Stripe receipt.
        items: List of cart item dicts with 'name', 'quantity',
               'line_total' keys.
        total: Order total in the currency's main unit (e.g. shekels).
        currency: ISO currency code (default "ils").
        success_url: Optional URL Stripe redirects to after successful
            payment. If omitted, Stripe shows a generic completion page.
        cancel_url: Optional URL Stripe redirects to if the customer
            cancels. If omitted, Stripe shows a generic page.

    Returns:
        The Stripe Checkout URL the customer should visit to pay.
    """
    stripe.api_key = _get_secret_key()

    line_items = []
    for item in items:
        unit_amount = int(round(item["line_total"] / item.get("quantity", 1) * 100))
        line_items.append({
            "price_data": {
                "currency": currency,
                "product_data": {
                    "name": str(item.get("name", "Item")),
                },
                "unit_amount": unit_amount,
            },
            "quantity": item.get("quantity", 1),
        })

    kwargs: dict = {
        "mode": "payment",
        "line_items": line_items,
        "metadata": {
            "session_id": session_id,
            "restaurant_id": restaurant_id,
        },
    }
    if success_url:
        kwargs["success_url"] = success_url
    if cancel_url:
        kwargs["cancel_url"] = cancel_url

    checkout = stripe.checkout.Session.create(**kwargs)

    logger.info(
        "Created Stripe session %s for order %s/%s — %s",
        checkout.id, restaurant_id, session_id, checkout.url,
    )
    return checkout.url


def verify_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify a Stripe webhook signature and return the event data.

    Args:
        payload: Raw request body bytes.
        sig_header: Value of the Stripe-Signature header.

    Returns:
        The verified Stripe event dict.

    Raises:
        stripe.error.SignatureVerificationError: If the signature is invalid.
    """
    stripe.api_key = _get_secret_key()
    event = stripe.Webhook.construct_event(
        payload, sig_header, _get_webhook_secret(),
    )
    return event  # type: ignore[return-value]


def _get_secret_key() -> str:
    """Return the Stripe secret key from environment."""
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise ValueError(
            "STRIPE_SECRET_KEY is not set. "
            "Add it to .env or as an environment variable."
        )
    return key


def _get_webhook_secret() -> str:
    """Return the Stripe webhook signing secret from environment."""
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise ValueError(
            "STRIPE_WEBHOOK_SECRET is not set. "
            "Add it to .env or as an environment variable."
        )
    return secret

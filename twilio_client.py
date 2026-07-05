"""Twilio client — WhatsApp messaging + webhook signature validation.

Wraps the Twilio REST API for sending WhatsApp messages and provides
static helpers for parsing form-encoded webhook payloads and validating
X-Twilio-Signature headers.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

from twilio.request_validator import RequestValidator
from twilio.rest import Client

logger = logging.getLogger(__name__)


class TwilioClient:
    """Thin wrapper around the Twilio REST API for WhatsApp messaging."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        whatsapp_number: str,
    ) -> None:
        self.auth_token = auth_token
        self._client = Client(account_sid, auth_token)
        self._from = f"whatsapp:{whatsapp_number}"

    # ── Send ────────────────────────────────────────────────────────────────

    def send_whatsapp_message(self, to: str, text: str) -> str:
        """Send a free-form WhatsApp message via Twilio.

        Args:
            to: Recipient phone number (digits only, e.g. "972539534345").
            text: Message body.

        Returns:
            The Twilio message SID on success.

        Raises:
            Exception: If the Twilio API returns an error.
        """
        # Strip any existing "whatsapp:" prefix to avoid double-wrapping
        clean_to = to.removeprefix("whatsapp:")
        msg = self._client.messages.create(
            body=text,
            from_=self._from,
            to=f"whatsapp:{clean_to}",
        )
        logger.debug("Sent message %s to %s", msg.sid, clean_to[:6])
        return msg.sid

    # ── Webhook Validation ──────────────────────────────────────────────────

    @staticmethod
    def validate_webhook(
        url: str,
        params: dict[str, str],
        signature: str,
        auth_token: str,
    ) -> bool:
        """Validate that a webhook POST genuinely came from Twilio.

        Args:
            url: The full URL Twilio POSTed to.
            params: The form-encoded POST parameters (as a flat dict).
            signature: The value of the X-Twilio-Signature header.
            auth_token: Your Twilio Auth Token.

        Returns:
            True if the signature matches.
        """
        validator = RequestValidator(auth_token)
        return validator.validate(url, params, signature)

    # ── Payload Parsing ─────────────────────────────────────────────────────

    @staticmethod
    def extract_whatsapp_message(
        form_body: bytes | str,
    ) -> tuple[str, str] | None:
        """Extract the WhatsApp ID and message text from a Twilio webhook.

        Twilio sends ``application/x-www-form-urlencoded`` POSTs. Key fields:

        * ``WaId`` — sender's WhatsApp ID (digits only)
        * ``Body`` — message text
        * ``NumMedia`` — if > 0, the message has media attachments (skipped)

        Args:
            form_body: Raw form-encoded POST body bytes or string.

        Returns:
            A (wa_id, message_text) tuple, or None if it's not a text message.
        """
        if isinstance(form_body, bytes):
            form_body = form_body.decode("utf-8")

        parsed = parse_qs(form_body)

        # parse_qs returns {key: [value, ...]} — flatten single-value fields
        flat: dict[str, str] = {}
        for k, v in parsed.items():
            flat[k] = v[0] if len(v) == 1 else v[-1]

        wa_id = flat.get("WaId", "")
        body = flat.get("Body", "")
        num_media = int(flat.get("NumMedia", "0"))

        if num_media > 0:
            logger.debug("Skipping media message from %s", wa_id)
            return None

        if not wa_id or not body:
            return None

        return wa_id, body

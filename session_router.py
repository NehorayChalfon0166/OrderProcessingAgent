"""Session router — maps phone numbers to persistent order sessions.

Each customer gets one active session per restaurant at a time, keyed by
the composite (restaurant_id, phone_number). The session_id is the sanitized
phone number. Sessions are persisted via SQLite Database.
"""

from __future__ import annotations

import logging
import re

from db import Database
from models import OrderState
from session import OrderSession

logger = logging.getLogger(__name__)

_PHONE_CLEAN = re.compile(r"[^0-9]")


class SessionRouter:
    """Load-or-create order sessions keyed by restaurant and phone number."""

    def get_or_create(
        self,
        restaurant_id: str,
        phone_number: str,
        db: Database,
    ) -> OrderSession:
        """Return the active session for *(restaurant_id, phone_number)*.

        Creates one if no active session exists. Terminal-state sessions
        (CANCELLED, COMPLETED) are replaced with a fresh session.

        Args:
            restaurant_id: Restaurant slug (e.g. "marios_pizzeria").
            phone_number: Customer phone number (digits, E.164, or with
                whatsapp: prefix).
            db: Database instance for SQLite-backed persistence.

        Returns:
            The loaded or newly created OrderSession with _db set.
        """
        sid = self._sanitize(phone_number)

        session = db.load_session(restaurant_id, sid)

        if session is not None:
            session._db = db  # type: ignore[has-type]
            if session.state in (OrderState.CANCELLED, OrderState.COMPLETED):
                logger.info(
                    "Terminal session %s/%s, creating new", restaurant_id, sid
                )
                session = OrderSession(restaurant_id=restaurant_id)
                session.session_id = sid
                session._db = db  # type: ignore[has-type]
                session.save()
                return session
            # Ensure restaurant_id is set (handles legacy sessions)
            if not session.restaurant_id:
                session.restaurant_id = restaurant_id
            logger.info("Loaded session %s/%s", restaurant_id, sid)
            return session

        # New session
        session = OrderSession(restaurant_id=restaurant_id)
        session.session_id = sid
        session._db = db  # type: ignore[has-type]
        session.save()
        logger.info("New session %s/%s", restaurant_id, sid)
        return session

    @staticmethod
    def _sanitize(phone_number: str) -> str:
        return _PHONE_CLEAN.sub("", phone_number)

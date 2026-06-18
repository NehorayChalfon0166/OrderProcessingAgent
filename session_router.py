"""Session router — maps phone numbers to persistent order sessions.

Each customer gets one active session per restaurant at a time, keyed by
the composite (restaurant_id, phone_number). The session_id is the sanitized
phone number. Sessions are stored in restaurant-scoped subdirectories:
  {sessions_dir}/{restaurant_id}/{phone_digits}.json
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from models import OrderState
from session import OrderSession

logger = logging.getLogger(__name__)

_PHONE_CLEAN = re.compile(r"[^0-9]")


class SessionRouter:
    """Load-or-create order sessions keyed by restaurant and phone number."""

    def __init__(self, sessions_dir: str) -> None:
        self.sessions_dir = sessions_dir

    def get_or_create(self, restaurant_id: str, phone_number: str) -> OrderSession:
        """Return the active session for *(restaurant_id, phone_number)*.

        Creates one if no active session exists. Terminal-state sessions
        (CANCELLED, COMPLETED) are replaced with a fresh session.

        Args:
            restaurant_id: Restaurant slug (e.g. "marios_pizzeria").
            phone_number: Customer phone number (digits, E.164, or with
                whatsapp: prefix).

        Returns:
            The loaded or newly created OrderSession with restaurant_id set.
        """
        sid = self._sanitize(phone_number)
        session_dir = str(Path(self.sessions_dir) / restaurant_id)

        try:
            session = OrderSession.load(sid, session_dir)
            if session.state in (OrderState.CANCELLED, OrderState.COMPLETED):
                logger.info(
                    "Terminal session %s/%s, creating new", restaurant_id, sid
                )
                session = OrderSession()
                session.session_id = sid
                session.restaurant_id = restaurant_id
                session.save(session_dir)
                return session
            # Ensure restaurant_id is set (handles any legacy sessions)
            if not session.restaurant_id:
                session.restaurant_id = restaurant_id
            logger.info("Loaded session %s/%s", restaurant_id, sid)
            return session
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(
                "Corrupted session for %s/%s, creating new: %s",
                restaurant_id, sid, e,
            )

        session = OrderSession()
        session.session_id = sid
        session.restaurant_id = restaurant_id
        session.save(session_dir)
        logger.info("New session %s/%s", restaurant_id, sid)
        return session

    @staticmethod
    def _sanitize(phone_number: str) -> str:
        return _PHONE_CLEAN.sub("", phone_number)

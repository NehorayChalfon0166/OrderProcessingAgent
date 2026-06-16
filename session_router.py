"""Session router — maps phone numbers to persistent order sessions.

Each customer gets one active session at a time, keyed by their phone
number. The session_id is set to the sanitized phone number so the
existing OrderSession.save/load API works without changes.
"""

from __future__ import annotations

import logging
import re

from models import OrderState
from session import OrderSession

logger = logging.getLogger(__name__)

_PHONE_CLEAN = re.compile(r"[^0-9]")


class SessionRouter:
    """Load-or-create order sessions keyed by phone number."""

    def __init__(self, sessions_dir: str) -> None:
        self.sessions_dir = sessions_dir

    def get_or_create(self, phone_number: str) -> OrderSession:
        """Return the active session for *phone_number*, creating one if needed.

        Args:
            phone_number: Phone number (digits, E.164, or with whatsapp: prefix).

        Returns:
            The loaded or newly created OrderSession.
        """
        sid = self._sanitize(phone_number)

        try:
            session = OrderSession.load(sid, self.sessions_dir)
            # Terminal-state sessions are dead — create a fresh one
            if session.state in (OrderState.CANCELLED, OrderState.COMPLETED):
                logger.info("Terminal session %s, creating new", sid)
                session = OrderSession()
                session.session_id = sid
                session.save(self.sessions_dir)
                return session
            logger.info("Loaded session %s", sid)
            return session
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Corrupted session for %s, creating new: %s", sid, e)

        session = OrderSession()
        session.session_id = sid
        session.save(self.sessions_dir)
        logger.info("New session %s", sid)
        return session

    @staticmethod
    def _sanitize(phone_number: str) -> str:
        return _PHONE_CLEAN.sub("", phone_number)

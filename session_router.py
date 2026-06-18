"""Session router — maps phone numbers to persistent order sessions.

Each customer gets one active session per restaurant at a time, keyed by
the composite (restaurant_id, phone_number). The session_id is the sanitized
phone number. Sessions are persisted via Database (when available) or as
JSON files in restaurant-scoped subdirectories.

  Database path:    sessions table, rows keyed by (restaurant_id, session_id)
  Filesystem path:  {sessions_dir}/{restaurant_id}/{phone_digits}.json
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from models import OrderState
from session import OrderSession

if TYPE_CHECKING:
    from db import Database

logger = logging.getLogger(__name__)

_PHONE_CLEAN = re.compile(r"[^0-9]")


class SessionRouter:
    """Load-or-create order sessions keyed by restaurant and phone number."""

    def __init__(self, sessions_dir: str) -> None:
        self.sessions_dir = sessions_dir

    def get_or_create(
        self,
        restaurant_id: str,
        phone_number: str,
        db: Database | None = None,
    ) -> OrderSession:
        """Return the active session for *(restaurant_id, phone_number)*.

        Creates one if no active session exists. Terminal-state sessions
        (CANCELLED, COMPLETED) are replaced with a fresh session.

        If *db* is provided, sessions are loaded from and saved to the
        database. Otherwise, JSON files are used.

        Args:
            restaurant_id: Restaurant slug (e.g. "marios_pizzeria").
            phone_number: Customer phone number (digits, E.164, or with
                whatsapp: prefix).
            db: Optional Database instance for SQLite-backed persistence.

        Returns:
            The loaded or newly created OrderSession with _db set if
            a Database was provided.
        """
        sid = self._sanitize(phone_number)

        if db is not None:
            return self._get_or_create_db(restaurant_id, sid, db)
        return self._get_or_create_fs(restaurant_id, sid)

    # ------------------------------------------------------------------
    # Database path
    # ------------------------------------------------------------------

    def _get_or_create_db(
        self, restaurant_id: str, sid: str, db: Database,
    ) -> OrderSession:
        """Load or create a session from the database."""
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
            logger.info("Loaded session %s/%s from DB", restaurant_id, sid)
            return session

        # New session
        session = OrderSession(restaurant_id=restaurant_id)
        session.session_id = sid
        session._db = db  # type: ignore[has-type]
        session.save()
        logger.info("New session %s/%s in DB", restaurant_id, sid)
        return session

    # ------------------------------------------------------------------
    # Filesystem path (unchanged from before)
    # ------------------------------------------------------------------

    def _get_or_create_fs(
        self, restaurant_id: str, sid: str,
    ) -> OrderSession:
        """Load or create a session from JSON files."""
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

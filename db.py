"""Database layer — SQLite-backed session and order persistence.

Replaces JSON-file storage for sessions and orders with SQLite + WAL mode.
Menus and restaurant config remain as JSON files (authored once, loaded at
startup).

Provides:
- Peewee ORM models for sessions and orders tables
- Database class with CRUD methods
- Automatic migration from legacy JSON files on first run
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from peewee import (
    CompositeKey,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

from session import OrderSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Peewee models
# ---------------------------------------------------------------------------

# The database handle is set at connection time by Database.__init__
_db_handle: SqliteDatabase | None = None


class BaseModel(Model):
    """Base model that binds to the shared database handle."""

    class Meta:
        database = _db_handle  # type: ignore[assignment]


class SessionRow(BaseModel):
    """A single order session — mirrors OrderSession fields as columns."""

    session_id = TextField()
    restaurant_id = TextField()
    state = TextField(default="building")
    cart = TextField(default="[]")           # JSON: list[CartItem]
    customer = TextField(default="{}")       # JSON: CustomerInfo
    conversation = TextField(default="[]")   # JSON: list[Message]
    payment_method = TextField(null=True)
    created_at = TextField()
    updated_at = TextField()

    class Meta:
        table_name = "sessions"
        primary_key = CompositeKey("restaurant_id", "session_id")


class OrderRow(BaseModel):
    """A completed order — written once, never mutated."""

    id = TextField(primary_key=True)  # {restaurant_id}_{phone}_{ts}
    restaurant_id = TextField()
    session_id = TextField()
    customer_name = TextField(null=True)
    customer_phone = TextField(null=True)
    items = TextField(default="[]")          # JSON: list[CartItem]
    subtotal = TextField()                   # stored as text, cast on read
    delivery_fee = TextField()
    total = TextField()
    order_type = TextField()
    payment_method = TextField(null=True)
    printed = IntegerField(default=0)        # 0=false, 1=true
    created_at = TextField()

    class Meta:
        table_name = "orders"


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """Manages the SQLite database connection and provides CRUD operations.

    Usage::

        db = Database("order_agent.db")
        db.save_session(session)
        session = db.load_session(restaurant_id, session_id)
    """

    def __init__(self, path: str = "order_agent.db") -> None:
        global _db_handle
        self._path = path

        _db_handle = SqliteDatabase(path, pragmas={
            "journal_mode": "wal",
            "foreign_keys": 1,
        })
        _db_handle.connect()

        # Bind models to this database
        BaseModel._meta.database = _db_handle
        SessionRow._meta.database = _db_handle
        OrderRow._meta.database = _db_handle

        self._create_tables()
        self._migrate_if_needed()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def save_session(self, session: OrderSession) -> None:
        """Insert or replace a session row."""
        data = {
            "session_id": session.session_id,
            "restaurant_id": session.restaurant_id,
            "state": session.state.value,
            "cart": session.model_dump_json(
                include={"cart"}, exclude_none=False
            ),
            "customer": session.customer.model_dump_json(exclude_none=False),
            "conversation": _conversation_to_json(session),
            "payment_method": session.payment_method,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }
        # extract nested JSON fields from the dump
        data["cart"] = json.dumps([item.model_dump() for item in session.cart])
        data["customer"] = session.customer.model_dump_json(exclude_none=False)
        data["conversation"] = json.dumps(
            [msg.model_dump() for msg in session.conversation],
            default=str,
        )

        SessionRow.replace(data).execute()

    def load_session(
        self,
        restaurant_id: str,
        session_id: str,
    ) -> OrderSession | None:
        """Load a session from the database, or return None."""
        row = (
            SessionRow
            .select()
            .where(
                (SessionRow.restaurant_id == restaurant_id)
                & (SessionRow.session_id == session_id)
            )
            .first()
        )
        if row is None:
            return None

        return OrderSession(
            session_id=row.session_id,
            restaurant_id=row.restaurant_id,
            state=row.state,
            cart=json.loads(row.cart),
            customer=json.loads(row.customer),
            conversation=json.loads(row.conversation),
            payment_method=row.payment_method,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def delete_session(self, restaurant_id: str, session_id: str) -> None:
        """Delete a session row. No-op if it doesn't exist."""
        SessionRow.delete().where(
            (SessionRow.restaurant_id == restaurant_id)
            & (SessionRow.session_id == session_id)
        ).execute()

    # ------------------------------------------------------------------
    # Order CRUD
    # ------------------------------------------------------------------

    def save_order(self, order_data: dict) -> None:
        """Insert a completed order."""
        OrderRow.replace({
            "id": order_data.get("order_id", ""),
            "restaurant_id": order_data.get("restaurant_id", ""),
            "session_id": order_data.get("order_id", ""),
            "customer_name": order_data.get("customer", {}).get("name"),
            "customer_phone": order_data.get("customer", {}).get("phone"),
            "items": json.dumps(order_data.get("items", [])),
            "subtotal": str(order_data.get("subtotal", 0)),
            "delivery_fee": str(order_data.get("delivery_fee", 0)),
            "total": str(order_data.get("total", 0)),
            "order_type": order_data.get("order_type", "pickup"),
            "payment_method": order_data.get("payment_method", "cash"),
            "printed": 0,
            "created_at": order_data.get("timestamp", ""),
        }).execute()

    def get_orders(
        self, restaurant_id: str, limit: int = 50
    ) -> list[dict]:
        """Return recent orders for a restaurant, newest first."""
        rows = (
            OrderRow
            .select()
            .where(OrderRow.restaurant_id == restaurant_id)
            .order_by(OrderRow.created_at.desc())
            .limit(limit)
        )
        return [_order_row_to_dict(r) for r in rows]

    def get_unprinted_orders(self, restaurant_id: str) -> list[dict]:
        """Return orders with printed=0 for the printer agent."""
        rows = (
            OrderRow
            .select()
            .where(
                (OrderRow.restaurant_id == restaurant_id)
                & (OrderRow.printed == 0)
            )
            .order_by(OrderRow.created_at.asc())
        )
        return [_order_row_to_dict(r) for r in rows]

    def mark_printed(self, order_id: str) -> None:
        """Set printed=1 for an order. Idempotent."""
        OrderRow.update(printed=1).where(
            OrderRow.id == order_id
        ).execute()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        _db_handle.create_tables([SessionRow, OrderRow], safe=True)

    def _migrate_if_needed(self) -> None:
        """One-time migration from legacy JSON files."""
        if SessionRow.select().count() > 0:
            return  # already migrated or fresh start with data

        sessions_dir = Path("sessions")
        if not sessions_dir.exists():
            return  # no legacy data to migrate

        count = 0
        for restaurant_dir in sessions_dir.iterdir():
            if not restaurant_dir.is_dir():
                continue
            rid = restaurant_dir.name
            for session_file in restaurant_dir.glob("*.json"):
                try:
                    session = OrderSession.model_validate_json(
                        session_file.read_text(encoding="utf-8")
                    )
                    session.restaurant_id = rid
                    self.save_session(session)
                    count += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to migrate session %s: %s",
                        session_file, exc,
                    )

        if count > 0:
            logger.info("Migrated %d session(s) from JSON to SQLite", count)

        # Migrate orders too
        orders_dir = Path("orders")
        if not orders_dir.exists():
            return

        order_count = 0
        for restaurant_dir in orders_dir.iterdir():
            if not restaurant_dir.is_dir():
                continue
            for order_file in restaurant_dir.glob("*.json"):
                try:
                    order_data = json.loads(
                        order_file.read_text(encoding="utf-8")
                    )
                    self.save_order(order_data)
                    order_count += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to migrate order %s: %s",
                        order_file, exc,
                    )

        if order_count > 0:
            logger.info("Migrated %d order(s) from JSON to SQLite", order_count)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conversation_to_json(session: OrderSession) -> str:
    """Serialize conversation to JSON string."""
    return json.dumps(
        [msg.model_dump() for msg in session.conversation],
        default=str,
    )


def _order_row_to_dict(row: OrderRow) -> dict:
    """Convert an OrderRow to a dict for API responses."""
    return {
        "order_id": row.id,
        "restaurant_id": row.restaurant_id,
        "session_id": row.session_id,
        "customer_name": row.customer_name,
        "customer_phone": row.customer_phone,
        "items": json.loads(row.items),
        "subtotal": float(row.subtotal),
        "delivery_fee": float(row.delivery_fee),
        "total": float(row.total),
        "order_type": row.order_type,
        "payment_method": row.payment_method,
        "printed": bool(row.printed),
        "created_at": row.created_at,
    }

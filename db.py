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


class BaseModel(Model):
    """Base model — database is bound at runtime by Database.__init__."""

    class Meta:
        database = None  # type: ignore[assignment] — set by Database.__init__


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

    id = TextField(primary_key=True)  # unique order ID
    restaurant_id = TextField()
    session_id = TextField()
    customer_name = TextField(null=True)
    customer_phone = TextField(null=True)
    customer_address = TextField(null=True)
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
        self._path = path

        self._db = SqliteDatabase(path, pragmas={
            "journal_mode": "wal",
            "foreign_keys": 1,
            "busy_timeout": 5000,       # wait up to 5s instead of "database is locked"
            "wal_autocheckpoint": 1000, # checkpoint after 1000 pages written
        })
        self._db.connect()
        # Limit WAL file size to prevent unbounded growth under write load
        self._db.execute_sql("PRAGMA journal_size_limit = 67108864")  # 64MB

        # Bind models to this database instance
        BaseModel._meta.database = self._db
        SessionRow._meta.database = self._db
        OrderRow._meta.database = self._db

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
            "cart": json.dumps([item.model_dump() for item in session.cart]),
            "customer": session.customer.model_dump_json(exclude_none=False),
            "conversation": json.dumps(
                [msg.model_dump() for msg in session.conversation],
                default=str,
            ),
            "payment_method": session.payment_method,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }
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

    def list_active_sessions(
        self, restaurant_id: str | None = None, limit: int = 50
    ) -> list[OrderSession]:
        """Return active (non-terminal) sessions, newest first."""
        query = SessionRow.select().where(
            SessionRow.state.not_in(["completed", "cancelled"])
        )
        if restaurant_id:
            query = query.where(SessionRow.restaurant_id == restaurant_id)
        query = query.order_by(SessionRow.updated_at.desc()).limit(limit)
        return [
            OrderSession(
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
            for row in query
        ]

    # ------------------------------------------------------------------
    # Order CRUD
    # ------------------------------------------------------------------

    def persist_completed_order(
        self,
        session,
        pricing,
        restaurant_name: str,
        orders_dir: str = "orders",
    ) -> str:
        """Save a completed order to JSON file and database.

        Builds the payload from session + pricing, generates a unique
        order ID, writes to both JSON file and the orders table.

        Returns the generated order_id.
        """
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        ot = session.customer.order_type
        if hasattr(ot, 'value'):
            ot = ot.value
        ot = ot or "pickup"
        subtotal, delivery_fee, total = pricing.compute_totals(session.cart, session.customer.order_type)

        ts = _dt.now(_tz.utc)
        order_id = (
            f"{session.restaurant_id}_{ts.strftime('%Y%m%d%H%M%S')}_"
            f"{str(_uuid.uuid4())[:6]}"
        )

        payload = {
            "order_id": order_id,
            "session_id": session.session_id,
            "restaurant_id": session.restaurant_id,
            "restaurant": restaurant_name,
            "items": [item.model_dump() for item in session.cart],
            "customer": session.customer.model_dump(),
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "total": total,
            "order_type": ot,
            "payment_method": session.payment_method or "cash",
            "timestamp": ts.isoformat(),
        }

        # Save to JSON file
        order_dir = Path(orders_dir) / session.restaurant_id
        order_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{order_id}.json"
        filepath = order_dir / filename
        filepath.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Save to database
        self.save_order(payload)

        logger.info("Order saved: %s (id: %s)", filepath, order_id)
        return order_id

    def save_order(self, order_data: dict) -> None:
        """Insert a completed order (low-level — use persist_completed_order)."""
        customer = order_data.get("customer", {})
        OrderRow.replace({
            "id": order_data.get("order_id", ""),
            "restaurant_id": order_data.get("restaurant_id", ""),
            "session_id": order_data.get("session_id", order_data.get("order_id", "")),
            "customer_name": customer.get("name"),
            "customer_phone": customer.get("phone"),
            "customer_address": customer.get("address"),
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

    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """Delete terminal sessions older than *max_age_days*. Returns count."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        deleted = (
            SessionRow
            .delete()
            .where(
                SessionRow.state.in_(["completed", "cancelled"])
                & (SessionRow.updated_at < cutoff)
            )
            .execute()
        )
        if deleted:
            logger.info("Cleaned up %d old sessions (older than %d days)", deleted, max_age_days)
        return deleted

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
        self._db.create_tables([SessionRow, OrderRow], safe=True)
        # Add customer_address column to existing orders table if needed
        try:
            self._db.execute_sql(
                "ALTER TABLE orders ADD COLUMN customer_address TEXT"
            )
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                logger.warning(
                    "Unexpected error adding customer_address column: %s", e
                )

    def _migrate_if_needed(self) -> None:
        """One-time migration from legacy JSON files.

        Supports both flat files (v1 layout) and per-restaurant
        subdirectories (v2 layout).
        """
        if SessionRow.select().count() > 0:
            return  # already migrated or fresh start with data

        sessions_dir = Path("sessions")
        if not sessions_dir.exists():
            return  # no legacy data to migrate

        count = 0
        for entry in sessions_dir.iterdir():
            if entry.is_dir():
                # v2: per-restaurant subdirectories
                rid = entry.name
                for session_file in entry.glob("*.json"):
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
            elif entry.suffix == ".json":
                # v1: flat files — infer restaurant from filename or default
                try:
                    session = OrderSession.model_validate_json(
                        entry.read_text(encoding="utf-8")
                    )
                    if not session.restaurant_id:
                        session.restaurant_id = "default"
                    self.save_session(session)
                    count += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to migrate session %s: %s",
                        entry, exc,
                    )

        if count > 0:
            logger.info("Migrated %d session(s) from JSON to SQLite", count)

        # Migrate orders too
        orders_dir = Path("orders")
        if not orders_dir.exists():
            return

        order_count = 0
        for entry in orders_dir.iterdir():
            if entry.is_dir():
                for order_file in entry.glob("*.json"):
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
            elif entry.suffix == ".json":
                try:
                    order_data = json.loads(
                        entry.read_text(encoding="utf-8")
                    )
                    self.save_order(order_data)
                    order_count += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to migrate order %s: %s",
                        entry, exc,
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
        "customer_address": row.customer_address,
        "items": json.loads(row.items),
        "subtotal": float(row.subtotal),
        "delivery_fee": float(row.delivery_fee),
        "total": float(row.total),
        "order_type": row.order_type,
        "payment_method": row.payment_method,
        "printed": bool(row.printed),
        "created_at": row.created_at,
    }

"""Order session — holds mutable order state and handles persistence.

The session is a Pydantic model that stores everything about an active
order: cart items, customer info, conversation history, and state machine
position. Persists to SQLite when a Database instance is attached, or
falls back to JSON files.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, PrivateAttr

from models import CartItem, CustomerInfo, Message, MessageRole, OrderState

if TYPE_CHECKING:
    from db import Database


class OrderSession(BaseModel):
    """Mutable state for a single order conversation.

    Holds cart items, customer info, conversation history, and current
    state. Persisted to JSON after every agent loop iteration.
    """

    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8].upper(),
        description="Unique session identifier",
    )
    restaurant_id: str = Field(
        default="",
        description="Restaurant tenant identifier for this order",
    )
    state: OrderState = Field(
        default=OrderState.BUILDING,
        description="Current state machine state",
    )
    cart: list[CartItem] = Field(
        default_factory=list,
        description="Line items in the order",
    )
    customer: CustomerInfo = Field(
        default_factory=CustomerInfo,
        description="Merged customer details",
    )
    conversation: list[Message] = Field(
        default_factory=list,
        description="Typed conversation history (provider-agnostic)",
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat(),
        description="ISO 8601 creation timestamp",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat(),
        description="ISO 8601 last-update timestamp",
    )
    payment_method: str | None = Field(
        default=None,
        description="'cash' or 'link' — set by confirm_order, read by payment webhook",
    )

    # Internal signal — set by tools, read and cleared by agent loop.
    # Excluded from serialization and never seen by the LLM.
    _pending_transition: OrderState | None = PrivateAttr(default=None)

    # Order ID — set by confirm_order, read by channel-level save logic.
    # Ensures the customer-facing confirmation number matches the persisted
    # order ID and kitchen ticket.
    _confirmed_order_id: str | None = PrivateAttr(default=None)

    # Database handle — set by SessionRouter or caller. When present,
    # save() delegates to the database instead of writing JSON files.
    _db: Database | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_complete(self) -> bool:
        """True when the order has been confirmed."""
        return self.state == OrderState.COMPLETED

    @property
    def is_cancelled(self) -> bool:
        """True when the order has been cancelled."""
        return self.state == OrderState.CANCELLED

    @property
    def is_active(self) -> bool:
        """True while the order is still in progress."""
        return self.state not in (OrderState.COMPLETED, OrderState.CANCELLED)

    # ------------------------------------------------------------------
    # Conversation Helpers
    # ------------------------------------------------------------------

    def add_user_message(self, content: str) -> None:
        """Append a user message to the conversation."""
        self.conversation.append(Message(role=MessageRole.USER, content=content))

    def add_assistant_message(
        self,
        content: str | None = None,
        tool_calls: list | None = None,
    ) -> None:
        """Append an assistant message (with optional tool calls)."""
        self.conversation.append(
            Message(
                role=MessageRole.ASSISTANT,
                content=content,
                tool_calls=tool_calls,
            )
        )

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result_json: str,
    ) -> None:
        """Append a tool result message."""
        self.conversation.append(
            Message(
                role=MessageRole.TOOL,
                content=result_json,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist session state via the attached Database.

        Raises:
            RuntimeError: If no Database is attached to this session.
        """
        if self._db is None:
            raise RuntimeError(
                "Cannot save session — no Database attached. "
                "Set session._db to a Database instance before saving."
            )
        self.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._db.save_session(self)

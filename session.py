"""Order session — holds mutable order state and handles JSON persistence.

The session is a Pydantic model that stores everything about an active
order: cart items, customer info, conversation history, and state machine
position. It is persisted to sessions/{session_id}.json after every turn.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, PrivateAttr

from models import CartItem, CustomerInfo, Message, MessageRole, OrderState


class OrderSession(BaseModel):
    """Mutable state for a single order conversation.

    Holds cart items, customer info, conversation history, and current
    state. Persisted to JSON after every agent loop iteration.
    """

    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8].upper(),
        description="Unique session identifier",
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

    def save(self, sessions_dir: str = "sessions") -> Path:
        """Write session state to a JSON file.

        Creates the sessions directory if it doesn't exist.
        """
        self.updated_at = datetime.now(tz=timezone.utc).isoformat()
        dir_path = Path(sessions_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        filepath = dir_path / f"{self.session_id}.json"
        filepath.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return filepath

    @classmethod
    def load(cls, session_id: str, sessions_dir: str = "sessions") -> "OrderSession":
        """Load a session from a JSON file.

        Raises:
            FileNotFoundError: If the session file doesn't exist.
        """
        filepath = Path(sessions_dir) / f"{session_id}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Session not found: {filepath}")
        return cls.model_validate_json(filepath.read_text(encoding="utf-8"))

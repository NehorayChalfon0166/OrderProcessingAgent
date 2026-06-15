# Component: `session.py`

Status: **SETTLED**

Holds the mutable state of an active order and handles persistence.
Thin — most logic lives in tools and the agent loop.

## OrderSession

```python
class OrderSession(BaseModel):
    session_id: str                      # UUID
    state: OrderState                    # current state machine state
    cart: list[CartItem]                 # line items
    customer: CustomerInfo               # merged customer details
    conversation: list[Message]          # typed event list
    created_at: str                      # ISO 8601
    updated_at: str                      # ISO 8601, updated every turn
```

## Conversation Format: Typed Events (Option B)

Store provider-agnostic events, convert to OpenAI format in `llm_client.py`.

```python
class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class ToolCallRequest(BaseModel):
    """One tool call the LLM requested in its response."""
    id: str
    name: str
    arguments: dict[str, Any]

class Message(BaseModel):
    role: MessageRole
    content: str | None = None                          # None when tool calls present
    tool_calls: list[ToolCallRequest] | None = None     # ASSISTANT messages
    tool_call_id: str | None = None                     # TOOL messages (which call this answers)
    name: str | None = None                             # TOOL messages (tool name)
```

### How `llm_client.py` uses these

**Outbound (Python → LLM):** The client's `messages_to_openai(session.conversation)` converts
`Message` objects to the OpenAI dict format. `USER` → `{"role": "user", ...}`, `ASSISTANT` with
tool_calls → the OpenAI assistant+tool_calls format, `TOOL` → `{"role": "tool", ...}`.

**Inbound (LLM → Python):** The agent loop receives the raw OpenAI response, extracts text and
tool_calls, and appends one `ASSISTANT` Message (with any tool_calls) and one `TOOL` Message
per tool result to `session.conversation`.

### Why B over A (raw dicts)

- Provider-agnostic — switching from DeepSeek to another API doesn't change session files.
- Schema validation — Pydantic catches malformed messages at insertion time, not at API call time.
- Inspectable — session JSON files are self-documenting with explicit fields.
- Low overhead — conversion is a single method with a dict-of-role-to-type mapping.

## Internal Signal: `_pending_transition`

```python
_pending_transition: OrderState | None = None  # excluded from model_dump
```

Tools set this. The agent loop reads and clears it after all tool calls for the turn.
Never persisted, never seen by the LLM.

## Persistence

```
sessions/
  {session_id}.json    # complete OrderSession serialized via model_dump_json
```

- `OrderSession.save(path)` → writes to `sessions/{session_id}.json`
- `OrderSession.load(session_id)` → reads and `model_validate_json`
- Called at the end of every agent loop iteration
- No index file — `os.listdir("sessions/")` is sufficient for prototyping

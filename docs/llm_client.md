# Component: `llm_client.py`

Status: **SETTLED**

Thin wrapper around the DeepSeek API using the OpenAI SDK. No JSON parsing, no
fallback logic — the entire v1 `chat_structured()` stack is gone. Tool calling
replaces all of it.

## Model

**`deepseek-v4-flash`** — 13B active params, 1M context window, $0.14/M input.

`deepseek-chat` (v3.2) is mapped to v4-flash during the grace period and will be
fully deprecated July 24, 2026. We use `deepseek-v4-flash` directly.

v4-flash is sufficient: the agent's cognitive load is intent classification +
parameter extraction. State machine, tool gating, and tool results do the heavy
lifting.

## Standard Mode (No Strict)

DeepSeek supports strict function calling via the `/beta` endpoint, but it mandates
all object properties in `required` and `additionalProperties: false`. Our tools
have optional parameters with defaults (`size=None`, `quantity=1`, `toppings=[]`).
Strict mode would force the LLM to pass these explicitly every time.

We use **standard mode** (`https://api.deepseek.com`). The tool set is small,
parameters are obvious from context, and state-gating is the real safety mechanism.
Revisit strict mode if the model hallucinates parameters.

## API

```python
class LLMClient:
    def __init__(self, config: AppConfig) -> None:
        # Uses OpenAI SDK with base_url="https://api.deepseek.com"
        # Model: config.llm_model (deepseek-v4-flash)

    def chat(
        self,
        messages: list[dict],           # OpenAI-format conversation
        tools: list[dict] | None = None # tool definitions
    ) -> tuple[str, list[ToolCall]]
        # Returns: (assistant_text, [tool_calls])
        # If no tools provided: plain chat, no tool calls
        # If tools provided: model may return text + tool_calls
```

No `chat_structured()` method. No JSON parsing. No balanced-brace scanner. Just
the OpenAI SDK's native `client.chat.completions.create()` with the `tools` and
`tool_choice` parameters.

## Message Conversion

Converts our typed `Message` objects (from `session.py`) to OpenAI-format dicts:

```python
def messages_to_openai(conversation: list[Message]) -> list[dict]:
    # Maps session.Message to OpenAI chat format:
    #   USER       → {"role": "user", "content": "..."}
    #   ASSISTANT  → {"role": "assistant", "content": "...", "tool_calls": [...]}
    #   TOOL       → {"role": "tool", "tool_call_id": "...", "content": "..."}
```

## Tool Definition Builder

Converts our `@tool`-decorated Python functions to OpenAI function-calling schemas:

```python
def build_tool_definitions(tools: list[callable]) -> list[dict]:
    # For each @tool-decorated function:
    #   1. Read __tool_name__, __tool_description__ from decorator
    #   2. Read __tool_schema__ (JSON Schema pre-built by decorator)
    #   3. Return OpenAI-format {"type": "function", "function": {...}}
```

The `@tool` decorator (defined in `tools.py`) stores metadata on the function:

```python
@tool(description="Add an item to the cart")
def add_to_cart(
    product_name: str,
    quantity: int = 1,
    size: str | None = None,
    ...
) -> AddToCartResult:
```

## Error Handling

Standard try-catch for API errors (carried over from v1):

- `APIConnectionError` — endpoint unreachable
- `APITimeoutError` — request timed out
- `APIStatusError` — HTTP error from DeepSeek

No retry logic (can add later). No client-side tool call validation — DeepSeek
validates tool calls server-side.

## What Was Dropped from v1 `llm_client.py`

| v1 | v2 |
|---|---|
| `chat_structured()` with JSON mode | `chat()` with native tool calling |
| 4-tier JSON parsing (direct → markdown → balanced braces → fallback) | Gone — DeepSeek returns structured tool calls natively |
| `_extract_first_json_object()` (the balanced-brace scanner) | Gone |
| `response_format={"type": "json_object"}` | Gone — replaced by `tools` + `tool_choice` |
| Provider presets in the client | Gone — single provider (DeepSeek), config handles it |

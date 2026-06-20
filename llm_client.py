"""Thin DeepSeek API wrapper using the OpenAI SDK.

No JSON parsing, no fallback logic — tool calling replaces all of v1's
chat_structured() stack. Handles message conversion from our typed format
to OpenAI-format dicts and builds tool definitions from @tool-decorated
functions.
"""

from __future__ import annotations

import logging
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from config import AppConfig
from models import Message, MessageRole, ToolCallRequest

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 3.0, 9.0]  # seconds per attempt
_RETRYABLE_STATUSES = frozenset({429, 503})


class LLMClient:
    """DeepSeek API client via OpenAI SDK.

    Model: deepseek-v4-flash. Standard mode (not strict — our tools have
    optional parameters that conflict with strict mode's 'all required' rule).

    Retries transient failures (connection, timeout, 429, 503) with
    exponential backoff. Permanent errors (401, 403, 400) propagate immediately.
    """

    def __init__(self, config: AppConfig) -> None:
        self._model = config.llm_model
        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self._debug = config.debug

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> tuple[str, list[ToolCallRequest]]:
        """Send a chat completion request with retry for transient failures.

        Args:
            messages: OpenAI-format conversation history.
            tools: Optional tool definitions (from build_tool_definitions).

        Returns:
            (assistant_text, [tool_calls]).

        Raises:
            APIConnectionError: After exhausting retries on connection failures.
            APITimeoutError: After exhausting retries on timeouts.
            APIStatusError: Immediately for permanent errors (400, 401, 403),
                or after exhausting retries for transient ones (429, 503).
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                return self._chat_once(messages, tools)
            except (APIConnectionError, APITimeoutError) as e:
                last_exc = e
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BACKOFF[attempt]
                    logger.warning(
                        "LLM %s on attempt %d/%d, retrying in %.1fs: %s",
                        type(e).__name__, attempt + 1, _MAX_RETRIES, delay, e,
                    )
                    time.sleep(delay)
            except APIStatusError as e:
                if e.status_code in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES - 1:
                    last_exc = e
                    delay = _RETRY_BACKOFF[attempt]
                    logger.warning(
                        "LLM HTTP %d on attempt %d/%d, retrying in %.1fs",
                        e.status_code, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                else:
                    raise  # permanent error or retries exhausted

        # Retries exhausted
        assert last_exc is not None
        logger.error("LLM call failed after %d attempts: %s", _MAX_RETRIES, last_exc)
        raise last_exc

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _chat_once(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> tuple[str, list[ToolCallRequest]]:
        """Single chat completion attempt — no retry logic."""
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = self._parse_tool_calls(choice.message.tool_calls)

        if self._debug:
            logger.debug("LLM text: %s", content[:500])
            if tool_calls:
                logger.debug("LLM tool calls: %s", [tc.name for tc in tool_calls])

        return content, tool_calls

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_tool_calls(
        raw: list | None,
    ) -> list[ToolCallRequest]:
        """Convert OpenAI tool call objects to our typed ToolCallRequest."""
        if not raw:
            return []
        result: list[ToolCallRequest] = []
        for tc in raw:
            import json

            args = {}
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, AttributeError):
                pass
            result.append(
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
            )
        return result


# =============================================================================
# Message Conversion
# =============================================================================


def messages_to_openai(conversation: list[Message]) -> list[dict]:
    """Convert our typed Message objects to OpenAI-format dicts.

    Maps:
        USER       → {"role": "user", "content": "..."}
        ASSISTANT  → {"role": "assistant", "content": "..."}
        TOOL       → {"role": "tool", "tool_call_id": "...", "content": "..."}
    """
    result: list[dict] = []
    for msg in conversation:
        if msg.role == MessageRole.USER:
            result.append({"role": "user", "content": msg.content or ""})
        elif msg.role == MessageRole.ASSISTANT:
            entry: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _dict_to_json(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            result.append(entry)
        elif msg.role == MessageRole.TOOL:
            result.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "",
                "content": msg.content or "",
            })
    return result


def _dict_to_json(d: dict) -> str:
    """Serialize a dict to JSON for tool call arguments."""
    import json
    return json.dumps(d, ensure_ascii=False)


# =============================================================================
# Tool Definition Builder
# =============================================================================


def build_tool_definitions(tool_funcs: list) -> list[dict]:
    """Build OpenAI function-calling tool definitions from @tool-decorated functions.

    Each function must have __tool_name__, __tool_description__, and
    __tool_schema__ attributes set by the @tool decorator.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": f.__tool_name__,
                "description": f.__tool_description__,
                "parameters": f.__tool_schema__,
            },
        }
        for f in tool_funcs
    ]

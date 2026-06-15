"""Thin DeepSeek API wrapper using the OpenAI SDK.

No JSON parsing, no fallback logic — tool calling replaces all of v1's
chat_structured() stack. Handles message conversion from our typed format
to OpenAI-format dicts and builds tool definitions from @tool-decorated
functions.
"""

from __future__ import annotations

import logging

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from config import AppConfig
from models import Message, MessageRole, ToolCallRequest

logger = logging.getLogger(__name__)


class LLMClient:
    """DeepSeek API client via OpenAI SDK.

    Model: deepseek-v4-flash. Standard mode (not strict — our tools have
    optional parameters that conflict with strict mode's 'all required' rule).
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
        """Send a chat completion request.

        Args:
            messages: OpenAI-format conversation history.
            tools: Optional tool definitions (from build_tool_definitions).

        Returns:
            (assistant_text, [tool_calls]).
        """
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            content = choice.message.content or ""
            tool_calls = self._parse_tool_calls(choice.message.tool_calls)

            if self._debug:
                logger.debug("LLM text: %s", content[:500])
                if tool_calls:
                    logger.debug("LLM tool calls: %s", [tc.name for tc in tool_calls])

            return content, tool_calls

        except APIConnectionError:
            logger.error("DeepSeek API connection error")
            raise
        except APITimeoutError:
            logger.error("DeepSeek API timeout")
            raise
        except APIStatusError as e:
            logger.error("DeepSeek API HTTP %s: %s", e.status_code, e.message)
            raise
        except Exception as e:
            logger.error("Unexpected LLM error: %s", e)
            raise

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

"""Tests for llm_client.py — message conversion, tool definitions, and retry logic."""

from unittest import mock

import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from config import AppConfig
from models import Message, MessageRole, ToolCallRequest
from llm_client import LLMClient, build_tool_definitions, messages_to_openai


class TestMessagesToOpenAI:
    def test_user_message(self):
        conv = [Message(role=MessageRole.USER, content="Hello")]
        result = messages_to_openai(conv)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_assistant_text_only(self):
        conv = [Message(role=MessageRole.ASSISTANT, content="Welcome!")]
        result = messages_to_openai(conv)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Welcome!"
        assert "tool_calls" not in result[0]

    def test_assistant_with_tool_calls(self):
        tc = ToolCallRequest(id="call_1", name="add_to_cart", arguments={"product_name": "Margherita"})
        conv = [Message(role=MessageRole.ASSISTANT, content=None, tool_calls=[tc])]
        result = messages_to_openai(conv)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] is None
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["function"]["name"] == "add_to_cart"

    def test_tool_result(self):
        conv = [
            Message(
                role=MessageRole.TOOL,
                content='{"success": true}',
                tool_call_id="call_1",
                name="add_to_cart",
            )
        ]
        result = messages_to_openai(conv)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == '{"success": true}'

    def test_full_conversation(self):
        conv = [
            Message(role=MessageRole.USER, content="I want a pizza"),
            Message(
                role=MessageRole.ASSISTANT,
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="c1",
                        name="add_to_cart",
                        arguments={"product_name": "Margherita"},
                    )
                ],
            ),
            Message(
                role=MessageRole.TOOL,
                content='{"success": true}',
                tool_call_id="c1",
                name="add_to_cart",
            ),
        ]
        result = messages_to_openai(conv)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "tool"


class TestBuildToolDefinitions:
    def test_builds_from_decorated_tools(self):
        from tools import add_to_cart, view_cart
        defs = build_tool_definitions([add_to_cart, view_cart])
        assert len(defs) == 2
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "add_to_cart"
        assert "description" in defs[0]["function"]
        assert "parameters" in defs[0]["function"]

    def test_all_tools_have_schemas(self):
        from tools import TOOLS_BY_STATE
        from models import OrderState

        for state, funcs in TOOLS_BY_STATE.items():
            for f in funcs:
                assert hasattr(f, "__tool_name__"), f"{f} missing __tool_name__"
                assert hasattr(f, "__tool_description__"), f"{f} missing __tool_description__"
                assert hasattr(f, "__tool_schema__"), f"{f} missing __tool_schema__"
                assert f.__tool_schema__["type"] == "object"
                assert "properties" in f.__tool_schema__


# =============================================================================
# LLM Retry Tests
# =============================================================================


@pytest.fixture
def llm_client():
    """Create an LLMClient with a mock config (no real API key needed)."""
    config = AppConfig(
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
        llm_base_url="https://api.deepseek.com",
    )
    return LLMClient(config)


def _make_mock_response(content="Hello"):
    """Build a minimal mock OpenAI response."""
    msg = mock.MagicMock()
    msg.content = content
    msg.tool_calls = None
    choice = mock.MagicMock()
    choice.message = msg
    response = mock.MagicMock()
    response.choices = [choice]
    return response


class TestLLMRetry:
    def test_succeeds_on_first_attempt(self, llm_client):
        """Normal call — no retries needed."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.return_value = _make_mock_response("Hi there!")

            text, tool_calls = llm_client.chat([{"role": "user", "content": "hello"}])

            assert text == "Hi there!"
            assert tool_calls == []
            assert m.call_count == 1

    def test_retries_on_connection_error(self, llm_client):
        """Retries after APIConnectionError, succeeds on 3rd attempt."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = [
                APIConnectionError(message="connection refused", request=None),
                APIConnectionError(message="connection refused", request=None),
                _make_mock_response("finally!"),
            ]

            text, _ = llm_client.chat([{"role": "user", "content": "hello"}])

            assert text == "finally!"
            assert m.call_count == 3

    def test_retries_on_timeout(self, llm_client):
        """Retries after APITimeoutError."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = [
                APITimeoutError(request=None),
                _make_mock_response("ok"),
            ]

            text, _ = llm_client.chat([{"role": "user", "content": "hello"}])

            assert text == "ok"
            assert m.call_count == 2

    def test_retries_on_503(self, llm_client):
        """Retries after 503 Service Unavailable."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = [
                APIStatusError("overloaded", response=mock.MagicMock(status_code=503), body=None),
                APIStatusError("overloaded", response=mock.MagicMock(status_code=503), body=None),
                _make_mock_response("recovered"),
            ]

            text, _ = llm_client.chat([{"role": "user", "content": "hello"}])

            assert text == "recovered"
            assert m.call_count == 3

    def test_retries_on_429(self, llm_client):
        """Retries after 429 Rate Limit."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = [
                APIStatusError("rate limited", response=mock.MagicMock(status_code=429), body=None),
                _make_mock_response("ok"),
            ]

            text, _ = llm_client.chat([{"role": "user", "content": "hello"}])

            assert text == "ok"
            assert m.call_count == 2

    def test_raises_immediately_on_401(self, llm_client):
        """No retry on permanent error 401."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = APIStatusError(
                "unauthorized",
                response=mock.MagicMock(status_code=401),
                body=None,
            )

            with pytest.raises(APIStatusError):
                llm_client.chat([{"role": "user", "content": "hello"}])

            assert m.call_count == 1  # no retry

    def test_raises_immediately_on_400(self, llm_client):
        """No retry on permanent error 400."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = APIStatusError(
                "bad request",
                response=mock.MagicMock(status_code=400),
                body=None,
            )

            with pytest.raises(APIStatusError):
                llm_client.chat([{"role": "user", "content": "hello"}])

            assert m.call_count == 1  # no retry

    def test_raises_after_exhausting_retries(self, llm_client):
        """Raises the last exception after 3 failed connection attempts."""
        with mock.patch.object(llm_client._client.chat.completions, "create") as m:
            m.side_effect = [
                APIConnectionError(message="fail 1", request=None),
                APIConnectionError(message="fail 2", request=None),
                APIConnectionError(message="fail 3", request=None),
            ]

            with pytest.raises(APIConnectionError) as exc_info:
                llm_client.chat([{"role": "user", "content": "hello"}])

            assert "fail 3" in str(exc_info.value)
            assert m.call_count == 3

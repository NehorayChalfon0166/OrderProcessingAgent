"""Tests for llm_client.py — message conversion and tool definitions (no API calls)."""

from models import Message, MessageRole, ToolCallRequest
from llm_client import build_tool_definitions, messages_to_openai


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

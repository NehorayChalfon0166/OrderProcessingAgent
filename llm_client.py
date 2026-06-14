"""Provider-agnostic LLM client using the OpenAI-compatible API.

Supports any OpenAI-compatible endpoint: Groq, Gemini, OpenRouter,
Mistral, Ollama, and more. Switch providers by changing environment
variables (LLM_API_KEY, LLM_BASE_URL, LLM_MODEL).
"""

from __future__ import annotations

import logging
import re

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from config import AppConfig
from models import LLMResponse

logger = logging.getLogger(__name__)


class LLMClient:
    """Provider-agnostic LLM client using OpenAI-compatible API.

    Supports: Groq, Gemini, OpenRouter, Mistral, Ollama, or any
    OpenAI-compatible endpoint. Switch providers by changing env vars.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self._model = config.llm_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, messages: list[dict], *, json_mode: bool = False) -> str:
        """Send a chat completion request and return the raw response text.

        Args:
            messages: Conversation messages in OpenAI chat format.
            json_mode: When *True*, request JSON-formatted output from the
                model (``response_format={"type": "json_object"}``).

        Returns:
            The text content of the first completion choice.

        Raises:
            APIConnectionError: Network-level connectivity failure.
            APITimeoutError: The request exceeded the configured timeout.
            APIStatusError: The API returned an HTTP error status.
        """
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.3,  # Low temp for deterministic ordering
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if self._config.debug:
                logger.debug("LLM Response: %s", content)
            return content

        except APIConnectionError:
            logger.error("LLM API connection error – is the endpoint reachable?")
            raise
        except APITimeoutError:
            logger.error("LLM API request timed out")
            raise
        except APIStatusError as e:
            logger.error("LLM API HTTP %s: %s", e.status_code, e.message)
            raise
        except Exception as e:
            logger.error("Unexpected LLM API error: %s", e)
            raise

    def chat_structured(self, messages: list[dict]) -> LLMResponse:
        """Send a chat request and parse the response into an ``LLMResponse``.

        Uses JSON mode and attempts multiple parsing strategies to handle
        models that occasionally wrap JSON in markdown fences or emit
        extraneous text.

        Parsing order:
            1. Direct ``model_validate_json`` on raw text.
            2. Extract JSON from ``\\`\\`\\`json … \\`\\`\\``` code blocks.
            3. Find the first balanced ``{ … }`` block in the text.
            4. Fallback: return a minimal ``LLMResponse`` with the raw text.
        """
        raw = self.chat(messages, json_mode=True)

        # Strategy 1 – direct parse
        try:
            return LLMResponse.model_validate_json(raw)
        except Exception:
            logger.debug("Direct JSON parse failed, trying fallbacks…")

        # Strategy 2 – extract from markdown code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if json_match:
            try:
                return LLMResponse.model_validate_json(json_match.group(1).strip())
            except Exception:
                logger.debug("Markdown code-block extraction failed")

        # Strategy 3 – find the first balanced { … } block
        response = self._extract_first_json_object(raw)
        if response is not None:
            return response

        # Strategy 4 – graceful fallback
        logger.warning(
            "Failed to parse LLM response as structured JSON. Raw: %s",
            raw[:500],
        )
        return LLMResponse(
            response_text=raw,
            action="continue",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_first_json_object(text: str) -> LLMResponse | None:
        """Locate the first balanced ``{…}`` block and parse it.

        Returns ``None`` if no valid JSON object is found.
        """
        try:
            start = text.index("{")
        except ValueError:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i in range(start, len(text)):
            char = text[i]

            if escape_next:
                escape_next = False
                continue

            if char == "\\":
                if in_string:
                    escape_next = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return LLMResponse.model_validate_json(text[start : i + 1])
                    except Exception:
                        return None

        return None

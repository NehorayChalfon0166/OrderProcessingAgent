"""Unit tests for voice_handler.py — TwiML generation."""

import pytest
import xml.etree.ElementTree as ET

from voice_handler import (
    build_error_twiml,
    build_goodbye_twiml,
    build_reply_twiml,
    build_welcome_twiml,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse(twiml: str) -> ET.Element:
    """Parse a TwiML string and return the root <Response> element."""
    return ET.fromstring(twiml)


def _find(el: ET.Element, tag: str) -> ET.Element | None:
    """Find the first child element with the given tag (namespace-agnostic)."""
    for child in el:
        if child.tag.endswith(tag):
            return child
    return None


def _find_all(el: ET.Element, tag: str) -> list[ET.Element]:
    """Find all child elements with the given tag."""
    return [child for child in el if child.tag.endswith(tag)]


def _text(el: ET.Element) -> str:
    """Return the text content of an element (or '')."""
    return el.text or ""


# ── build_welcome_twiml ──────────────────────────────────────────────────────


class TestBuildWelcomeTwiML:
    def test_returns_valid_xml(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=marios",
            "Welcome to Mario's! What would you like?",
        )
        root = _parse(twiml)
        assert root.tag == "Response"

    def test_contains_gather_with_say(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=marios",
            "Welcome!",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert gather is not None
        say = _find(gather, "Say")
        assert say is not None
        assert "Welcome!" in _text(say)

    def test_gather_has_speech_attributes(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=marios",
            "Hello",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert gather is not None
        assert gather.get("input") == "speech"
        assert gather.get("speechModel") == "googlev2_telephony"
        assert gather.get("language") == "he-IL"
        assert gather.get("speechTimeout") == "auto"
        assert gather.get("enhanced") == "true"

    def test_gather_action_url_set(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=sakura",
            "Hello",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert gather.get("action") == "/voice/speech-result?restaurant=sakura"

    def test_hints_included_when_provided(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=marios",
            "Hello",
            hints="pizza, burger, fries",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert "pizza" in gather.get("hints", "")

    def test_no_hints_attribute_when_empty(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=marios",
            "Hello",
            hints="",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert gather.get("hints") is None

    def test_say_has_voice_and_language(self):
        twiml = build_welcome_twiml("/voice/speech-result?restaurant=marios", "Hi")
        root = _parse(twiml)
        gather = _find(root, "Gather")
        say = _find(gather, "Say")
        assert say.get("voice") == "Polly.Zeina"
        assert say.get("language") == "he-IL"

    def test_fallback_say_after_gather(self):
        twiml = build_welcome_twiml("/voice/speech-result?restaurant=marios", "Hi")
        root = _parse(twiml)
        # The fallback <Say> is a direct child of <Response> (after <Gather>)
        direct_says = _find_all(root, "Say")
        assert len(direct_says) >= 1
        assert "didn't catch" in _text(direct_says[0])


# ── build_reply_twiml ────────────────────────────────────────────────────────


class TestBuildReplyTwiML:
    def test_returns_valid_xml(self):
        twiml = build_reply_twiml(
            "Added a large pizza!",
            "/voice/speech-result?restaurant=marios",
        )
        root = _parse(twiml)
        assert root.tag == "Response"

    def test_contains_agent_text_and_anything_else(self):
        twiml = build_reply_twiml(
            "Added a large pizza!",
            "/voice/speech-result?restaurant=marios",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        say = _find(gather, "Say")
        text = _text(say)
        assert "Added a large pizza!" in text
        assert "Anything else?" in text

    def test_includes_hints(self):
        twiml = build_reply_twiml(
            "OK", "/voice/speech-result?restaurant=test",
            hints="cola, sprite",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert "cola" in gather.get("hints", "")


# ── build_goodbye_twiml ──────────────────────────────────────────────────────


class TestBuildGoodbyeTwiML:
    def test_returns_valid_xml(self):
        twiml = build_goodbye_twiml("Thank you! Goodbye!")
        root = _parse(twiml)
        assert root.tag == "Response"

    def test_contains_say_and_hangup(self):
        twiml = build_goodbye_twiml("Your order is confirmed!")
        root = _parse(twiml)
        say = _find(root, "Say")
        assert say is not None
        assert "Your order is confirmed!" in _text(say)
        hangup = _find(root, "Hangup")
        assert hangup is not None

    def test_no_gather_element(self):
        twiml = build_goodbye_twiml("Done!")
        root = _parse(twiml)
        assert _find(root, "Gather") is None


# ── build_error_twiml ────────────────────────────────────────────────────────


class TestBuildErrorTwiML:
    def test_returns_valid_xml(self):
        twiml = build_error_twiml("Something went wrong.")
        root = _parse(twiml)
        assert root.tag == "Response"

    def test_contains_say_and_hangup(self):
        twiml = build_error_twiml("Error!")
        root = _parse(twiml)
        assert _find(root, "Say") is not None
        assert _find(root, "Hangup") is not None

    def test_no_gather(self):
        twiml = build_error_twiml("Error!")
        root = _parse(twiml)
        assert _find(root, "Gather") is None


# ── XML escaping ─────────────────────────────────────────────────────────────


class TestXmlEscaping:
    def test_ampersand_escaped(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=test",
            "Fish & Chips",
        )
        assert "Fish &amp; Chips" in twiml or "Fish &amp;amp; Chips" not in twiml
        # The text should contain &amp; (escaped once)
        assert "&amp;" in twiml

    def test_angle_brackets_escaped(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=test",
            "Special <Offer> today",
        )
        assert "&lt;Offer&gt;" in twiml

    def test_quotes_in_text_preserved(self):
        """Double quotes in text content don't need escaping in XML."""
        twiml = build_goodbye_twiml('Your "Mega" pizza is ready!')
        root = _parse(twiml)
        say = _find(root, "Say")
        assert "Mega" in _text(say)

    def test_hints_with_special_chars_escaped(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=test",
            "Hi",
            hints="Fish & Chips, Burger",
        )
        # Check raw XML string — the & must be escaped for valid XML.
        # (ET.fromstring unescapes attributes, so we check the string.)
        assert "Fish &amp; Chips" in twiml


# ── Action URL encoding ──────────────────────────────────────────────────────


class TestActionUrl:
    def test_restaurant_id_in_action_url(self):
        twiml = build_welcome_twiml(
            "/voice/speech-result?restaurant=golden_burger",
            "Welcome!",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert gather.get("action") == "/voice/speech-result?restaurant=golden_burger"

    def test_reply_has_same_action_pattern(self):
        twiml = build_reply_twiml(
            "OK", "/voice/speech-result?restaurant=sakura",
        )
        root = _parse(twiml)
        gather = _find(root, "Gather")
        assert "restaurant=sakura" in gather.get("action", "")

"""Voice handler — TwiML generation for phone-call ordering.

Generates TwiML XML responses for Twilio's Programmable Voice API using the
``<Gather input="speech">`` verb. The caller speaks, Twilio transcribes
(Google STT internally), and the result is POSTed to our webhook.

See ``docs/voice_research.md`` for the architecture rationale and trade-offs
vs. Media Streams.
"""

from __future__ import annotations

from xml.sax.saxutils import escape as _xml_escape

# ── Gather defaults ──────────────────────────────────────────────────────────

_GATHER_ATTRS = (
    ' input="speech"'
    ' speechModel="googlev2_telephony"'
    ' language="he-IL"'
    ' speechTimeout="auto"'
    ' enhanced="true"'
)

_SAY_ATTRS = ' voice="Polly.Zeina" language="he-IL"'

_FALLBACK = "Sorry, I didn't catch that. Please call again. Goodbye!"


# ── Public API ───────────────────────────────────────────────────────────────


def build_welcome_twiml(
    action_url: str,
    greeting: str,
    hints: str = "",
) -> str:
    """Return TwiML for an incoming call — greet the caller and listen.

    The greeting is played inside a ``<Gather>`` so the caller hears the
    prompt and can respond immediately.  If no speech is captured, a
    fallback message is spoken and the call ends.

    Args:
        action_url: Webhook URL that receives the ``SpeechResult`` POST
            (e.g. ``"/voice/speech-result?restaurant=marios"``).
        greeting: Text spoken to the caller (e.g. "Welcome to Mario's! ...").
        hints: Comma-separated words/phrases to boost STT recognition
            (menu item names).

    Returns:
        Valid TwiML XML as a string.
    """
    hints_attr = f' hints="{_xml_escape(hints)}"' if hints else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Gather{_GATHER_ATTRS} action={_xml_quote(action_url)}{hints_attr}>"
        f"<Say{_SAY_ATTRS}>{_xml_escape(greeting)}</Say>"
        "</Gather>"
        f"<Say{_SAY_ATTRS}>{_xml_escape(_FALLBACK)}</Say>"
        "</Response>"
    )


def build_reply_twiml(
    text: str,
    action_url: str,
    hints: str = "",
) -> str:
    """Return TwiML for an ongoing order — speak the agent's reply and listen.

    The agent's response and a short "Anything else?" prompt are played
    inside a ``<Gather>`` so the caller hears the update and can continue
    the conversation in a single turn.

    Args:
        text: The agent's response text (e.g. "Added a large pizza!").
        action_url: Webhook URL for the next ``SpeechResult`` POST.
        hints: Comma-separated STT hints.

    Returns:
        Valid TwiML XML as a string.
    """
    combined = f"{text.strip()} Anything else?"
    hints_attr = f' hints="{_xml_escape(hints)}"' if hints else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Gather{_GATHER_ATTRS} action={_xml_quote(action_url)}{hints_attr}>"
        f"<Say{_SAY_ATTRS}>{_xml_escape(combined)}</Say>"
        "</Gather>"
        f"<Say{_SAY_ATTRS}>{_xml_escape(_FALLBACK)}</Say>"
        "</Response>"
    )


def build_goodbye_twiml(text: str) -> str:
    """Return TwiML that speaks a final message and hangs up.

    Args:
        text: The agent's final response (order summary, goodbye, etc.).

    Returns:
        Valid TwiML XML as a string.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say{_SAY_ATTRS}>{_xml_escape(text)}</Say>"
        "<Hangup/>"
        "</Response>"
    )


def build_error_twiml(text: str) -> str:
    """Return TwiML that speaks an error message and hangs up.

    Args:
        text: Error message for the caller.

    Returns:
        Valid TwiML XML as a string.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say{_SAY_ATTRS}>{_xml_escape(text)}</Say>"
        "<Hangup/>"
        "</Response>"
    )


# ── Internal helpers ─────────────────────────────────────────────────────────


def _xml_quote(value: str) -> str:
    """Quote a value for use as an XML attribute."""
    return f'"{_xml_escape(value)}"'

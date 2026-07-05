"""Tests for twilio_client.py — message sending, webhook parsing, validation."""

from unittest import mock

import pytest

from twilio_client import TwilioClient


# =============================================================================
# Payload Extraction
# =============================================================================


REALISTIC_FORM_BODY = (
    "ToCountry=US&ToState=CA&SmsMessageSid=SM123&NumMedia=0&"
    "ToCity=GB&FromZip=12345&SmsSid=SM123&FromState=CA&SmsStatus=received&"
    "FromCity=GB&Body=I+want+a+pizza&FromCountry=US&To=%2B14155238886&"
    "ToZip=12345&NumSegments=1&MessageSid=SM123&AccountSid=AC123&"
    "From=%2B972539534345&ApiVersion=2010-04-01&"
    "WaId=972539534345&ProfileName=Nehoray"
)


class TestExtractWhatsAppMessage:
    def test_extracts_wa_id_and_body_from_form_data(self):
        result = TwilioClient.extract_whatsapp_message(REALISTIC_FORM_BODY)
        assert result is not None
        wa_id, body = result
        assert wa_id == "972539534345"
        assert body == "I want a pizza"

    def test_handles_bytes_input(self):
        body = REALISTIC_FORM_BODY.encode("utf-8")
        result = TwilioClient.extract_whatsapp_message(body)
        assert result is not None
        assert result[0] == "972539534345"

    def test_returns_none_for_media_message(self):
        form = "WaId=972539534345&NumMedia=2&Body=pic"
        result = TwilioClient.extract_whatsapp_message(form)
        assert result is None

    def test_returns_none_for_empty_body(self):
        form = "WaId=972539534345&Body=&NumMedia=0"
        result = TwilioClient.extract_whatsapp_message(form)
        assert result is None

    def test_returns_none_for_missing_wa_id(self):
        form = "Body=Hi&NumMedia=0"
        result = TwilioClient.extract_whatsapp_message(form)
        assert result is None

    def test_returns_none_for_empty_form(self):
        result = TwilioClient.extract_whatsapp_message("")
        assert result is None


# =============================================================================
# Signature Validation
# =============================================================================


class TestValidateWebhook:
    def test_validation_runs_without_error(self):
        """Smoke test — the Twilio SDK's RequestValidator is used."""
        # Real validation would need a properly signed request.
        # This just verifies the method delegates to the SDK.
        result = TwilioClient.validate_webhook(
            url="https://example.com/webhook",
            params={"Body": "Hi", "From": "whatsapp:+972539534345"},
            signature="bogus_signature_value",
            auth_token="test_token",
        )
        # With a bogus signature it should return False
        assert result is False


# =============================================================================
# Send Message (mocked)
# =============================================================================


class TestSendWhatsAppMessage:
    @mock.patch("twilio_client.Client")
    def test_sends_with_correct_format(self, mock_client_cls):
        mock_client = mock.Mock()
        mock_msg = mock.Mock()
        mock_msg.sid = "SM_test123"
        mock_client.messages.create.return_value = mock_msg
        mock_client_cls.return_value = mock_client

        client = TwilioClient("AC123", "token", "+14155238886")
        sid = client.send_whatsapp_message("972539534345", "Hello!")

        assert sid == "SM_test123"
        mock_client.messages.create.assert_called_once_with(
            body="Hello!",
            from_="whatsapp:+14155238886",
            to="whatsapp:972539534345",
        )

    @mock.patch("twilio_client.Client")
    def test_strips_existing_whatsapp_prefix(self, mock_client_cls):
        mock_client = mock.Mock()
        mock_msg = mock.Mock()
        mock_msg.sid = "SM_test"
        mock_client.messages.create.return_value = mock_msg
        mock_client_cls.return_value = mock_client

        client = TwilioClient("AC123", "token", "+14155238886")
        # Pass a number that already has "whatsapp:" prefix
        client.send_whatsapp_message("whatsapp:972539534345", "Hi")

        mock_client.messages.create.assert_called_once_with(
            body="Hi",
            from_="whatsapp:+14155238886",
            to="whatsapp:972539534345",  # no double-wrapping
        )

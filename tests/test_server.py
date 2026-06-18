"""Tests for server.py — FastAPI webhook endpoints (Twilio)."""

import asyncio
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_deps():
    """Inject mock dependencies into the server module globals."""
    import server as srv

    mock_cat = mock.Mock()
    mock_cat.restaurant_name = "Test Pizzeria"
    mock_cat.menu_data = {"restaurant_name": "Test Pizzeria", "products": [], "deals": []}
    mock_cat.get_hints.return_value = {}

    mock_prc = mock.Mock()
    mock_llm = mock.Mock()
    mock_wa = mock.Mock()
    mock_wa.auth_token = "test_token"
    mock_router = mock.Mock()
    mock_router.sessions_dir = "/tmp/test_sessions"

    orig = {
        "_catalogue": srv._catalogue,
        "_pricing": srv._pricing,
        "_llm": srv._llm,
        "_twilio": srv._twilio,
        "_router": srv._router,
        "_orders_dir": srv._orders_dir,
        "_locks": dict(srv._locks),
        "_lock_access": dict(srv._lock_access),
    }

    srv._catalogue = mock_cat
    srv._pricing = mock_prc
    srv._llm = mock_llm
    srv._twilio = mock_wa
    srv._router = mock_router
    srv._orders_dir = "/tmp/test_orders"
    srv._locks.clear()
    srv._lock_access.clear()

    yield {"catalogue": mock_cat, "pricing": mock_prc, "llm": mock_llm,
           "whatsapp": mock_wa, "router": mock_router}

    srv._catalogue = orig["_catalogue"]
    srv._pricing = orig["_pricing"]
    srv._llm = orig["_llm"]
    srv._twilio = orig["_twilio"]
    srv._router = orig["_router"]
    srv._orders_dir = orig["_orders_dir"]
    srv._locks = orig["_locks"]
    srv._lock_access = orig["_lock_access"]


@pytest.fixture
def client(mock_deps):
    """FastAPI test client with mocked dependencies and signature validation."""
    import server as srv

    with mock.patch.object(
        __import__("twilio_client").TwilioClient,
        "validate_webhook",
        return_value=True,
    ):
        yield TestClient(srv.app)


# =============================================================================
# POST /whatsapp/webhook — Message Processing
# =============================================================================


class TestReceiveMessage:
    def test_ignores_non_text_messages(self, client, mock_deps):
        """Status updates and read receipts return empty TwiML."""
        form_data = "WaId=972539534345&NumMedia=2&Body=pic"
        resp = client.post(
            "/whatsapp/webhook",
            content=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200
        mock_deps["router"].get_or_create.assert_not_called()

    def test_processes_text_message(self, client, mock_deps):
        """A text message is processed through the agent loop."""
        from session import OrderSession

        session = OrderSession()
        session.session_id = "972539534345"
        mock_deps["router"].get_or_create.return_value = session

        form_data = "WaId=972539534345&Body=I+want+a+pizza&NumMedia=0&From=whatsapp"
        resp = client.post(
            "/whatsapp/webhook",
            content=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert resp.status_code == 200
        mock_deps["router"].get_or_create.assert_called_once_with("972539534345")
        mock_deps["whatsapp"].send_whatsapp_message.assert_called_once()

    def test_agent_error_returns_fallback_message(self, client, mock_deps):
        """If process_turn blows up, respond with a fallback."""
        from session import OrderSession

        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session

        with mock.patch("server.process_turn", side_effect=RuntimeError("boom")):
            form_data = "WaId=972539534345&Body=Hi&NumMedia=0&From=whatsapp"
            resp = client.post(
                "/whatsapp/webhook",
                content=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert resp.status_code == 200
            call_args = mock_deps["whatsapp"].send_whatsapp_message.call_args
            assert "sorry" in call_args[0][1].lower()


# =============================================================================
# Per-Session Locking
# =============================================================================


class TestSessionLocking:
    def test_lock_created_per_phone(self, client, mock_deps):
        """Each phone gets its own asyncio.Lock."""
        from session import OrderSession
        import server as srv

        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session

        client.post(
            "/whatsapp/webhook",
            content="WaId=972539534345&Body=A&NumMedia=0&From=whatsapp",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert "972539534345" in srv._locks
        assert isinstance(srv._locks["972539534345"], asyncio.Lock)

    def test_different_phones_get_different_locks(self, client, mock_deps):
        """Phone A and phone B do not share a lock."""
        from session import OrderSession
        import server as srv

        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session

        for phone in ("972539534345", "972539876543"):
            client.post(
                "/whatsapp/webhook",
                content=f"WaId={phone}&Body=Hi&NumMedia=0&From=whatsapp",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        assert srv._locks["972539534345"] is not srv._locks["972539876543"]


# =============================================================================
# Stale Session Detection
# =============================================================================


class TestStaleSession:
    def test_recent_session_is_not_stale(self):
        from server import _is_session_stale
        from session import OrderSession
        s = OrderSession()
        assert not _is_session_stale(s)

    def test_old_session_is_stale(self):
        from server import _is_session_stale
        from datetime import datetime, timedelta, timezone
        from session import OrderSession
        s = OrderSession()
        s.updated_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert _is_session_stale(s)


class TestSignatureValidation:
    def test_returns_500_when_twilio_not_initialised(self, mock_deps):
        import server as srv
        srv._twilio = None
        with mock.patch.object(
            __import__("twilio_client").TwilioClient,
            "validate_webhook", return_value=True,
        ):
            from fastapi.testclient import TestClient
            client = TestClient(srv.app)
            form_data = "WaId=972539534345&Body=Hi&NumMedia=0&From=whatsapp"
            resp = client.post(
                "/whatsapp/webhook", content=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert resp.status_code == 500
            assert "not configured" in resp.text.lower()


class TestLockEviction:
    def test_sweep_removes_stale(self):
        import server as srv
        import time
        srv._locks.clear()
        srv._lock_access.clear()
        srv._locks["a"] = asyncio.Lock()
        srv._locks["b"] = asyncio.Lock()
        srv._lock_access["a"] = time.monotonic() - 99999  # very stale
        srv._lock_access["b"] = time.monotonic()          # fresh
        orig = srv._MAX_LOCKS_BEFORE_SWEEP
        srv._MAX_LOCKS_BEFORE_SWEEP = 1
        try:
            srv._sweep_stale_locks()
        finally:
            srv._MAX_LOCKS_BEFORE_SWEEP = orig
        assert "a" not in srv._locks
        assert "b" in srv._locks


class TestEventLoopOffloading:
    def test_process_turn_runs_in_thread(self, client, mock_deps):
        from session import OrderSession
        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session
        with mock.patch("server.asyncio.to_thread") as mock_tt:
            mock_tt.return_value = "Thanks!"
            form_data = "WaId=972539534345&Body=Hi&NumMedia=0&From=whatsapp"
            client.post(
                "/whatsapp/webhook", content=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert mock_tt.called

"""Tests for server.py — FastAPI webhook endpoints (Twilio)."""

import asyncio
from unittest import mock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_restaurant_ctx(rid="marios_pizzeria", name="Test Pizzeria"):
    """Build a mock RestaurantContext with catalogue + pricing."""
    mock_cat = mock.Mock()
    mock_cat.restaurant_name = name
    mock_cat.menu_data = {"restaurant_name": name, "products": [], "deals": []}
    mock_cat.get_hints.return_value = {}

    mock_prc = mock.Mock()
    mock_prc.compute_totals.return_value = (20.0, 3.99, 23.99)

    mock_config = mock.Mock()
    mock_config.id = rid
    mock_config.name = name
    mock_config.owner_phone = "+15551234567"

    ctx = mock.Mock()
    ctx.config = mock_config
    ctx.catalogue = mock_cat
    ctx.pricing = mock_prc
    return ctx


def _form_data(wa_id="972539534345", body="Hi", to="whatsapp:+14155238886"):
    """Build a Twilio-like form-encoded body string.

    The To number is URL-encoded so parse_qs decodes it correctly
    (bare ``+`` is interpreted as a space in form-encoded data).
    """
    from urllib.parse import quote
    to_encoded = quote(to, safe="")
    return (
        f"WaId={wa_id}&Body={body}&NumMedia=0"
        f"&From=whatsapp%3A%2B{wa_id}"
        f"&To={to_encoded}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_deps():
    """Inject mock dependencies into the server module globals."""
    import server as srv

    mock_reg = mock.Mock()
    mock_llm = mock.Mock()
    mock_wa = mock.Mock()
    mock_wa.auth_token = "test_token"
    mock_router = mock.Mock()
    mock_router.sessions_dir = "/tmp/test_sessions"

    orig = {
        "_registry": srv._registry,
        "_llm": srv._llm,
        "_twilio": srv._twilio,
        "_router": srv._router,
        "_orders_dir": srv._orders_dir,
        "_locks": dict(srv._locks),
        "_lock_access": dict(srv._lock_access),
    }

    srv._registry = mock_reg
    srv._llm = mock_llm
    srv._twilio = mock_wa
    srv._router = mock_router
    srv._orders_dir = "/tmp/test_orders"
    srv._locks.clear()
    srv._lock_access.clear()

    yield {"registry": mock_reg, "llm": mock_llm,
           "whatsapp": mock_wa, "router": mock_router}

    srv._registry = orig["_registry"]
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
        form_data = "WaId=972539534345&NumMedia=2&Body=pic&To=whatsapp:+14155238886"
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

        ctx = _make_restaurant_ctx("marios_pizzeria")
        mock_deps["registry"].get_by_twilio_phone.return_value = ctx

        session = OrderSession()
        session.session_id = "972539534345"
        mock_deps["router"].get_or_create.return_value = session

        resp = client.post(
            "/whatsapp/webhook",
            content=_form_data(body="I want a pizza"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert resp.status_code == 200
        # get_or_create now takes (restaurant_id, phone_number, db=...)
        mock_deps["router"].get_or_create.assert_called_once_with(
            "marios_pizzeria", "972539534345", db=mock.ANY
        )
        mock_deps["whatsapp"].send_whatsapp_message.assert_called_once()

    def test_unknown_restaurant_returns_500(self, client, mock_deps):
        """Webhook with unrecognized To number returns 500."""
        mock_deps["registry"].get_by_twilio_phone.return_value = None

        resp = client.post(
            "/whatsapp/webhook",
            content=_form_data(to="whatsapp:+0000000000"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 500

    def test_agent_error_returns_fallback_message(self, client, mock_deps):
        """If process_turn blows up, respond with a fallback."""
        from session import OrderSession

        ctx = _make_restaurant_ctx()
        mock_deps["registry"].get_by_twilio_phone.return_value = ctx

        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session

        with mock.patch("server.process_turn", side_effect=RuntimeError("boom")):
            resp = client.post(
                "/whatsapp/webhook",
                content=_form_data(body="Hi"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert resp.status_code == 200
            call_args = mock_deps["whatsapp"].send_whatsapp_message.call_args
            assert "sorry" in call_args[0][1].lower()

    def test_routes_to_correct_restaurant_by_to_field(self, client, mock_deps):
        """To field determines which restaurant context is used."""
        from session import OrderSession

        ctx = _make_restaurant_ctx("luigis")
        mock_deps["registry"].get_by_twilio_phone.return_value = ctx

        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session

        client.post(
            "/whatsapp/webhook",
            content=_form_data(to="whatsapp:+14151234567"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        mock_deps["registry"].get_by_twilio_phone.assert_called_once_with(
            "+14151234567"
        )


# =============================================================================
# Per-Session Locking
# =============================================================================


class TestSessionLocking:
    def _setup_session(self, mock_deps, rid="marios_pizzeria"):
        from session import OrderSession
        ctx = _make_restaurant_ctx(rid)
        mock_deps["registry"].get_by_twilio_phone.return_value = ctx
        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session
        return session

    def test_lock_created_per_identity(self, client, mock_deps):
        """Each (restaurant, phone) pair gets its own asyncio.Lock."""
        import server as srv

        self._setup_session(mock_deps)

        client.post(
            "/whatsapp/webhook",
            content=_form_data(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        lock_key = "marios_pizzeria:972539534345"
        assert lock_key in srv._locks
        assert isinstance(srv._locks[lock_key], asyncio.Lock)

    def test_different_restaurants_get_different_locks(self, client, mock_deps):
        """Same phone, different restaurant → different locks."""
        import server as srv

        self._setup_session(mock_deps, rid="marios")
        client.post(
            "/whatsapp/webhook",
            content=_form_data(wa_id="972539534345", to="whatsapp:+1111111111"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self._setup_session(mock_deps, rid="luigis")
        client.post(
            "/whatsapp/webhook",
            content=_form_data(wa_id="972539534345", to="whatsapp:+2222222222"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert srv._locks["marios:972539534345"] is not srv._locks["luigis:972539534345"]


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


# =============================================================================
# Signature Validation
# =============================================================================


class TestSignatureValidation:
    def test_returns_500_when_twilio_not_initialised(self, mock_deps):
        import server as srv
        srv._twilio = None
        with mock.patch.object(
            __import__("twilio_client").TwilioClient,
            "validate_webhook", return_value=True,
        ):
            from fastapi.testclient import TestClient as TC
            client = TC(srv.app)
            form_data = _form_data()
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
        ctx = _make_restaurant_ctx()
        mock_deps["registry"].get_by_twilio_phone.return_value = ctx
        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session
        with mock.patch("server.asyncio.to_thread") as mock_tt:
            mock_tt.return_value = "Thanks!"
            client.post(
                "/whatsapp/webhook",
                content=_form_data(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert mock_tt.called

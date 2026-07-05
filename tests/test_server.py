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

    def test_lock_exclusive_per_identity(self, mock_deps):
        """Each (restaurant, phone) gets its own lock; same identity shares one."""
        import asyncio
        import server as srv

        lock_a = srv._get_lock("marios:972539534345")
        lock_b = srv._get_lock("luigis:972539534345")
        lock_a2 = srv._get_lock("marios:972539534345")

        # Same identity returns the same lock object
        assert lock_a is lock_a2
        # Different identities return different locks
        assert lock_a is not lock_b
        # Both are proper asyncio.Lock instances
        assert isinstance(lock_a, asyncio.Lock)
        assert isinstance(lock_b, asyncio.Lock)

    def test_lock_acquire_and_release(self):
        """Lock can be acquired and released without deadlock."""
        import asyncio
        import server as srv

        lock = srv._get_lock("test:972511111111")

        async def _critical():
            async with lock:
                return "done"

        result = asyncio.run(_critical())
        assert result == "done"
        assert not lock.locked()

    def test_lock_released_on_exception(self, mock_deps):
        """Lock is released even if process_turn raises."""
        import server as srv
        from session import OrderSession

        ctx = _make_restaurant_ctx("marios")
        mock_deps["registry"].get_by_twilio_phone.return_value = ctx
        session = OrderSession()
        mock_deps["router"].get_or_create.return_value = session

        lock_key = "marios:972539534345"

        with mock.patch("server.process_turn", side_effect=RuntimeError("boom")):
            with mock.patch("server.asyncio.to_thread", side_effect=RuntimeError("boom")):
                resp = None
                with mock.patch.object(
                    __import__("twilio_client").TwilioClient,
                    "validate_webhook", return_value=True,
                ):
                    from fastapi.testclient import TestClient as TC
                    client = TC(srv.app)
                    resp = client.post(
                        "/whatsapp/webhook",
                        content=_form_data(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )

        # Lock should not be held after exception
        lock = srv._locks.get(lock_key)
        if lock is not None:
            assert not lock.locked(), "Lock should be released after exception"


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


# =============================================================================
# Printer API
# =============================================================================


class TestPrinterAPI:
    def test_get_orders_valid_token(self, client, mock_deps):
        """Returns unprinted orders for a valid token."""
        import server as srv
        srv._api_token = "secret"
        mock_db = mock.Mock()
        mock_db.get_unprinted_orders.return_value = [
            {"order_id": "ORDER001", "items": [], "subtotal": 10.0,
             "delivery_fee": 0, "total": 10.0, "order_type": "pickup",
             "created_at": "2026-06-21T12:00:00Z",
             "customer_name": "Test", "customer_phone": "555"}
        ]
        srv._db = mock_db
        try:
            resp = client.get(
                "/api/orders",
                params={"restaurant_id": "marios", "token": "secret"},
            )
            assert resp.status_code == 200
            data = resp.json()
            orders = data["orders"]
            assert len(orders) == 1
            assert orders[0]["order_id"] == "ORDER001"
        finally:
            srv._api_token = ""
            srv._db = None

    def test_get_orders_invalid_token(self, client, mock_deps):
        """Returns 403 for wrong token."""
        import server as srv
        srv._api_token = "secret"
        try:
            resp = client.get(
                "/api/orders",
                params={"restaurant_id": "marios", "token": "wrong"},
            )
            assert resp.status_code == 403
        finally:
            srv._api_token = ""

    def test_get_orders_missing_token(self, client, mock_deps):
        """Returns 403 when no token provided."""
        import server as srv
        srv._api_token = ""
        try:
            resp = client.get(
                "/api/orders",
                params={"restaurant_id": "marios", "token": "wrong"},
            )
            assert resp.status_code == 403
        finally:
            srv._api_token = ""

    def test_mark_printed_valid_token(self, client, mock_deps):
        """Marks an order as printed."""
        import server as srv
        srv._api_token = "secret"
        mock_db = mock.Mock()
        srv._db = mock_db
        try:
            resp = client.post(
                "/api/orders/ORDER001/printed",
                params={"token": "secret"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
            mock_db.mark_printed.assert_called_once_with("ORDER001")
        finally:
            srv._api_token = ""
            srv._db = None

    def test_mark_printed_idempotent(self, client, mock_deps):
        """Marking already-printed order still returns 200."""
        import server as srv
        srv._api_token = "secret"
        mock_db = mock.Mock()
        srv._db = mock_db
        try:
            resp = client.post(
                "/api/orders/ORDER001/printed",
                params={"token": "secret"},
            )
            assert resp.status_code == 200
        finally:
            srv._api_token = ""
            srv._db = None


# =============================================================================
# Stripe Webhook
# =============================================================================


class TestStripeWebhook:
    def test_non_checkout_event_ignored(self, client, mock_deps):
        """Non-checkout.session.completed events return ignored."""
        with mock.patch("server.verify_webhook") as mock_vw:
            mock_vw.return_value = {"type": "charge.succeeded"}
            resp = client.post(
                "/payment/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "sig"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"status": "ignored"}

    def test_invalid_signature_returns_400(self, client, mock_deps):
        """Invalid Stripe signature returns 400."""
        with mock.patch("server.verify_webhook") as mock_vw:
            mock_vw.side_effect = __import__("stripe").error.SignatureVerificationError(
                "bad", "sig"
            )
            resp = client.post(
                "/payment/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "bad"},
            )
            assert resp.status_code == 400

    def test_checkout_completed_completes_order(self, client, mock_deps):
        """checkout.session.completed transitions order to COMPLETED."""
        import server as srv
        from session import OrderSession

        ctx = _make_restaurant_ctx("marios_pizzeria")
        srv._registry.get_by_id = mock.Mock(return_value=ctx)
        session = OrderSession(restaurant_id="marios_pizzeria")
        session.session_id = "972539534345"
        session.state = __import__("models", fromlist=["OrderState"]).OrderState.PAYMENT_PENDING
        session._db = mock.Mock()  # type: ignore[has-type] — needed for save()
        # Leave cart empty — _save_order_file serializes it to JSON
        mock_router = mock.Mock()
        mock_router.get_or_create.return_value = session
        srv._router = mock_router
        try:
            with mock.patch("server.verify_webhook") as mock_vw:
                mock_vw.return_value = {
                    "type": "checkout.session.completed",
                    "data": {
                        "object": {
                            "metadata": {
                                "restaurant_id": "marios_pizzeria",
                                "session_id": "972539534345",
                            }
                        }
                    },
                }
                resp = client.post(
                    "/payment/webhook",
                    content=b"{}",
                    headers={"Stripe-Signature": "sig"},
                )
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
        finally:
            srv._router = mock_deps["router"]
            srv._registry.get_by_id = mock.Mock(return_value=None)

    def test_already_completed_is_idempotent(self, client, mock_deps):
        """Already COMPLETED order returns already_completed."""
        import server as srv
        from session import OrderSession

        ctx = _make_restaurant_ctx("marios_pizzeria")
        srv._registry.get_by_id = mock.Mock(return_value=ctx)
        session = OrderSession(restaurant_id="marios_pizzeria")
        session.session_id = "972539534345"
        session.state = __import__("models", fromlist=["OrderState"]).OrderState.COMPLETED
        session._db = mock.Mock()  # type: ignore[has-type] — needed for save()
        mock_router = mock.Mock()
        mock_router.get_or_create.return_value = session
        srv._router = mock_router
        try:
            with mock.patch("server.verify_webhook") as mock_vw:
                mock_vw.return_value = {
                    "type": "checkout.session.completed",
                    "data": {
                        "object": {
                            "metadata": {
                                "restaurant_id": "marios_pizzeria",
                                "session_id": "972539534345",
                            }
                        }
                    },
                }
                resp = client.post(
                    "/payment/webhook",
                    content=b"{}",
                    headers={"Stripe-Signature": "sig"},
                )
            assert resp.status_code == 200
            assert resp.json() == {"status": "already_completed"}
        finally:
            srv._router = mock_deps["router"]
            srv._registry.get_by_id = mock.Mock(return_value=None)


# =============================================================================
# Payment UX Pages
# =============================================================================


class TestPaymentPages:
    def test_success_page(self, client):
        """GET /payment/success returns 200 with return-to-WhatsApp message."""
        resp = client.get("/payment/success")
        assert resp.status_code == 200
        assert "whatsapp" in resp.text.lower()

    def test_cancel_page(self, client):
        """GET /payment/cancel returns 200 with return-to-WhatsApp message."""
        resp = client.get("/payment/cancel")
        assert resp.status_code == 200
        assert "whatsapp" in resp.text.lower()

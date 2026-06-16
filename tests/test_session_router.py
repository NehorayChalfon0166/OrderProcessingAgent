"""Tests for session_router.py — phone → session mapping and stale detection."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from session import OrderSession
from session_router import SessionRouter


class TestSessionRouter:
    def test_creates_new_session_for_new_phone(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            session = router.get_or_create("+972539534345")

            assert isinstance(session, OrderSession)
            assert session.session_id == "972539534345"
            assert (Path(tmpdir) / "972539534345.json").exists()

    def test_returns_existing_session_on_second_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            s1 = router.get_or_create("+972539534345")
            s1.cart.append(
                __import__("models").CartItem(
                    product_id="pizza_test",
                    name="Test Pizza",
                    category="Pizzas",
                    base_price=10.0,
                    line_total=10.0,
                )
            )
            s1.save(router.sessions_dir)

            s2 = router.get_or_create("+972539534345")
            assert s2.session_id == "972539534345"
            assert len(s2.cart) == 1
            assert s2.cart[0].product_id == "pizza_test"

    def test_different_phones_get_different_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            s1 = router.get_or_create("+972539534345")
            s2 = router.get_or_create("+972539876543")

            assert s1.session_id != s2.session_id

    def test_sanitize_strips_special_chars(self):
        assert SessionRouter._sanitize("+972 (53) 953-4345") == "972539534345"
        assert SessionRouter._sanitize("whatsapp:+972539534345") == "972539534345"
        assert SessionRouter._sanitize("972539534345") == "972539534345"

    def test_handles_corrupted_session_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_path = Path(tmpdir) / "972539534345.json"
            bad_path.write_text("this is not valid json {{{")

            router = SessionRouter(tmpdir)
            session = router.get_or_create("+972539534345")
            assert isinstance(session, OrderSession)
            assert session.session_id == "972539534345"
            assert len(session.cart) == 0

    def test_session_dir_created_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "nested" / "sessions"
            router = SessionRouter(str(subdir))
            session = router.get_or_create("+972539534345")
            assert subdir.exists()
            assert (subdir / "972539534345.json").exists()

"""Tests for session_router.py — (restaurant_id, phone) → session mapping."""

import tempfile
from pathlib import Path

from models import CartItem, OrderState
from session import OrderSession
from session_router import SessionRouter

RESTAURANT = "marios_pizzeria"


class TestSessionRouter:
    def test_creates_new_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            session = router.get_or_create(RESTAURANT, "+972539534345")

            assert isinstance(session, OrderSession)
            assert session.session_id == "972539534345"
            assert session.restaurant_id == RESTAURANT
            assert (Path(tmpdir) / RESTAURANT / "972539534345.json").exists()

    def test_saves_in_restaurant_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            router.get_or_create("marios", "+972539534345")
            router.get_or_create("luigis", "+972539534345")

            assert (Path(tmpdir) / "marios" / "972539534345.json").exists()
            assert (Path(tmpdir) / "luigis" / "972539534345.json").exists()

    def test_returns_existing_session_on_second_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            s1 = router.get_or_create(RESTAURANT, "+972539534345")
            s1.cart.append(
                CartItem(
                    product_id="pizza_test",
                    name="Test Pizza",
                    category="Pizzas",
                    base_price=10.0,
                    line_total=10.0,
                )
            )
            s1.save(str(Path(tmpdir) / RESTAURANT))

            s2 = router.get_or_create(RESTAURANT, "+972539534345")
            assert s2.session_id == "972539534345"
            assert s2.restaurant_id == RESTAURANT
            assert len(s2.cart) == 1
            assert s2.cart[0].product_id == "pizza_test"

    def test_same_phone_different_restaurant_gets_different_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            s1 = router.get_or_create("marios", "+972539534345")
            s2 = router.get_or_create("luigis", "+972539534345")

            # Both have the same session_id (sanitized phone), different restaurant_id
            assert s1.session_id == "972539534345"
            assert s2.session_id == "972539534345"
            assert s1.restaurant_id == "marios"
            assert s2.restaurant_id == "luigis"
            # They are different objects with independent carts
            s1.cart.append(
                CartItem(
                    product_id="pizza_test",
                    name="Test Pizza",
                    category="Pizzas",
                    base_price=10.0,
                    line_total=10.0,
                )
            )
            assert len(s1.cart) == 1
            assert len(s2.cart) == 0

    def test_sanitize_strips_special_chars(self):
        assert SessionRouter._sanitize("+972 (53) 953-4345") == "972539534345"
        assert SessionRouter._sanitize("whatsapp:+972539534345") == "972539534345"
        assert SessionRouter._sanitize("972539534345") == "972539534345"

    def test_handles_corrupted_session_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / RESTAURANT
            session_dir.mkdir(parents=True)
            bad_path = session_dir / "972539534345.json"
            bad_path.write_text("this is not valid json {{{")

            router = SessionRouter(tmpdir)
            session = router.get_or_create(RESTAURANT, "+972539534345")
            assert isinstance(session, OrderSession)
            assert session.session_id == "972539534345"
            assert session.restaurant_id == RESTAURANT
            assert len(session.cart) == 0

    def test_session_dir_created_if_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "nested" / "sessions"
            router = SessionRouter(str(subdir))
            session = router.get_or_create(RESTAURANT, "+972539534345")
            expected_dir = subdir / RESTAURANT
            assert expected_dir.exists()
            assert (expected_dir / "972539534345.json").exists()

    def test_creates_new_for_terminal_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SessionRouter(tmpdir)
            s1 = router.get_or_create(RESTAURANT, "+972539534345")
            s1.cart.append(
                CartItem(
                    product_id="old_item",
                    name="Old Item",
                    category="Test",
                    base_price=5.0,
                    line_total=5.0,
                )
            )
            s1.state = OrderState.COMPLETED
            s1.save(str(Path(tmpdir) / RESTAURANT))

            s2 = router.get_or_create(RESTAURANT, "+972539534345")
            assert s2.session_id == "972539534345"
            assert s2.restaurant_id == RESTAURANT
            assert s2.state == OrderState.BUILDING
            assert len(s2.cart) == 0

    def test_sets_restaurant_id_on_existing_session_without_one(self):
        """Session loaded from file that lacks restaurant_id gets it set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Manually create a session file without restaurant_id
            session_dir = Path(tmpdir) / RESTAURANT
            session_dir.mkdir(parents=True)
            session = OrderSession()
            session.session_id = "972539534345"
            session.restaurant_id = ""  # Simulate legacy
            session.save(str(session_dir))

            router = SessionRouter(tmpdir)
            loaded = router.get_or_create(RESTAURANT, "+972539534345")
            assert loaded.restaurant_id == RESTAURANT

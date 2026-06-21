"""Tests for session_router.py — (restaurant_id, phone) → session mapping."""

from db import Database
from models import CartItem, OrderState
from session import OrderSession
from session_router import SessionRouter

RESTAURANT = "marios_pizzeria"


def _make_db(tmp_path, name="test.db"):
    return Database(str(tmp_path / name))


class TestSessionRouter:
    def test_creates_new_session(self, tmp_path):
        db = _make_db(tmp_path)
        router = SessionRouter()
        session = router.get_or_create(RESTAURANT, "+972539534345", db)

        assert isinstance(session, OrderSession)
        assert session.session_id == "972539534345"
        assert session.restaurant_id == RESTAURANT
        # Verify persisted in DB
        loaded = db.load_session(RESTAURANT, "972539534345")
        assert loaded is not None

    def test_different_restaurants_isolated(self, tmp_path):
        db = _make_db(tmp_path)
        router = SessionRouter()
        router.get_or_create("marios", "+972539534345", db)
        router.get_or_create("luigis", "+972539534345", db)

        assert db.load_session("marios", "972539534345") is not None
        assert db.load_session("luigis", "972539534345") is not None

    def test_returns_existing_session_on_second_call(self, tmp_path):
        db = _make_db(tmp_path)
        router = SessionRouter()
        s1 = router.get_or_create(RESTAURANT, "+972539534345", db)
        s1.cart.append(
            CartItem(
                product_id="pizza_test", name="Test Pizza",
                category="Pizzas", base_price=10.0, line_total=10.0,
            )
        )
        s1.save()

        s2 = router.get_or_create(RESTAURANT, "+972539534345", db)
        assert s2.session_id == "972539534345"
        assert len(s2.cart) == 1
        assert s2.cart[0].product_id == "pizza_test"

    def test_same_phone_different_restaurant_gets_different_sessions(self, tmp_path):
        db = _make_db(tmp_path)
        router = SessionRouter()
        s1 = router.get_or_create("marios", "+972539534345", db)
        s2 = router.get_or_create("luigis", "+972539534345", db)

        assert s1.session_id == s2.session_id == "972539534345"
        assert s1.restaurant_id == "marios"
        assert s2.restaurant_id == "luigis"
        s1.cart.append(
            CartItem(
                product_id="pizza_test", name="Test Pizza",
                category="Pizzas", base_price=10.0, line_total=10.0,
            )
        )
        assert len(s1.cart) == 1
        assert len(s2.cart) == 0

    def test_sanitize_strips_special_chars(self):
        assert SessionRouter._sanitize("+972 (53) 953-4345") == "972539534345"
        assert SessionRouter._sanitize("whatsapp:+972539534345") == "972539534345"
        assert SessionRouter._sanitize("972539534345") == "972539534345"

    def test_creates_new_for_terminal_session(self, tmp_path):
        db = _make_db(tmp_path)
        router = SessionRouter()
        s1 = router.get_or_create(RESTAURANT, "+972539534345", db)
        s1.cart.append(
            CartItem(
                product_id="old_item", name="Old Item",
                category="Test", base_price=5.0, line_total=5.0,
            )
        )
        s1.state = OrderState.COMPLETED
        s1.save()

        s2 = router.get_or_create(RESTAURANT, "+972539534345", db)
        assert s2.session_id == "972539534345"
        assert s2.state == OrderState.BUILDING
        assert len(s2.cart) == 0

    def test_sets_restaurant_id_on_existing_session_without_one(self, tmp_path):
        """Session loaded from DB that lacks restaurant_id gets it set."""
        db = _make_db(tmp_path)
        # Manually save a session without restaurant_id
        session = OrderSession()
        session.session_id = "972539534345"
        session.restaurant_id = ""
        session._db = db  # type: ignore[has-type]
        session.save()

        router = SessionRouter()
        loaded = router.get_or_create(RESTAURANT, "+972539534345", db)
        assert loaded.restaurant_id == RESTAURANT

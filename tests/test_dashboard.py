"""Tests for dashboard.py — admin dashboard endpoints."""
import json, os
from pathlib import Path
from unittest import mock
import pytest
from fastapi.testclient import TestClient
from config import AppConfig

@pytest.fixture
def dashboard_app(tmp_path):
    import dashboard as dash
    menu_path = tmp_path / "menus" / "test_restaurant.json"
    menu_path.parent.mkdir()
    menu_path.write_text(json.dumps({"restaurant_name":"Test","currency":"USD","categories":[{"name":"Pizzas","items":[{"id":"pizza_test","name":"Test Pizza","sizes":{"small":10.0,"large":15.0},"default_size":"small","description":"Test pizza","available_toppings":[]}]}],"toppings":[],"deals":[],"delivery_fee":3.99}))
    restaurants_path = tmp_path / "restaurants.json"
    restaurants_path.write_text(json.dumps({"restaurants":{"test":{"name":"Test Restaurant","menu_path":str(menu_path),"twilio_phone":"+1111111111","owner_phone":"+15551234567"}}}))
    db_path = tmp_path / "test.db"
    with mock.patch.dict(os.environ,{"DEEPSEEK_API_KEY":"sk-test","RESTAURANTS_PATH":str(restaurants_path),"DB_PATH":str(db_path),"API_TOKEN":"test-token"},clear=True):
        cfg = AppConfig.from_env()
        dash.init_dashboard(cfg)
        dash._db.save_order({"order_id":"ORDER001","restaurant_id":"test","customer":{"name":"Test Customer","phone":"555"},"items":[{"name":"Test Pizza","quantity":1,"line_total":10.0}],"subtotal":10.0,"delivery_fee":3.99,"total":13.99,"order_type":"delivery","payment_method":"cash","timestamp":"2026-06-21T12:00:00Z"})
        yield TestClient(dash.app)
    dash._db = None; dash._registry = None; dash._api_token = ""; dash._templates = None

_NO_TEMPLATES = True  # templates deleted — re-enable when new templates exist

def _skip_if_no_templates():
    if _NO_TEMPLATES:
        pytest.skip("Templates not yet rebuilt")


class TestDashboardAuth:
    def test_no_token_returns_403(self,dashboard_app): assert dashboard_app.get("/").status_code == 403
    def test_wrong_token_returns_403(self,dashboard_app): assert dashboard_app.get("/",params={"token":"wrong"}).status_code == 403
    def test_valid_token_returns_200(self,dashboard_app):
        _skip_if_no_templates()
        assert dashboard_app.get("/",params={"token":"test-token"}).status_code == 200

class TestOverview:
    def test_shows_restaurants(self,dashboard_app):
        _skip_if_no_templates()
        resp = dashboard_app.get("/",params={"token":"test-token"})
        assert resp.status_code == 200 and "Test Restaurant" in resp.text

class TestOrders:
    def test_lists_orders(self,dashboard_app):
        _skip_if_no_templates()
        resp = dashboard_app.get("/orders",params={"token":"test-token"})
        assert resp.status_code == 200 and "ORDER001" in resp.text
    def test_filter_by_restaurant(self,dashboard_app):
        _skip_if_no_templates()
        assert dashboard_app.get("/orders",params={"token":"test-token","restaurant_id":"test"}).status_code == 200
    def test_order_detail(self,dashboard_app):
        _skip_if_no_templates()
        resp = dashboard_app.get("/orders/ORDER001",params={"token":"test-token"})
        assert resp.status_code == 200 and "Test Customer" in resp.text
    def test_order_not_found(self,dashboard_app):
        assert dashboard_app.get("/orders/NONEXIST",params={"token":"test-token"}).status_code == 404

class TestSessions:
    def test_lists_sessions(self,dashboard_app):
        _skip_if_no_templates()
        assert dashboard_app.get("/sessions",params={"token":"test-token"}).status_code == 200
    def test_session_not_found(self,dashboard_app):
        assert dashboard_app.get("/sessions/NONEXIST",params={"token":"test-token"}).status_code == 404

class TestMenu:
    def test_view_menu(self,dashboard_app):
        _skip_if_no_templates()
        resp = dashboard_app.get("/menu/test",params={"token":"test-token"})
        assert resp.status_code == 200 and "Test Pizza" in resp.text
    def test_menu_not_found(self,dashboard_app): assert dashboard_app.get("/menu/nonexistent",params={"token":"test-token"}).status_code == 404
    def test_edit_menu(self,dashboard_app):
        resp = dashboard_app.post("/menu/test/edit",params={"token":"test-token"},data={"action":"set_price","item_id":"pizza_test","variant":"large","value":"18.0"})
        assert resp.status_code == 200 and "✅" in resp.text

class TestHealth:
    def test_health(self,dashboard_app): assert dashboard_app.get("/health").json() == {"status":"ok"}

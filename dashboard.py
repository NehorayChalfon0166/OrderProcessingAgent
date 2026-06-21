"""Dashboard server — read-only admin interface for order management.

Separate FastAPI process (port 8081 by default). Reads the same SQLite
database as the Twilio server via WAL mode. Token-protected with the
same API_TOKEN as the printer agent.

Usage:
    python main.py dashboard --port 8081
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import AppConfig
from db import Database
from restaurant import RestaurantRegistry

logger = logging.getLogger(__name__)

_db: Database | None = None
_registry: RestaurantRegistry | None = None
_api_token: str = ""
_restaurants_path: str = "restaurants.json"
_templates: Jinja2Templates | None = None

app = FastAPI(title="Order Processing Agent — Dashboard")

_dashboard_dir = Path(__file__).resolve().parent
_static_dir = _dashboard_dir / "dashboard_static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


def init_dashboard(config: AppConfig) -> None:
    global _db, _registry, _api_token, _templates, _restaurants_path
    _db = Database(config.db_path)
    _registry = RestaurantRegistry(config.restaurants_path)
    _api_token = config.api_token
    _restaurants_path = config.restaurants_path
    _templates = Jinja2Templates(directory=str(_dashboard_dir / "dashboard_templates"))
    if not _api_token:
        logger.warning("API_TOKEN not set — dashboard open without auth")


def _check_token(request: Request) -> None:
    if not _api_token:
        return
    token = request.query_params.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token or token != _api_token:
        raise HTTPException(status_code=403, detail="Invalid or missing token")


def _token_url(request: Request) -> str:
    """Return ?token=xxx for appending to URLs, or empty string."""
    t = request.query_params.get("token", "")
    return f"?token={t}" if t else ""


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    assert _templates is not None
    raw_token = request.query_params.get("token", "")
    return _templates.TemplateResponse(request, name, {
        "request": request,
        "token_url": _token_url(request),
        "token": raw_token,
        "registry": _registry,
        **ctx,
    })


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    _check_token(request)
    assert _db is not None and _registry is not None
    stats = []
    for r in _registry.list_restaurants():
        orders = _db.get_orders(r.id, limit=100)
        unprinted = _db.get_unprinted_orders(r.id)
        stats.append({"config": r, "order_count": len(orders), "unprinted_count": len(unprinted)})
    return _render(request, "overview.html", stats=stats)


@app.get("/orders", response_class=HTMLResponse)
async def list_orders(request: Request, restaurant_id: str = "", limit: int = Query(default=50, le=500), order_type: str = "", payment_method: str = ""):
    _check_token(request)
    assert _db is not None
    all_orders = []
    if restaurant_id:
        all_orders = _db.get_orders(restaurant_id, limit=limit)
    elif _registry:
        for r in _registry.list_restaurants():
            all_orders.extend(_db.get_orders(r.id, limit=limit))
    all_orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    all_orders = all_orders[:limit]
    if order_type:
        all_orders = [o for o in all_orders if o.get("order_type") == order_type]
    if payment_method:
        all_orders = [o for o in all_orders if o.get("payment_method") == payment_method]
    restaurants = _registry.list_restaurants() if _registry else []
    return _render(request, "orders.html", orders=all_orders, restaurants=restaurants, filters={"restaurant_id": restaurant_id, "order_type": order_type, "payment_method": payment_method, "limit": limit})


@app.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: str):
    _check_token(request)
    assert _db is not None
    order = None
    if _registry:
        for r in _registry.list_restaurants():
            for o in _db.get_orders(r.id, limit=500):
                if o.get("order_id") == order_id:
                    order = o; break
            if order: break
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _render(request, "order_detail.html", order=order)


@app.get("/sessions", response_class=HTMLResponse)
async def list_sessions(request: Request, restaurant_id: str = "", limit: int = Query(default=50, le=200)):
    _check_token(request)
    assert _db is not None and _registry is not None
    sessions = []
    for r in _registry.list_restaurants():
        if restaurant_id and r.id != restaurant_id: continue
        for o in _db.get_orders(r.id, limit=limit):
            if o.get("printed") is False:
                sessions.append({"session_id": o.get("session_id","?"), "restaurant_id": r.id, "customer_name": o.get("customer_name","Unknown"), "customer_phone": o.get("customer_phone",""), "order_id": o.get("order_id"), "updated_at": o.get("created_at")})
    restaurants = _registry.list_restaurants()
    return _render(request, "sessions.html", sessions=sessions[:limit], restaurants=restaurants, filters={"restaurant_id": restaurant_id})


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail(request: Request, session_id: str):
    _check_token(request)
    assert _db is not None and _registry is not None
    session_data = None; restaurant_name = ""
    for r in _registry.list_restaurants():
        s = _db.load_session(r.id, session_id)
        if s is not None:
            session_data = {"session_id": s.session_id, "restaurant_id": s.restaurant_id, "state": s.state.value, "cart": [item.model_dump() for item in s.cart], "customer": s.customer.model_dump(), "conversation": [msg.model_dump() for msg in s.conversation], "updated_at": s.updated_at}
            restaurant_name = r.name; break
    if session_data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _render(request, "session_detail.html", session=session_data, restaurant_name=restaurant_name)


@app.get("/menu/{restaurant_id}", response_class=HTMLResponse)
async def view_menu(request: Request, restaurant_id: str):
    _check_token(request)
    assert _registry is not None
    ctx = _registry.get_by_id(restaurant_id)
    if ctx is None: raise HTTPException(status_code=404, detail="Restaurant not found")
    return _render(request, "menu_editor.html", restaurant_id=restaurant_id, restaurant_name=ctx.config.name, menu=ctx.catalogue.menu_data)


@app.post("/menu/{restaurant_id}/edit")
async def edit_menu(request: Request, restaurant_id: str):
    _check_token(request)
    assert _registry is not None
    ctx = _registry.get_by_id(restaurant_id)
    if ctx is None: raise HTTPException(status_code=404, detail="Restaurant not found")
    from menu_manager import MenuAction, manage_menu
    form = await request.form()
    action_type = str(form.get("action", ""))
    item_id = str(form.get("item_id", ""))
    variant = str(form.get("variant", "")) or None
    value = str(form.get("value", "")) or None
    if action_type == "set_price" and value:
        try: value = float(value)
        except ValueError: return HTMLResponse("<p style='color:red'>Invalid price</p>", status_code=400)
    result = manage_menu(ctx.config.menu_path, [MenuAction(action=action_type, item_id=item_id, variant_id=variant, value=value)])
    if result.success:
        return HTMLResponse(f"<p style='color:green'>✅ {result.message}</p>")
    return HTMLResponse("<p style='color:red'>" + "<br>".join(result.errors) + "</p>", status_code=400)


@app.get("/restaurants", response_class=HTMLResponse)
async def list_restaurants(request: Request):
    _check_token(request)
    assert _registry is not None
    configs = _registry.list_restaurants()
    assert _db is not None
    stats = []
    for r in configs:
        orders = _db.get_orders(r.id, limit=30)
        unprinted = _db.get_unprinted_orders(r.id)
        stats.append({"config": r, "order_count": len(orders), "unprinted_count": len(unprinted)})
    return _render(request, "restaurants.html", stats=stats)


@app.post("/restaurants/add")
async def add_restaurant(request: Request):
    _check_token(request)
    assert _registry is not None
    from restaurant import save_restaurant
    form = await request.form()
    rid = str(form.get("restaurant_id", ""))
    name = str(form.get("name", ""))
    phone = str(form.get("phone", ""))
    owner = str(form.get("owner", ""))
    try:
        msg = save_restaurant(_restaurants_path, rid, name=name, twilio_phone=phone, owner_phone=owner)
        return HTMLResponse(f"<p style='color:green'>✅ {msg}</p>")
    except (ValueError, FileNotFoundError) as e:
        return HTMLResponse(f"<p style='color:red'>❌ {e}</p>", status_code=400)


@app.get("/health")
async def health(): return {"status": "ok"}

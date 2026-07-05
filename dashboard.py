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


def _require_init() -> tuple[Database, RestaurantRegistry]:
    """Verify the dashboard has been initialised. Raises 500 if not."""
    if _db is None or _registry is None or _templates is None:
        raise HTTPException(
            status_code=500,
            detail="Dashboard not initialised — call init_dashboard() first.",
        )
    return _db, _registry


def _check_token(request: Request) -> None:
    if not _api_token:
        return
    # Check cookie first (set on first query-param access)
    token = request.cookies.get("dashboard_token")
    if token and token == _api_token:
        return
    # Fall back to query param (initial access, bookmarked URL)
    token = request.query_params.get("token")
    if token and token == _api_token:
        return
    # Fall back to Authorization header (API-style access)
    token = (request.headers.get("Authorization", "") or "").removeprefix("Bearer ")
    if token and token == _api_token:
        return
    raise HTTPException(status_code=403, detail="Invalid or missing token")


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    _db, _registry = _require_init()
    raw_token = request.query_params.get("token", "")
    response = _templates.TemplateResponse(request, name, {
        "request": request,
        "token_url": "",
        "token": raw_token,
        "registry": _registry,
        **ctx,
    })
    # On first access via query param, set a cookie so subsequent
    # navigation doesn't leak the token in URLs.
    if raw_token and raw_token == _api_token:
        response.set_cookie(
            key="dashboard_token",
            value=raw_token,
            httponly=True,
            samesite="lax",
            max_age=86400,  # 24 hours
        )
    return response


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    _check_token(request)
    db, registry = _require_init()
    stats = []
    for r in registry.list_restaurants():
        orders = db.get_orders(r.id, limit=100)
        unprinted = db.get_unprinted_orders(r.id)
        active = len(db.list_active_sessions(r.id, limit=200))
        stats.append({
            "config": r,
            "order_count": len(orders),
            "unprinted_count": len(unprinted),
            "active_sessions": active,
        })
    return _render(request, "overview.html", stats=stats)


@app.get("/orders", response_class=HTMLResponse)
async def list_orders(
    request: Request,
    restaurant_id: str = "",
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    order_type: str = "",
    payment_method: str = "",
    search: str = "",
):
    _check_token(request)
    db, registry = _require_init()
    all_orders = []
    fetch_limit = max(limit + offset + 200, 500)  # fetch extra for client-side filter headroom
    if restaurant_id:
        all_orders = db.get_orders(restaurant_id, limit=fetch_limit)
    else:
        for r in registry.list_restaurants():
            all_orders.extend(db.get_orders(r.id, limit=fetch_limit))
    all_orders.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    if order_type:
        all_orders = [o for o in all_orders if o.get("order_type") == order_type]
    if payment_method:
        all_orders = [o for o in all_orders if o.get("payment_method") == payment_method]
    if search:
        q = search.lower()
        all_orders = [
            o for o in all_orders
            if q in (o.get("customer_name") or "").lower()
            or q in (o.get("customer_phone") or "").lower()
            or q in (o.get("order_id") or "").lower()
        ]
    total = len(all_orders)
    has_more = (offset + limit) < total
    page = all_orders[offset:offset + limit]
    restaurants = registry.list_restaurants()
    return _render(request, "orders.html", orders=page, restaurants=restaurants, filters={
        "restaurant_id": restaurant_id, "order_type": order_type,
        "payment_method": payment_method, "limit": limit, "offset": offset,
        "search": search,
    }, total=total, has_more=has_more)


@app.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: str):
    _check_token(request)
    db, registry = _require_init()
    order = None
    for r in registry.list_restaurants():
        for o in db.get_orders(r.id, limit=500):
            if o.get("order_id") == order_id:
                order = o
                break
        if order:
            break
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _render(request, "order_detail.html", order=order)


@app.get("/sessions", response_class=HTMLResponse)
async def list_sessions(request: Request, restaurant_id: str = "", limit: int = Query(default=50, le=200)):
    _check_token(request)
    db, registry = _require_init()
    active = db.list_active_sessions(
        restaurant_id=restaurant_id or None, limit=limit
    )
    sessions = [
        {
            "session_id": s.session_id,
            "restaurant_id": s.restaurant_id,
            "customer_name": s.customer.name or "Unknown",
            "customer_phone": s.customer.phone or "",
            "state": s.state.value if hasattr(s.state, 'value') else s.state,
            "updated_at": s.updated_at,
        }
        for s in active
    ]
    restaurants = registry.list_restaurants()
    return _render(request, "sessions.html", sessions=sessions, restaurants=restaurants, filters={"restaurant_id": restaurant_id})


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail(request: Request, session_id: str):
    _check_token(request)
    db, registry = _require_init()
    session_data = None
    restaurant_name = ""
    for r in registry.list_restaurants():
        s = db.load_session(r.id, session_id)
        if s is not None:
            state_value = s.state.value if hasattr(s.state, 'value') else s.state
            session_data = {
                "session_id": s.session_id,
                "restaurant_id": s.restaurant_id,
                "state": state_value,
                "cart": [item.model_dump() for item in s.cart],
                "customer": s.customer.model_dump(),
                "conversation": [msg.model_dump() for msg in s.conversation],
                "updated_at": s.updated_at,
            }
            restaurant_name = r.name
            break
    if session_data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _render(request, "session_detail.html", session=session_data, restaurant_name=restaurant_name)


@app.get("/menu/{restaurant_id}", response_class=HTMLResponse)
async def view_menu(request: Request, restaurant_id: str):
    _check_token(request)
    _db, registry = _require_init()
    ctx = registry.get_by_id(restaurant_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return _render(request, "menu_editor.html", restaurant_id=restaurant_id, restaurant_name=ctx.config.name, menu=ctx.catalogue.menu_data)


@app.post("/menu/{restaurant_id}/edit")
async def edit_menu(request: Request, restaurant_id: str):
    _check_token(request)
    _db, registry = _require_init()
    ctx = registry.get_by_id(restaurant_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    from menu_manager import MenuAction, manage_menu
    form = await request.form()
    action_type = str(form.get("action", ""))
    item_id = str(form.get("item_id", ""))
    variant = str(form.get("variant", "")) or None
    value = str(form.get("value", "")) or None
    if action_type == "set_price" and value:
        try:
            value = float(value)
        except ValueError:
            return HTMLResponse("<p style='color:red'>Invalid price</p>", status_code=400)
    result = manage_menu(ctx.config.menu_path, [MenuAction(action=action_type, item_id=item_id, variant_id=variant, value=value)])
    if result.success:
        # Hot-reload: rebuild Catalogue and PricingEngine so the menu
        # editor shows updated data without a server restart.
        registry.reload_restaurant(restaurant_id)
        return HTMLResponse(f"<p style='color:green'>✅ {result.message}</p>")
    return HTMLResponse("<p style='color:red'>" + "<br>".join(result.errors) + "</p>", status_code=400)


@app.get("/restaurants", response_class=HTMLResponse)
async def list_restaurants(request: Request):
    _check_token(request)
    db, registry = _require_init()
    configs = registry.list_restaurants()
    stats = []
    for r in configs:
        orders = db.get_orders(r.id, limit=30)
        unprinted = db.get_unprinted_orders(r.id)
        stats.append({"config": r, "order_count": len(orders), "unprinted_count": len(unprinted)})
    return _render(request, "restaurants.html", stats=stats)


@app.post("/restaurants/add")
async def add_restaurant(request: Request):
    _check_token(request)
    _db, registry = _require_init()
    from restaurant import save_restaurant
    form = await request.form()
    rid = str(form.get("restaurant_id", ""))
    name = str(form.get("name", ""))
    phone = str(form.get("phone", ""))
    owner = str(form.get("owner", ""))
    try:
        msg = save_restaurant(_restaurants_path, rid, name=name, twilio_phone=phone, owner_phone=owner)
        # Reload registry so the new restaurant appears immediately.
        registry.reload()
        return HTMLResponse(f"<p style='color:green'>✅ {msg}</p>")
    except (ValueError, FileNotFoundError) as e:
        return HTMLResponse(f"<p style='color:red'>❌ {e}</p>", status_code=400)


@app.get("/health")
async def health(): return {"status": "ok"}

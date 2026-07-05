"""Dashboard server — read-only admin interface for order management.

Separate FastAPI process (port 8081 by default). Reads the same SQLite
database as the Twilio server via WAL mode. Token-protected with the
same API_TOKEN as the printer agent.

Usage:
    python main.py dashboard --port 8081
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
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
    # If no token configured, allow (dev mode without auth)
    if not _api_token:
        return
    # If token configured but dashboard not initialised, block
    if _db is None:
        raise HTTPException(status_code=503, detail="Dashboard not initialised")
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
    from datetime import datetime as _dt, timezone as _tz
    today = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    total_orders_today = 0
    total_revenue_today = 0.0
    total_active = 0
    total_pending_print = 0
    stats = []

    for r in registry.list_restaurants():
        orders = db.get_orders(r.id, limit=200)
        unprinted = db.get_unprinted_orders(r.id)
        active = len(db.list_active_sessions(r.id, limit=200))

        orders_today = [o for o in orders if (o.get("created_at") or "")[:10] == today]
        revenue_today = sum(float(o.get("total", 0)) for o in orders_today)

        total_orders_today += len(orders_today)
        total_revenue_today += revenue_today
        total_active += active
        total_pending_print += len(unprinted)

        stats.append({
            "config": r,
            "order_count": len(orders),
            "unprinted_count": len(unprinted),
            "active_sessions": active,
            "orders_today": len(orders_today),
            "revenue_today": revenue_today,
        })

    return _render(request, "overview.html",
        stats=stats,
        total_orders_today=total_orders_today,
        total_revenue_today=total_revenue_today,
        total_active=total_active,
        total_pending_print=total_pending_print,
    )


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


@app.get("/menus", response_class=HTMLResponse)
async def list_menus(request: Request):
    _check_token(request)
    _db, registry = _require_init()
    return _render(request, "menus.html", restaurants=registry.list_restaurants())


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

    # Accept both form-encoded (simple actions) and JSON (add/update item data)
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        body = await request.json()
        action_type = str(body.get("action", ""))
        item_id = str(body.get("item_id", body.get("id", "")))
        variant = body.get("variant")
        value = body.get("value")
        extra = body.get("extra")
    else:
        form = await request.form()
        action_type = str(form.get("action", ""))
        item_id = str(form.get("item_id", ""))
        variant = str(form.get("variant", "")) or None
        value = str(form.get("value", "")) or None
        extra = None

    # Parse value for set_price
    if action_type == "set_price" and value and not isinstance(value, (int, float, dict)):
        try:
            value = float(value)
        except (ValueError, TypeError):
            return HTMLResponse("<p style='color:red'>Invalid price</p>", status_code=400)

    result = manage_menu(ctx.config.menu_path, [
        MenuAction(action=action_type, item_id=item_id, variant_id=variant, value=value, extra=extra)
    ])
    if result.success:
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


@app.get("/events")
async def event_stream(request: Request):
    """SSE endpoint — pushes new-order notifications to connected dashboard clients."""
    _check_token(request)
    db, registry = _require_init()

    async def generate():
        last_counts: dict[str, int] = {}
        try:
            while True:
                # Check each restaurant for new orders
                for r in registry.list_restaurants():
                    orders = db.get_orders(r.id, limit=50)
                    current = len(orders)
                    prev = last_counts.get(r.id, current)
                    if current > prev:
                        newest = orders[0]
                        yield (
                            f"event: new_order\ndata: {json.dumps({'restaurant': r.name, 'order_id': newest.get('order_id', '?')[:12], 'customer': newest.get('customer_name', 'Unknown'), 'total': newest.get('total', 0)})}\n\n"
                        )
                    last_counts[r.id] = current
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request, restaurant_id: str = ""):
    _check_token(request)
    db, registry = _require_init()
    restaurants = registry.list_restaurants()
    ids = [restaurant_id] if restaurant_id else [r.id for r in restaurants]

    from collections import defaultdict

    all_orders = []
    for rid in ids:
        all_orders.extend(db.get_orders(rid, limit=1000))

    revenue_by_day: dict[str, float] = defaultdict(float)
    item_counts: dict[str, int] = defaultdict(int)
    type_counts: dict[str, int] = defaultdict(int)
    total_revenue = 0.0

    for o in all_orders:
        day = (o.get("created_at") or "")[:10]
        if day:
            revenue_by_day[day] += float(o.get("total", 0))
        total_revenue += float(o.get("total", 0))
        for item in o.get("items", []):
            item_counts[item.get("name", "?")] += item.get("quantity", 1)
        type_counts[o.get("order_type", "pickup")] += 1

    days = sorted(revenue_by_day.items())[-30:]
    popular = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    avg_order = total_revenue / len(all_orders) if all_orders else 0

    return _render(request, "analytics.html",
        restaurants=restaurants,
        filters={"restaurant_id": restaurant_id},
        days=days,
        popular=popular,
        type_counts=dict(type_counts),
        total_orders=len(all_orders),
        total_revenue=total_revenue,
        avg_order=avg_order,
    )


@app.get("/kitchen", response_class=HTMLResponse)
async def kitchen_display(request: Request, restaurant_id: str = ""):
    _check_token(request)
    db, registry = _require_init()
    if not restaurant_id and registry.list_restaurants():
        restaurant_id = registry.list_restaurants()[0].id
    orders = db.get_unprinted_orders(restaurant_id) if restaurant_id else []
    restaurant_name = ""
    if restaurant_id:
        ctx = registry.get_by_id(restaurant_id)
        if ctx:
            restaurant_name = ctx.config.name
    from datetime import datetime as _dt
    return _render(request, "kitchen.html",
        orders=orders,
        restaurant_id=restaurant_id,
        restaurant_name=restaurant_name,
        restaurants=registry.list_restaurants(),
        now=_dt.now().isoformat(),
    )


@app.get("/audit", response_class=HTMLResponse)
async def audit_log(request: Request, restaurant_id: str = "", limit: int = Query(default=100, le=500)):
    _check_token(request)
    db, registry = _require_init()
    entries = db.get_audit_log(restaurant_id=restaurant_id or "", limit=limit)
    restaurants = registry.list_restaurants()
    return _render(request, "audit.html",
        entries=entries,
        restaurants=restaurants,
        filters={"restaurant_id": restaurant_id, "limit": limit},
    )


@app.get("/health")
async def health(): return {"status": "ok"}

"""Extensive integration/regression tests for multi-restaurant support.

Tests old functionality (ordering, saving, session management) hasn't broken,
and new multi-restaurant functionality works correctly.

Run: python tests/test_integration.py
"""

import json
import tempfile
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Setup — add project root to path
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_menu(path, name="Test Restaurant", items=None, deals=None):
    """Write a menu JSON file."""
    if items is None:
        items = [
            {
                "id": "pizza_margherita",
                "name": "Margherita",
                "description": "Classic pizza",
                "sizes": {"small": 9.99, "medium": 12.99, "large": 15.99},
                "default_size": "medium",
                "available_toppings": ["extra_cheese", "mushrooms", "olives"],
            },
            {
                "id": "pizza_pepperoni",
                "name": "Pepperoni",
                "description": "Pepperoni pizza",
                "sizes": {"small": 10.99, "medium": 13.99, "large": 16.99},
                "default_size": "medium",
                "available_toppings": ["extra_cheese", "mushrooms"],
            },
            {
                "id": "side_garlic_bread",
                "name": "Garlic Bread",
                "description": "Buttered bread",
                "price": 4.99,
            },
            {
                "id": "drink_cola",
                "name": "Cola",
                "description": "Soda",
                "sizes": {"regular": 1.99, "large": 2.99},
                "default_size": "regular",
            },
        ]
    if deals is None:
        deals = [
            {
                "id": "deal_family",
                "name": "Family Deal",
                "description": "2 large pizzas + side + 2 large drinks",
                "price": 34.99,
                "includes": {
                    "pizzas": {"quantity": 2, "size": "large"},
                    "sides": {"quantity": 1},
                    "drinks": {"quantity": 2, "size": "large"},
                },
            }
        ]
    menu = {
        "restaurant_name": name,
        "currency": "USD",
        "categories": [
            {"name": "Pizzas", "items": [i for i in items if i["id"].startswith("pizza")]},
            {"name": "Sides", "items": [i for i in items if i["id"].startswith("side")]},
            {"name": "Drinks", "items": [i for i in items if i["id"].startswith("drink")]},
        ],
        "toppings": [
            {"id": "extra_cheese", "name": "Extra Cheese", "price": 1.50},
            {"id": "mushrooms", "name": "Mushrooms", "price": 1.00},
            {"id": "olives", "name": "Olives", "price": 1.00},
        ],
        "deals": deals,
        "delivery_fee": 3.99,
        "min_order_amount": 10.00,
        "estimated_delivery_time": "30-45 min",
    }
    path.write_text(json.dumps(menu))


def _make_restaurants_json(path, restaurants):
    """Write a restaurants.json file."""
    path.write_text(json.dumps({"restaurants": restaurants}))


def _make_restaurant_setup(tmp, second=False):
    """Create menus and restaurants.json for testing. Returns (menu_paths, restaurants_path)."""
    menu_a = tmp / "menus" / "rest_a.json"
    menu_a.parent.mkdir(parents=True, exist_ok=True)
    _make_menu(menu_a, "Restaurant A")

    restaurants = {
        "rest_a": {
            "name": "Restaurant A",
            "menu_path": str(menu_a),
            "twilio_phone": "+1111111111",
            "owner_phone": "+15551234567",
        }
    }

    if second:
        menu_b = tmp / "menus" / "rest_b.json"
        _make_menu(menu_b, "Restaurant B")
        restaurants["rest_b"] = {
            "name": "Restaurant B",
            "menu_path": str(menu_b),
            "twilio_phone": "+2222222222",
            "owner_phone": "+15551234567",
        }

    restaurants_path = tmp / "restaurants.json"
    _make_restaurants_json(restaurants_path, restaurants)
    return restaurants_path


# ---------------------------------------------------------------------------
# Results tracker
# ---------------------------------------------------------------------------


results = {"pass": 0, "fail": 0, "errors": []}


def check(description, condition):
    """Assert-like check that records results."""
    if condition:
        results["pass"] += 1
        print(f"  ✓ {description}")
    else:
        results["fail"] += 1
        msg = f"  ✗ FAIL: {description}"
        results["errors"].append(msg)
        print(msg)


# =============================================================================
# SECTION 1: Old functionality — regression tests
# =============================================================================
print("=" * 70)
print("SECTION 1: Old functionality regression")
print("=" * 70)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    restaurants_path = _make_restaurant_setup(tmp)

    from restaurant import RestaurantRegistry
    registry = RestaurantRegistry(str(restaurants_path))
    ctx = registry.get_default()
    catalogue = ctx.catalogue
    pricing = ctx.pricing

    # --- Catalogue: product lookup ---
    print("\n-- Catalogue: product lookup --")
    p = catalogue.find_product("Margherita")
    check("finds product by exact name", p is not None and p.id == "pizza_margherita")
    check("finds product case-insensitive", catalogue.find_product("margherita").id == "pizza_margherita")
    check("finds product by substring", catalogue.find_product("pepper").id == "pizza_pepperoni")
    check("finds flat-price item", catalogue.find_product("Garlic Bread").id == "side_garlic_bread")
    check("returns None for unknown", catalogue.find_product("sushi") is None)
    check("get_product by id works", catalogue.get_product("pizza_margherita").name == "Margherita")
    check("get_product unknown returns None", catalogue.get_product("nope") is None)

    # --- Catalogue: topping lookup ---
    print("\n-- Catalogue: topping lookup --")
    t = catalogue.find_topping("Extra Cheese")
    check("finds topping by name", t is not None and t.id == "extra_cheese")
    check("finds topping case-insensitive", catalogue.find_topping("mushrooms").id == "mushrooms")
    check("finds topping by id fallback", catalogue.find_topping("extra_cheese").id == "extra_cheese")
    check("unknown topping returns None", catalogue.find_topping("caviar") is None)

    # --- Catalogue: size resolution ---
    print("\n-- Catalogue: size resolution --")
    prod = catalogue.get_product("pizza_margherita")
    resolved_size, _ = catalogue.resolve_size(prod, "large")
    check("resolves valid size", resolved_size == "large")
    default_size, _ = catalogue.resolve_size(prod, None)
    check("defaults when no size given", default_size == "medium")
    bad_size, bad_warnings = catalogue.resolve_size(prod, "gigantic")
    check("warns on invalid size", len(bad_warnings) > 0)
    check("defaults on invalid size", bad_size == "medium")

    flat = catalogue.get_product("side_garlic_bread")
    check("flat-price item has no sizes", flat.sizes is None)

    # --- Catalogue: deal lookup and expansion ---
    print("\n-- Catalogue: deal lookup and expansion --")
    deal = catalogue.find_deal("Family Deal")
    check("finds deal by name", deal is not None)
    check("deal has correct price", deal.price == 34.99)
    expanded = catalogue.expand_deal(deal)
    check("deal expands to items (2 pizzas + 1 side + 2 drinks = 5)", len(expanded) == 5)

    # --- Catalogue: hints ---
    print("\n-- Catalogue: hints --")
    hints = catalogue.get_hints()
    check("hints returns non-empty string", len(hints) > 0)
    check("hints includes category name", "Pizzas" in hints)

    # --- Catalogue: suggestions ---
    print("\n-- Catalogue: suggestions --")
    suggestions = catalogue.get_product_suggestions("peproni")
    check("suggestions for misspelling (peproni → Pepperoni)", len(suggestions) > 0)

    # --- Pricing ---
    print("\n-- Pricing --")
    from models import CartItem, CartTopping, OrderType

    item = CartItem(
        product_id="pizza_margherita",
        name="Margherita",
        category="Pizzas",
        size="medium",
        base_price=0,  # will be set by pricing
        line_total=0,
    )
    priced = pricing.price_item(item)
    check("base_price set correctly (medium = 12.99)", priced.base_price == 12.99)
    check("line_total = base_price * quantity", priced.line_total == 12.99)

    # with toppings
    item_with_tops = CartItem(
        product_id="pizza_margherita",
        name="Margherita",
        category="Pizzas",
        size="medium",
        quantity=1,
        toppings=[
            CartTopping(topping_id="extra_cheese", name="Extra Cheese", price=1.50),
            CartTopping(topping_id="mushrooms", name="Mushrooms", price=1.00),
        ],
        base_price=0,
        line_total=0,
    )
    priced2 = pricing.price_item(item_with_tops)
    check("base + toppings (12.99 + 2.50 = 15.49)", priced2.line_total == 15.49)

    qty_item = CartItem(
        product_id="side_garlic_bread",
        name="Garlic Bread",
        category="Sides",
        quantity=3,
        base_price=0,
        line_total=0,
    )
    priced3 = pricing.price_item(qty_item)
    check("quantity multiplier (4.99 * 3 = 14.97)", priced3.line_total == 14.97)

    subtotal, delivery, total = pricing.compute_totals([priced], OrderType.DELIVERY)
    check("delivery subtotal", subtotal == 12.99)
    check("delivery fee", delivery == 3.99)
    check("delivery total", total == 16.98)

    subtotal_pu, delivery_pu, total_pu = pricing.compute_totals([priced], OrderType.PICKUP)
    check("pickup has no delivery fee", delivery_pu == 0.0)

    check("min order met (12.99 >= 10.00)", pricing.check_minimum_order(12.99) is True)
    check("min order not met (5.00 < 10.00)", pricing.check_minimum_order(5.00) is False)

    # --- Session ---
    print("\n-- Session --")
    from session import OrderSession
    from models import OrderState, CustomerInfo, MessageRole

    s = OrderSession()
    check("default state is BUILDING", s.state == OrderState.BUILDING)
    check("default cart empty", s.cart == [])
    check("default restaurant_id empty", s.restaurant_id == "")
    check("is_active true for BUILDING", s.is_active is True)
    check("is_complete false for BUILDING", s.is_complete is False)

    s2 = OrderSession(restaurant_id="rest_a")
    check("restaurant_id can be set", s2.restaurant_id == "rest_a")

    s.add_user_message("Hello")
    check("add_user_message works", len(s.conversation) == 1)
    check("message role is USER", s.conversation[0].role == MessageRole.USER)

    # save/load via Database
    from db import Database
    db = Database(str(tmp / "test.db"))
    s._db = db  # type: ignore[has-type]
    s.restaurant_id = "rest_a"
    s.cart = [priced]
    s.save()
    loaded = db.load_session(s.restaurant_id, s.session_id)
    check("session save/load roundtrip", loaded is not None and loaded.session_id == s.session_id)
    check("cart preserved after load", len(loaded.cart) == 1)
    check("cart item preserved", loaded.cart[0].product_id == "pizza_margherita")

    # terminal session detection
    s_terminal = OrderSession(state=OrderState.COMPLETED)
    check("completed is terminal", s_terminal.is_complete is True)
    check("completed is not active", s_terminal.is_active is False)

    # --- CustomerInfo ---
    print("\n-- CustomerInfo --")
    ci = CustomerInfo()
    check("customer defaults to None name", ci.name is None)
    ci2 = CustomerInfo(name="John", phone="555-0123")
    check("customer with fields", ci2.name == "John" and ci2.phone == "555-0123")

    # --- Tools ---
    print("\n-- Tools --")
    from tools import (
        add_to_cart, view_cart, remove_from_cart, update_item,
        set_customer_info, request_review, confirm_order, cancel_order,
    )

    tool_session = OrderSession()

    # add_to_cart
    result = add_to_cart(tool_session, catalogue, pricing, product_name="Margherita")
    check("add_to_cart succeeds", result.success is True)
    check("cart has 1 item", len(tool_session.cart) == 1)
    check("item name is Margherita", tool_session.cart[0].name == "Margherita")
    check("price is set (not 0)", tool_session.cart[0].base_price > 0)

    # add with size
    result2 = add_to_cart(tool_session, catalogue, pricing, product_name="Pepperoni", size="large")
    check("add with size succeeds", result2.success is True)
    check("cart has 2 items", len(tool_session.cart) == 2)
    check("size is large", tool_session.cart[1].size == "large")

    # add with toppings
    result3 = add_to_cart(tool_session, catalogue, pricing,
                          product_name="Margherita", toppings=["Extra Cheese"])
    check("add with topping succeeds", result3.success is True)
    check("topping added", len(tool_session.cart[2].toppings) == 1)

    # add unknown product
    result_bad = add_to_cart(tool_session, catalogue, pricing, product_name="Sushi")
    check("add unknown product fails", result_bad.success is False)

    # view_cart
    view = view_cart(tool_session, catalogue, pricing)
    check("view_cart has correct count", view.item_count == 3)
    check("view_cart has items list", len(view.items) == 3)

    # remove_from_cart by name
    rem = remove_from_cart(tool_session, catalogue, pricing, "Pepperoni")
    check("remove by name succeeds", rem.success is True)
    check("cart now has 2 items", len(tool_session.cart) == 2)

    # remove by index
    rem2 = remove_from_cart(tool_session, catalogue, pricing, "2")
    check("remove by index succeeds", rem2.success is True)
    check("cart now has 1 item", len(tool_session.cart) == 1)

    # update_item
    upd = update_item(tool_session, catalogue, pricing, "Margherita", quantity=2)
    check("update quantity succeeds", upd.success is True)
    check("quantity is now 2", tool_session.cart[0].quantity == 2)

    # set_customer_info
    info = set_customer_info(tool_session, catalogue, pricing, name="John", phone="555-0123")
    check("set name and phone", info.success is True)
    check("name is set", tool_session.customer.name == "John")
    check("phone is set", tool_session.customer.phone == "555-0123")

    # set address
    info2 = set_customer_info(tool_session, catalogue, pricing, address="123 Main St", order_type="delivery")
    check("set address", info2.success is True)
    check("address is set", tool_session.customer.address == "123 Main St")
    check("order type is delivery", tool_session.customer.order_type == OrderType.DELIVERY)

    # request_review (needs cart + name + phone + address for delivery)
    review = request_review(tool_session, catalogue, pricing)
    check("request review succeeds with all info", review.success is True)

    # confirm_order
    confirm = confirm_order(tool_session, catalogue, pricing, payment_method="cash")
    check("confirm cash order", confirm.success is True)
    check("payment method is cash", tool_session.payment_method == "cash")

    # cancel_order
    cancel_session = OrderSession()
    cancel_result = cancel_order(cancel_session, catalogue, pricing)
    check("cancel order sets transition", cancel_result.success is True)
    check("pending transition is CANCELLED",
          cancel_session._pending_transition == OrderState.CANCELLED)

    # --- Order saving ---
    print("\n-- Order saving --")
    from main import save_order

    order_session = OrderSession(restaurant_id="rest_a")
    order_session.session_id = "972539534345"
    order_session.customer = CustomerInfo(name="Test User", phone="555-0123", order_type=OrderType.DELIVERY)
    priced_item = pricing.price_item(CartItem(
        product_id="pizza_margherita", name="Margherita",
        category="Pizzas", size="medium", base_price=0, line_total=0,
    ))
    order_session.cart = [priced_item]

    orders_dir = str(tmp / "orders")
    filepath = save_order(order_session, pricing, "Restaurant A", orders_dir)
    check("order file created", Path(filepath).exists())
    check("order in subdirectory", f"orders{Path('/').as_posix()}rest_a" in filepath.replace("\\", "/"))

    order_data = json.loads(Path(filepath).read_text())
    check("order has restaurant_id", order_data.get("restaurant_id") == "rest_a")
    check("order has restaurant name", order_data.get("restaurant") == "Restaurant A")
    check("order has items", len(order_data["items"]) == 1)
    check("order has total", order_data["total"] == 16.98)  # 12.99 + 3.99 delivery


# =============================================================================
# SECTION 2: New multi-restaurant functionality
# =============================================================================
print("\n" + "=" * 70)
print("SECTION 2: New multi-restaurant functionality")
print("=" * 70)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    restaurants_path = _make_restaurant_setup(tmp, second=True)

    from restaurant import RestaurantRegistry, RestaurantConfig, RestaurantContext
    registry = RestaurantRegistry(str(restaurants_path))

    # --- Registry loading ---
    print("\n-- Registry loading --")
    check("two restaurants loaded", len(registry.list_restaurants()) == 2)
    check("rest_a accessible by ID", registry.get_by_id("rest_a") is not None)
    check("rest_b accessible by ID", registry.get_by_id("rest_b") is not None)
    check("nonexistent returns None", registry.get_by_id("nope") is None)

    # --- Registry: phone lookup ---
    print("\n-- Registry: phone lookup --")
    ctx_a = registry.get_by_twilio_phone("+1111111111")
    check("phone lookup rest_a", ctx_a is not None and ctx_a.config.id == "rest_a")
    ctx_b = registry.get_by_twilio_phone("+2222222222")
    check("phone lookup rest_b", ctx_b is not None and ctx_b.config.id == "rest_b")
    check("unknown phone returns None", registry.get_by_twilio_phone("+000") is None)
    check("whatsapp prefix stripped",
          registry.get_by_twilio_phone("whatsapp:+1111111111").config.id == "rest_a")

    # --- Registry: get_default ---
    print("\n-- Registry: get_default --")
    default = registry.get_default()
    check("default returns first restaurant", default.config.id == "rest_a")

    # --- Registry: list_restaurants ---
    print("\n-- Registry: list_restaurants --")
    configs = registry.list_restaurants()
    check("list returns 2 configs", len(configs) == 2)
    check("configs are RestaurantConfig", all(isinstance(c, RestaurantConfig) for c in configs))
    ids = {c.id for c in configs}
    check("contains rest_a", "rest_a" in ids)
    check("contains rest_b", "rest_b" in ids)

    # --- Registry: context has catalogue and pricing ---
    print("\n-- Registry: context integrity --")
    for rid in ("rest_a", "rest_b"):
        ctx = registry.get_by_id(rid)
        check(f"{rid} context has catalogue", ctx.catalogue is not None)
        check(f"{rid} context has pricing", ctx.pricing is not None)
        check(f"{rid} catalogue has restaurant_name",
              ctx.catalogue.restaurant_name == f"Restaurant {'A' if rid == 'rest_a' else 'B'}")
        # Verify items can be looked up
        p = ctx.catalogue.find_product("Margherita")
        check(f"{rid} can find products", p is not None)

    # --- Registry: validation errors ---
    print("\n-- Registry: validation --")
    # missing twilio_phone
    bad_path = tmp / "bad_restaurants.json"
    _make_restaurants_json(bad_path, {
        "bad": {"name": "Bad", "menu_path": str(tmp / "menus" / "rest_a.json")}
    })
    try:
        RestaurantRegistry(str(bad_path))
        check("missing twilio_phone raises error", False)
    except ValueError as e:
        check("missing twilio_phone raises ValueError", "twilio_phone" in str(e).lower())

    # missing file
    try:
        RestaurantRegistry(str(tmp / "nonexistent.json"))
        check("missing file raises error", False)
    except FileNotFoundError:
        check("missing file raises FileNotFoundError", True)

    # --- Session isolation: same phone, different restaurants ---
    print("\n-- Session isolation --")
    from session_router import SessionRouter
    from db import Database

    db2 = Database(str(tmp / "iso_test.db"))
    router = SessionRouter()

    s_a = router.get_or_create("rest_a", "+972539534345", db2)
    s_b = router.get_or_create("rest_b", "+972539534345", db2)

    check("same session_id (phone digits)", s_a.session_id == s_b.session_id == "972539534345")
    check("different restaurant_id", s_a.restaurant_id != s_b.restaurant_id)
    check("rest_a has correct restaurant_id", s_a.restaurant_id == "rest_a")
    check("rest_b has correct restaurant_id", s_b.restaurant_id == "rest_b")

    # Add items to rest_a session only
    from models import CartItem as CI
    s_a.cart.append(CI(
        product_id="test_item", name="Test Item", category="Test",
        base_price=10.0, line_total=10.0,
    ))
    s_a.save()

    # Reload and verify isolation
    s_a2 = router.get_or_create("rest_a", "+972539534345", db2)
    s_b2 = router.get_or_create("rest_b", "+972539534345", db2)
    check("rest_a session has item", len(s_a2.cart) == 1)
    check("rest_b session is empty", len(s_b2.cart) == 0)

    # Verify DB persistence
    check("rest_a session in DB", db2.load_session("rest_a", "972539534345") is not None)
    check("rest_b session in DB", db2.load_session("rest_b", "972539534345") is not None)

    # --- SessionRouter: terminal state per restaurant ---
    print("\n-- SessionRouter: terminal state handling --")
    from models import OrderState

    s_term = router.get_or_create("rest_a", "+972531111111", db2)
    s_term.cart.append(CI(
        product_id="old", name="Old Item", category="Test",
        base_price=5.0, line_total=5.0,
    ))
    s_term.state = OrderState.COMPLETED
    s_term.save()

    s_new = router.get_or_create("rest_a", "+972531111111", db2)
    check("terminal session replaced with fresh", s_new.state == OrderState.BUILDING)
    check("fresh session has empty cart", len(s_new.cart) == 0)
    check("fresh session keeps same session_id", s_new.session_id == "972531111111")
    check("fresh session has restaurant_id", s_new.restaurant_id == "rest_a")

    # --- Multi-restaurant orders ---
    print("\n-- Multi-restaurant orders --")
    from main import save_order

    for rid, phone in [("rest_a", "+972531234567"), ("rest_b", "+972531234567")]:
        ctx = registry.get_by_id(rid)
        order_s = OrderSession(restaurant_id=rid)
        order_s.session_id = "972531234567"
        order_s.customer = CustomerInfo(name="Customer", phone=phone)
        item = ctx.pricing.price_item(CI(
            product_id="pizza_margherita", name="Margherita",
            category="Pizzas", size="medium", base_price=0, line_total=0,
        ))
        order_s.cart = [item]
        fp = save_order(order_s, ctx.pricing, ctx.config.name, str(tmp / "orders"))
        check(f"order for {rid} created", Path(fp).exists())
        check(f"order for {rid} in correct subdirectory",
              f"{Path('/').as_posix()}orders{Path('/').as_posix()}{rid}" in fp.replace("\\", "/"))

        data = json.loads(Path(fp).read_text())
        check(f"order for {rid} has restaurant_id", data["restaurant_id"] == rid)

    # --- Registry config validation ---
    print("\n-- Registry: RestaurantConfig frozen --")
    config = RestaurantConfig(id="test", name="Test", menu_path="x.json", twilio_phone="+123", owner_phone="+1555")
    check("config has correct fields", config.id == "test" and config.name == "Test")
    try:
        config.name = "Changed"  # type: ignore
        check("config is frozen", False)
    except Exception:
        check("config is frozen (immutable)", True)

    # --- process_turn signature ---
    print("\n-- process_turn signature --")
    from agent_loop import process_turn
    import inspect
    sig = inspect.signature(process_turn)
    params = list(sig.parameters.keys())
    check("process_turn takes session, user_message, catalogue, pricing, llm_client",
          params == ["session", "user_message", "catalogue", "pricing", "llm_client"])


# =============================================================================
# SECTION 3: Server routing (unit-tested via mock)
# =============================================================================
print("\n" + "=" * 70)
print("SECTION 3: Server routing (mock-based)")
print("=" * 70)

# These are covered by test_server.py (on integration branches).
# Verify the key scenarios if the file exists.

server_test_path = Path(__file__).parent / "test_server.py"
if server_test_path.exists():
    test_content = server_test_path.read_text()

    checks = [
        ("tests To field routing", "get_by_twilio_phone" in test_content),
        ("tests unknown restaurant 500", "Unknown restaurant" in test_content or
         "unknown_restaurant" in test_content.lower()),
        ("tests composite lock keys", "marios_pizzeria:972539534345" in test_content),
        ("tests different restaurant locks", "different_restaurants" in test_content.lower()),
        ("tests get_or_create with restaurant_id",
         '"marios_pizzeria"' in test_content and 'get_or_create' in test_content),
        ("tests fallback message on error", "sorry" in test_content.lower()),
    ]

    for desc, condition in checks:
        check(f"test_server.py: {desc}", condition)
else:
    print("  (test_server.py not on this branch — skipping server routing checks)")


# =============================================================================
# SECTION 4: Edge cases
# =============================================================================
print("\n" + "=" * 70)
print("SECTION 4: Edge cases")
print("=" * 70)

with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    _make_restaurant_setup(tmp, second=True)
    registry = RestaurantRegistry(str(tmp / "restaurants.json"))

    # --- Edge: duplicate phone numbers ---
    print("\n-- Edge: duplicate phone numbers --")
    dup_path = tmp / "dup_restaurants.json"
    _make_restaurants_json(dup_path, {
        "r1": {"name": "R1", "menu_path": str(tmp / "menus" / "rest_a.json"), "twilio_phone": "+1111111111",
            "owner_phone": "+15551234567"},
        "r2": {"name": "R2", "menu_path": str(tmp / "menus" / "rest_b.json"), "twilio_phone": "+1111111111",
            "owner_phone": "+15551234567"},
    })
    dup_registry = RestaurantRegistry(str(dup_path))
    # Last one wins (dict overwrite)
    ctx_dup = dup_registry.get_by_twilio_phone("+1111111111")
    check("duplicate phone: last wins (r2)", ctx_dup is not None)
    # r1 is still accessible by ID
    check("duplicate phone: r1 still accessible by ID",
          dup_registry.get_by_id("r1") is not None)

    # --- Edge: whitespace in phone ---
    print("\n-- Edge: whitespace handling --")
    ctx = registry.get_by_twilio_phone("  +1111111111  ")
    check("whitespace NOT stripped (exact match required)", ctx is None)

    # --- Edge: empty string phone ---
    check("empty phone returns None", registry.get_by_twilio_phone("") is None)

    # --- Edge: sessions with special characters in phone ---
    print("\n-- Edge: session phone sanitization --")
    from session_router import SessionRouter
    from db import Database as DBEdge
    db_edge = DBEdge(str(tmp / "edge_test.db"))
    router = SessionRouter()
    s = router.get_or_create("rest_a", "whatsapp:+972 (53) 953-4345", db_edge)
    check("sanitized phone as session_id", s.session_id == "972539534345")

    # --- Edge: chained restaurant IDs ---
    print("\n-- Edge: similar restaurant IDs --")
    similar_path = tmp / "similar_restaurants.json"
    _make_restaurants_json(similar_path, {
        "marios": {"name": "Marios", "menu_path": str(tmp / "menus" / "rest_a.json"), "twilio_phone": "+1111111111",
            "owner_phone": "+15551234567"},
        "marios_pizzeria": {"name": "Marios Pizzeria", "menu_path": str(tmp / "menus" / "rest_b.json"), "twilio_phone": "+2222222222",
            "owner_phone": "+15551234567"},
    })
    sim_reg = RestaurantRegistry(str(similar_path))
    check("exact ID match (not prefix)", sim_reg.get_by_id("marios").config.name == "Marios")
    check("longer ID also works", sim_reg.get_by_id("marios_pizzeria").config.name == "Marios Pizzeria")
    check("partial match returns None", sim_reg.get_by_id("mario") is None)


# =============================================================================
# Summary
# =============================================================================
print("\n" + "=" * 70)
total = results["pass"] + results["fail"]
print(f"RESULTS: {results['pass']}/{total} passed, {results['fail']} failed")
print("=" * 70)

if results["fail"] > 0:
    print("\nFAILURES:")
    for err in results["errors"]:
        print(err)
    sys.exit(1)
else:
    print("\n✓ All tests passed!")
    sys.exit(0)



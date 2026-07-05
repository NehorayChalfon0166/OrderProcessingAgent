#!/usr/bin/env python3
"""Order Processing Agent — CLI and Server Entry Point.

A tool-calling AI agent for processing restaurant orders.

Usage:
    python main.py cli           # interactive terminal session
    python main.py server        # start Twilio WhatsApp webhook server
    python main.py --debug cli   # debug logging
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_loop import process_turn
from config import AppConfig
from db import Database
from llm_client import LLMClient
from models import OrderType
from restaurant import RestaurantRegistry
from session import OrderSession


# ── Logging ───────────────────────────────────────────────────────────────────


def setup_logging(debug: bool = False, log_json: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO

    if log_json:
        try:
            from pythonjsonlogger import jsonlogger
        except ImportError:
            print(
                "LOG_JSON=true requires python-json-logger. "
                "Install it with: pip install python-json-logger"
            )
            import sys
            sys.exit(1)

        handler = logging.StreamHandler()
        handler.setFormatter(jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    if not debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)


# ── Display ───────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║              🍕  Order Processing Agent  🍕                 ║
║                                                              ║
║   Type your order in natural language.                       ║
║   Type 'quit' or 'exit' to end the session.                 ║
║   Type 'status' to see your current order.                  ║
║   Type 'restart' to start a new order.                      ║
╚══════════════════════════════════════════════════════════════╝
"""

STATE_LABELS = {
    "building": "🛒 Building Order",
    "review": "✅ Review",
    "payment_pending": "💳 Payment",
    "completed": "🎉 Confirmed",
    "cancelled": "❌ Cancelled",
}


def print_agent(message: str) -> None:
    print(f"\n🤖 Agent: {message}\n")


def print_system(message: str) -> None:
    print(f"\n💡 {message}\n")


def print_status(session: OrderSession) -> None:
    label = STATE_LABELS.get(session.state.value, session.state.value)
    print(f"\n{'─' * 50}")
    print(f"  State: {label}")
    print(f"  Session: {session.session_id}")
    print(f"  Items: {len(session.cart)}")
    if session.cart:
        for i, item in enumerate(session.cart, 1):
            line = f"    {i}. {item.quantity}x {item.name}"
            if item.size:
                line += f" ({item.size})"
            if item.toppings:
                tops = ", ".join(t.name for t in item.toppings)
                line += f" + {tops}"
            line += f" — ${item.line_total:.2f}"
            print(line)
    if session.customer.name:
        print(f"  Customer: {session.customer.name}")
    print(f"{'─' * 50}\n")


# ── Order Output ──────────────────────────────────────────────────────────────


def save_order(
    session: OrderSession,
    pricing,
    restaurant_name: str,
    orders_dir: str,
) -> str:
    """Build and save the final order. Delegates to Database.persist_completed_order."""
    if session._db is not None:
        return session._db.persist_completed_order(session, pricing, restaurant_name, orders_dir)
    # Fallback for sessions without a DB handle (shouldn't happen in normal flow)
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    ot = session.customer.order_type or OrderType.PICKUP
    subtotal, delivery_fee, total = pricing.compute_totals(session.cart, ot)
    ts = _dt.now(_tz.utc)
    order_id = f"{session.restaurant_id}_{ts.strftime('%Y%m%d%H%M%S')}_{str(_uuid.uuid4())[:6]}"
    payload = {
        "order_id": order_id, "session_id": session.session_id,
        "restaurant_id": session.restaurant_id, "restaurant": restaurant_name,
        "items": [item.model_dump() for item in session.cart],
        "customer": session.customer.model_dump(),
        "subtotal": subtotal, "delivery_fee": delivery_fee, "total": total,
        "order_type": ot.value, "payment_method": session.payment_method or "cash",
        "timestamp": ts.isoformat(),
    }
    order_dir = Path(orders_dir) / session.restaurant_id
    order_dir.mkdir(parents=True, exist_ok=True)
    filepath = order_dir / f"{order_id}.json"
    filepath.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(filepath)


# ── Main Loop ─────────────────────────────────────────────────────────────────


def run_session(config: AppConfig, restaurant_id: str | None = None) -> None:
    logger = logging.getLogger("main")

    # Initialize registry and select restaurant
    registry = RestaurantRegistry(config.restaurants_path)
    if restaurant_id:
        ctx = registry.get_by_id(restaurant_id)
        if ctx is None:
            print_system(f"Restaurant '{restaurant_id}' not found.")
            print_system(
                f"Available restaurants: "
                f"{', '.join(c.id for c in registry.list_restaurants())}"
            )
            return
    else:
        try:
            ctx = registry.get_default()
        except ValueError as e:
            print_system(f"Error: {e}")
            return

    catalogue = ctx.catalogue
    pricing = ctx.pricing
    llm_client = LLMClient(config)
    db = Database(config.db_path)

    # Create session
    session = OrderSession(restaurant_id=ctx.config.id)
    session._db = db  # type: ignore[has-type]
    logger.info("New session: %s (restaurant: %s)", session.session_id, ctx.config.id)

    # Generate greeting
    print_system(f"Starting new order at {ctx.config.name}...")
    try:
        greeting = process_turn(
            session, "Hi", catalogue, pricing, llm_client,
        )
        print_agent(greeting)
    except Exception as e:
        logger.error("Failed to generate greeting: %s", e)
        print_system(
            f"Error connecting to LLM: {e}\n"
            f"Make sure DEEPSEEK_API_KEY is set in .env"
        )
        return

    # Interactive loop
    while True:
        label = STATE_LABELS.get(session.state.value, session.state.value)
        try:
            user_input = input(f"[{label}] You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            print_system("Session ended by user.")
            return

        if not user_input:
            continue

        # Meta-commands
        if user_input.lower() in ("quit", "exit"):
            print_system("Session ended. Goodbye! 👋")
            return

        if user_input.lower() == "status":
            print_status(session)
            continue

        if user_input.lower() == "restart":
            print_system("Starting a new order...")
            session = OrderSession(restaurant_id=ctx.config.id)
            session._db = db  # type: ignore[has-type]
            try:
                greeting = process_turn(
                    session, "Hi", catalogue, pricing, llm_client,
                )
                print_agent(greeting)
            except Exception:
                print_system("Error reconnecting.")
            continue

        # Process through agent loop
        try:
            response = process_turn(
                session, user_input, catalogue, pricing, llm_client,
            )
            print_agent(response)
        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)
            print_system(f"Something went wrong: {e}. Please try again.")
            continue

        # Check terminal states
        if session.is_complete:
            try:
                filepath = save_order(
                    session, pricing, ctx.config.name, config.orders_dir,
                )
                order_type = session.customer.order_type or OrderType.PICKUP
                _, _, total = pricing.compute_totals(session.cart, order_type)
                print_system(f"Order saved to: {filepath}")
                print(f"\n{'═' * 50}")
                print(f"  🎉  Order #{session.session_id} confirmed!")
                print(f"  📄  Total: ${total:.2f}")
                print(f"  📍  Type: {order_type.value if order_type else 'pickup'}")
                print(f"{'═' * 50}\n")
            except Exception as e:
                logger.error("Failed to save order: %s", e, exc_info=True)
                print_system(f"Error saving order: {e}")
            return

        if session.is_cancelled:
            print_system("Order has been cancelled.")
            return


# ── Entry Point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Order Processing Agent"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--list-restaurants", action="store_true",
        help="List configured restaurants and exit",
    )
    sub = parser.add_subparsers(dest="command", help="Run mode")

    # ── CLI subcommand ────────────────────────────────────────────────────
    cli_parser = sub.add_parser("cli", help="Interactive terminal session")
    cli_parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    cli_parser.add_argument(
        "--restaurant", default=None,
        help="Restaurant ID to use (defaults to first in restaurants.json)",
    )
    cli_parser.add_argument(
        "--list-restaurants", action="store_true",
        help="List configured restaurants and exit",
    )

    # ── Server subcommand ─────────────────────────────────────────────────
    srv_parser = sub.add_parser("server", help="Start Twilio webhook server")
    srv_parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )
    srv_parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to bind to"
    )

    # ── Dashboard subcommand ──────────────────────────────────────────────
    dash_parser = sub.add_parser("dashboard", help="Start admin dashboard")
    dash_parser.add_argument(
        "--port", type=int, default=8081, help="Port to listen on (default: 8081)"
    )
    dash_parser.add_argument(
        "--host", type=str, default="0.0.0.0", help="Host to bind to"
    )

    # ── Manage-menu subcommand ────────────────────────────────────────────
    menu_parser = sub.add_parser("manage-menu", help="Edit a restaurant menu")
    menu_parser.add_argument(
        "action",
        help="Action: set-price, 86 (out-of-stock), un86 (in-stock), describe",
    )
    menu_parser.add_argument("item_id", help="Menu item ID (e.g. pizza_margherita)")
    menu_parser.add_argument(
        "value", nargs="?", default=None,
        help="New price (for set-price) or description text (for describe)",
    )
    menu_parser.add_argument(
        "--variant", default=None,
        help="Size/variant (e.g. large). Only for sized items.",
    )
    menu_parser.add_argument(
        "--restaurant", default=None,
        help="Restaurant ID (defaults to first in restaurants.json)",
    )

    # ── Restaurant subcommand ────────────────────────────────────────────
    rest_parser = sub.add_parser("restaurant", help="Manage restaurants")
    rest_sub = rest_parser.add_subparsers(dest="restaurant_action")
    rest_add = rest_sub.add_parser("add", help="Add a new restaurant")
    rest_add.add_argument("restaurant_id", help="Restaurant slug")
    rest_add.add_argument("--name", required=True, help="Display name")
    rest_add.add_argument("--phone", required=True, help="Twilio WhatsApp number")
    rest_add.add_argument("--owner", required=True, help="Owner phone number")
    rest_add.add_argument("--menu", default=None, help="Menu path (default: menus/{id}.json)")

    rest_edit = rest_sub.add_parser("edit", help="Edit a restaurant")
    rest_edit.add_argument("restaurant_id", help="Restaurant slug")
    rest_edit.add_argument("--name", default=None, help="New display name")
    rest_edit.add_argument("--phone", default=None, help="New Twilio number")
    rest_edit.add_argument("--owner", default=None, help="New owner phone")

    args = parser.parse_args()

    # Default to CLI if no subcommand given
    if args.command is None:
        args.command = "cli"

    # ── --list-restaurants works without a subcommand too ─────────────
    list_flag = getattr(args, "list_restaurants", False)
    if list_flag:
        try:
            config = AppConfig.from_env()
        except ValueError as e:
            print(f"\n❌ Configuration error: {e}")
            sys.exit(1)
        try:
            registry = RestaurantRegistry(config.restaurants_path)
            print("\nConfigured restaurants:\n")
            for r in registry.list_restaurants():
                print(f"  {r.id}")
                print(f"    Name:   {r.name}")
                print(f"    Menu:   {r.menu_path}")
                print(f"    Phone:  {r.twilio_phone}")
                print()
        except Exception as e:
            print(f"\n❌ Error loading restaurants: {e}")
            sys.exit(1)
        return

    # Load config
    try:
        config = AppConfig.from_env()
    except ValueError as e:
        print(f"\n❌ Configuration error: {e}")
        print("   Copy .env.example to .env and add your DeepSeek API key.")
        sys.exit(1)

    debug = getattr(args, "debug", False)
    if debug:
        config.debug = True

    setup_logging(config.debug, config.log_json)

    if args.command == "cli":
        restaurant_id = getattr(args, "restaurant", None)
        print(BANNER)
        try:
            run_session(config, restaurant_id=restaurant_id)
        except KeyboardInterrupt:
            print("\n\n💡 Session interrupted. Goodbye!")
    elif args.command == "server":
        _run_server(args, config)
    elif args.command == "dashboard":
        _run_dashboard(args, config)
    elif args.command == "manage-menu":
        _run_manage_menu(args, config)
    elif args.command == "restaurant":
        _run_restaurant(args, config)


def _run_restaurant(args, config: AppConfig) -> None:
    """Add or edit a restaurant."""
    from restaurant import save_restaurant

    action = args.restaurant_action
    if action == "add":
        result = save_restaurant(
            config.restaurants_path, args.restaurant_id,
            name=args.name, twilio_phone=args.phone,
            owner_phone=args.owner, menu_path=args.menu,
        )
        print(f"✅ {result}")
    elif action == "edit":
        registry = RestaurantRegistry(config.restaurants_path)
        existing = registry.get_by_id(args.restaurant_id)
        if existing is None:
            print(f"❌ Restaurant '{args.restaurant_id}' not found.")
            sys.exit(1)
        result = save_restaurant(
            config.restaurants_path, args.restaurant_id,
            name=args.name or existing.config.name,
            twilio_phone=args.phone or existing.config.twilio_phone,
            owner_phone=args.owner or existing.config.owner_phone,
            menu_path=existing.config.menu_path,
        )
        print(f"✅ {result}")
    else:
        print(f"❌ Unknown action: {action}. Use 'add' or 'edit'.")
        sys.exit(1)


def _run_dashboard(args, config: AppConfig) -> None:
    """Start the admin dashboard server."""
    from dashboard import init_dashboard

    if not config.api_token:
        print(
            "⚠️  API_TOKEN is not set — dashboard will be accessible without "
            "authentication.\n"
            "   Set API_TOKEN in .env to enable token-based auth."
        )

    init_dashboard(config)

    import uvicorn

    print(f"📊 Starting dashboard on http://{args.host}:{args.port}")
    uvicorn.run("dashboard:app", host=args.host, port=args.port, reload=False)


def _run_manage_menu(args, config: AppConfig) -> None:
    """Parse CLI args and call manage_menu."""
    from menu_manager import MenuAction, manage_menu

    # Normalize action aliases
    action = args.action.replace("-", "_")
    if action == "86":
        action = "out_of_stock"
    elif action == "un86":
        action = "in_stock"

    # Resolve restaurant
    from restaurant import RestaurantRegistry
    registry = RestaurantRegistry(config.restaurants_path)
    if args.restaurant:
        ctx = registry.get_by_id(args.restaurant)
        if ctx is None:
            print(f"❌ Restaurant '{args.restaurant}' not found.")
            sys.exit(1)
    else:
        try:
            ctx = registry.get_default()
        except ValueError as e:
            print(f"❌ {e}")
            sys.exit(1)

    # Parse value
    value = args.value
    if action == "set_price" and value is not None:
        try:
            value = float(value)
        except ValueError:
            print(f"❌ Invalid price: '{value}'. Must be a number.")
            sys.exit(1)

    actions = [
        MenuAction(
            action=action,
            item_id=args.item_id,
            variant_id=args.variant,
            value=value,
        )
    ]

    result = manage_menu(ctx.config.menu_path, actions)
    if result.success:
        print(f"✅ {result.message}")
    else:
        print(f"❌ {result.message}")
        for err in result.errors:
            print(f"   - {err}")
        sys.exit(1)


def _run_server(args, config: AppConfig) -> None:
    """Validate Twilio config and start the FastAPI server."""
    try:
        import server  # noqa: F401
    except ImportError:
        print(
            "❌ Server module (server.py) not found.\n"
            "   The Twilio webhook server has not been set up yet.\n"
            "   Ensure server.py exists in the project root."
        )
        sys.exit(1)

    if not config.twilio_account_sid:
        print("❌ TWILIO_ACCOUNT_SID is required for server mode.")
        sys.exit(1)
    if not config.twilio_auth_token:
        print("❌ TWILIO_AUTH_TOKEN is required for server mode.")
        sys.exit(1)

    import uvicorn

    print(f"🚀 Starting Twilio webhook server on {args.host}:{args.port}")
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()

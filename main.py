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


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
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
    """Build and save the final order to a JSON file."""
    ot = session.customer.order_type or OrderType.PICKUP
    subtotal, delivery_fee, total = pricing.compute_totals(session.cart, ot)

    payload = {
        "order_id": session.session_id,
        "restaurant_id": session.restaurant_id,
        "restaurant": restaurant_name,
        "items": [item.model_dump() for item in session.cart],
        "customer": session.customer.model_dump(),
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total": total,
        "order_type": ot.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    order_dir = Path(orders_dir) / session.restaurant_id
    order_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{session.session_id}_{ts}.json"
    filepath = order_dir / filename
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

    setup_logging(config.debug)

    if args.command == "cli":
        restaurant_id = getattr(args, "restaurant", None)
        print(BANNER)
        try:
            run_session(config, restaurant_id=restaurant_id)
        except KeyboardInterrupt:
            print("\n\n💡 Session interrupted. Goodbye!")
    elif args.command == "server":
        _run_server(args, config)


def _run_server(args, config: AppConfig) -> None:
    """Validate Twilio config and start the FastAPI server."""
    try:
        import server  # noqa: F401 — lives on twilio-integration branch
    except ImportError:
        print(
            "❌ Server mode requires the twilio-integration branch.\n"
            "   The server.py module is not on master — it lives on the\n"
            "   twilio-integration branch alongside twilio_client.py.\n"
            "   Check out that branch and try again."
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

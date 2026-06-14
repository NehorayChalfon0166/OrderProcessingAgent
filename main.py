#!/usr/bin/env python3
"""
Order Processing Agent — CLI Entry Point

A deterministic, state-machine-driven AI agent for processing restaurant orders.
Run this file to start an interactive ordering session via the terminal.

Usage:
    python main.py
    python main.py --debug
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from config import AppConfig
from menu_manager import MenuManager
from pricing import PricingEngine
from llm_client import LLMClient
from state_machine import OrderSession


# ── Logging Setup ─────────────────────────────────────────────────────────────

def setup_logging(debug: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy HTTP logs unless debugging
    if not debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)


# ── Order Output ──────────────────────────────────────────────────────────────

def save_order(order_summary, orders_dir: str) -> str:
    """Serialize and save the final order to a JSON file.
    
    Returns the path to the saved file.
    """
    Path(orders_dir).mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"order_{order_summary.order_id}_{timestamp}.json"
    filepath = Path(orders_dir) / filename
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(order_summary.model_dump(), f, indent=2, ensure_ascii=False)
    
    return str(filepath)


# ── CLI Display ───────────────────────────────────────────────────────────────

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

STATE_INDICATORS = {
    "greeting": "👋 Greeting",
    "assembly": "🛒 Building Order",
    "details": "📋 Collecting Details",
    "verification": "✅ Verification",
    "confirmed": "🎉 Confirmed",
    "cancelled": "❌ Cancelled",
}


def print_agent(message: str) -> None:
    """Print an agent response with formatting."""
    print(f"\n🤖 Agent: {message}\n")


def print_system(message: str) -> None:
    """Print a system message."""
    print(f"\n💡 {message}\n")


def print_status(session: OrderSession) -> None:
    """Print the current session status."""
    state_label = STATE_INDICATORS.get(session.state.value, session.state.value)
    print(f"\n{'─' * 50}")
    print(f"  State: {state_label}")
    print(f"  Order ID: {session.order_id}")
    print(f"  Items: {len(session.items)}")
    if session.items:
        for i, item in enumerate(session.items, 1):
            line = f"    {i}. {item.quantity}x {item.item_name}"
            if item.size:
                line += f" ({item.size})"
            if item.toppings:
                tops = ", ".join(t.topping_name for t in item.toppings)
                line += f" + {tops}"
            line += f" — ${item.line_total:.2f}"
            print(line)
    if session.customer.name:
        print(f"  Customer: {session.customer.name}")
    print(f"{'─' * 50}\n")


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run_session(config: AppConfig) -> None:
    """Run a single interactive order session."""
    logger = logging.getLogger("main")
    
    # Initialize components
    logger.info("Loading menu from %s", config.menu_path)
    menu_manager = MenuManager(config.menu_path)
    pricing_engine = PricingEngine(menu_manager.menu_data)
    llm_client = LLMClient(config)
    
    # Create session
    session = OrderSession(menu_manager, pricing_engine, llm_client)
    
    # Generate greeting
    print_system("Starting new order session...")
    try:
        greeting = session.start()
        print_agent(greeting)
    except Exception as e:
        logger.error("Failed to generate greeting: %s", e)
        print_system(f"Error connecting to LLM: {e}")
        print_system(
            "Make sure your API key is set correctly in .env\n"
            f"  Provider: {config.llm_provider}\n"
            f"  Model: {config.llm_model}\n"
            f"  Base URL: {config.llm_base_url}"
        )
        return
    
    # Interactive loop
    while True:
        # Show state indicator
        state_label = STATE_INDICATORS.get(session.state.value, session.state.value)
        try:
            user_input = input(f"[{state_label}] You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            print_system("Session ended by user.")
            return
        
        if not user_input:
            continue
        
        # Handle meta-commands
        if user_input.lower() in ("quit", "exit"):
            print_system("Session ended. Goodbye! 👋")
            return
        
        if user_input.lower() == "status":
            print_status(session)
            continue
        
        if user_input.lower() == "restart":
            print_system("Starting a new order...")
            session = OrderSession(menu_manager, pricing_engine, llm_client)
            greeting = session.start()
            print_agent(greeting)
            continue
        
        # Process through state machine
        try:
            response = session.process_message(user_input)
            print_agent(response)
        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)
            print_system(f"Something went wrong: {e}. Please try again.")
            continue
        
        # Check terminal states
        if session.is_complete:
            # Build and save the final order
            try:
                order = session.build_final_payload()
                filepath = save_order(order, config.orders_dir)
                print_system(f"Order {order.order_id} saved to: {filepath}")
                print(f"\n{'═' * 50}")
                print(f"  🎉  Order #{order.order_id} confirmed!")
                print(f"  📄  Total: ${order.total:.2f}")
                print(f"  📍  Type: {order.order_type.value}")
                print(f"{'═' * 50}\n")
            except Exception as e:
                logger.error("Failed to save order: %s", e, exc_info=True)
                print_system(f"Error saving order: {e}")
            return
        
        if session.is_cancelled:
            print_system("Order has been cancelled.")
            return


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="AI Order Processing Agent — CLI Interface"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging (shows LLM requests/responses)"
    )
    args = parser.parse_args()
    
    # Load config
    try:
        config = AppConfig.from_env()
    except ValueError as e:
        print(f"\n❌ Configuration error: {e}")
        print("   Copy .env.example to .env and fill in your API key.")
        sys.exit(1)
    
    if args.debug:
        config.debug = True
    
    setup_logging(config.debug)
    
    print(BANNER)
    
    try:
        run_session(config)
    except KeyboardInterrupt:
        print("\n\n💡 Session interrupted. Goodbye!")


if __name__ == "__main__":
    main()

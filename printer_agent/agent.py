#!/usr/bin/env python3
"""Printer agent — polls the server for unprinted orders and sends them to
a thermal printer.

Runs on the restaurant's POS computer. Fetches unprinted orders from the
OrderProcessingAgent server API, formats them as ESC/POS bytes via printer.py,
and sends them to the thermal printer over TCP (port 9100).

Configuration is read from config.json in the same directory. Copy
config.example.json to config.json and fill in your values.

Usage:
    python agent.py
    python agent.py --config /path/to/config.json
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

import requests

# Import printer.py from the parent repo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printer import format_order  # noqa: E402

logger = logging.getLogger("printer_agent")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = "config.json"
MAX_PRINTER_RETRIES = 3
PRINTER_RETRY_BACKOFF = [1.0, 10.0, 30.0]  # seconds per attempt
DEFAULT_POLL_INTERVAL = 5  # seconds

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config(path: str) -> dict:
    """Load agent configuration from a JSON file.

    Returns a dict with keys: server_url, restaurant_id, api_token,
    printer_ip, poll_interval, mode.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If required fields are missing.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path.resolve()}\n"
            f"Copy config.example.json to config.json and fill in your values."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    required = ["server_url", "restaurant_id", "api_token", "printer_ip"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(
            f"Missing required config fields: {', '.join(missing)}"
        )

    cfg.setdefault("poll_interval", DEFAULT_POLL_INTERVAL)
    cfg.setdefault("mode", "file")
    return cfg


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def print_order(
    order: dict,
    restaurant_name: str,
    printer_ip: str,
    mode: str,
) -> bool:
    """Format and print a single order. Returns True on success.

    In "file" mode, writes an HTML preview to printer_agent/tickets/.
    In "escpos" mode, sends raw bytes to the printer via TCP.
    """
    ticket_bytes = format_order(order, restaurant_name, mode=mode)

    if mode == "file":
        tickets_dir = Path(__file__).resolve().parent / "tickets"
        tickets_dir.mkdir(exist_ok=True)
        order_id = order.get("order_id", "unknown")
        filepath = tickets_dir / f"{order_id}.html"
        filepath.write_bytes(ticket_bytes)
        logger.info("Ticket written: %s", filepath)
        return True

    # ESC/POS mode — send to printer via TCP
    host, _, port_str = printer_ip.partition(":")
    try:
        port = int(port_str)
    except ValueError:
        logger.error("Invalid printer_ip '%s': expected host:port", printer_ip)
        return False

    try:
        sock = socket.create_connection((host, port), timeout=10)
        sock.sendall(ticket_bytes)
        sock.close()
        logger.info("Sent order %s to printer %s", order.get("order_id", "?"), printer_ip)
        return True
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logger.warning("Printer %s unreachable: %s", printer_ip, e)
        return False


def print_order_with_retry(
    order: dict,
    restaurant_name: str,
    printer_ip: str,
    mode: str,
) -> bool:
    """Print an order with retry for transient printer failures.

    Retries up to MAX_PRINTER_RETRIES times with increasing backoff.
    Returns True if the order was printed successfully.
    """
    for attempt in range(MAX_PRINTER_RETRIES):
        if print_order(order, restaurant_name, printer_ip, mode):
            return True

        if attempt < MAX_PRINTER_RETRIES - 1:
            delay = PRINTER_RETRY_BACKOFF[attempt]
            logger.warning(
                "Retry %d/%d in %.1fs for order %s",
                attempt + 1, MAX_PRINTER_RETRIES, delay,
                order.get("order_id", "?"),
            )
            time.sleep(delay)

    logger.error(
        "Failed to print order %s after %d attempts",
        order.get("order_id", "?"), MAX_PRINTER_RETRIES,
    )
    return False


# ---------------------------------------------------------------------------
# Server API
# ---------------------------------------------------------------------------


def fetch_unprinted_orders(
    server_url: str,
    restaurant_id: str,
    api_token: str,
) -> list[dict]:
    """Fetch unprinted orders from the server.

    Returns a list of order dicts. Returns empty list on errors
    (server unreachable, auth failure, etc.).
    """
    url = f"{server_url.rstrip('/')}/api/orders"
    params = {"restaurant_id": restaurant_id, "token": api_token}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 403:
            logger.error(
                "Server returned 403 — check that API_TOKEN matches "
                "the server's API_TOKEN env var."
            )
            return []
        else:
            logger.warning("Server returned %d: %s", resp.status_code, resp.text[:200])
            return []
    except requests.RequestException as e:
        logger.warning("Failed to reach server at %s: %s", server_url, e)
        return []


def mark_order_printed(
    server_url: str,
    order_id: str,
    api_token: str,
) -> bool:
    """Mark an order as printed on the server. Idempotent.

    Returns True on success.
    """
    url = f"{server_url.rstrip('/')}/api/orders/{order_id}/printed"
    params = {"token": api_token}
    try:
        resp = requests.post(url, params=params, timeout=10)
        if resp.status_code == 200:
            logger.info("Marked order %s as printed", order_id)
            return True
        else:
            logger.warning(
                "Failed to mark order %s printed: HTTP %d — %s",
                order_id, resp.status_code, resp.text[:200],
            )
            return False
    except requests.RequestException as e:
        logger.warning("Failed to reach server to mark %s printed: %s", order_id, e)
        return False


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------


_shutdown = False


def _on_shutdown(signum, frame):
    global _shutdown
    logger.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def run(cfg: dict) -> None:
    """Main poll loop — fetch unprinted orders, print them, mark done."""
    global _shutdown

    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)

    server_url = cfg["server_url"]
    restaurant_id = cfg["restaurant_id"]
    restaurant_name = cfg.get("restaurant_name", restaurant_id.replace("_", " ").title())
    api_token = cfg["api_token"]
    printer_ip = cfg["printer_ip"]
    poll_interval = cfg["poll_interval"]
    mode = cfg["mode"]

    logger.info(
        "Printer agent started — restaurant=%s, printer=%s, mode=%s, poll=%ds",
        restaurant_id, printer_ip, mode, poll_interval,
    )

    while not _shutdown:
        orders = fetch_unprinted_orders(server_url, restaurant_id, api_token)

        if orders:
            logger.info("Fetched %d unprinted order(s)", len(orders))
            for order in orders:
                if _shutdown:
                    break
                order_id = order.get("order_id", "?")
                logger.info("Processing order %s", order_id)

                if print_order_with_retry(order, restaurant_name, printer_ip, mode):
                    mark_order_printed(server_url, order_id, api_token)
                # If print failed after retries, leave unprinted — retried
                # on next poll cycle. Never mark printed on failure.

        # Sleep in small increments so we respond to shutdown signals promptly
        for _ in range(poll_interval):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Printer agent stopped")



# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Printer agent — polls for unprinted orders and prints them."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config file (default: config.json in agent directory)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve config path relative to the agent directory if not absolute
    config_path = args.config
    if not os.path.isabs(config_path):
        agent_dir = Path(__file__).resolve().parent
        config_path = str(agent_dir / config_path)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        logger.error("Config error: %s", e)
        sys.exit(1)

    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()

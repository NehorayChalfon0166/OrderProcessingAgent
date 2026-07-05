"""Thermal printer formatter — converts orders to kitchen tickets.

Produces ESC/POS bytes for direct printing on 80mm thermal printers.
Also supports a virtual "file" mode that outputs HTML styled as thermal
paper for development and testing without hardware.

Printer transport (TCP port 9100, USB spooler, ePOS) is handled by the
standalone printer agent, not this module. This module only produces
the bytes to send — how they reach the printer is the agent's concern.
"""

from __future__ import annotations

from datetime import datetime


# ---------------------------------------------------------------------------
# ESC/POS control codes
# ---------------------------------------------------------------------------

ESC = b"\x1b"
INIT = ESC + b"@"          # Initialize printer
ALIGN_LEFT = ESC + b"a\x00"
ALIGN_CENTER = ESC + b"a\x01"
BOLD_ON = ESC + b"E\x01"
BOLD_OFF = ESC + b"E\x00"
FEED = ESC + b"d\x03"      # Feed 3 lines
CUT = b"\x1d\x56\x00"      # Paper cut (partial)

# ---------------------------------------------------------------------------
# Ticket layout constants
# ---------------------------------------------------------------------------

LINE_WIDTH = 42  # characters per line on 80mm thermal paper at default font
SEPARATOR = "─" * LINE_WIDTH
BOLD_SEPARATOR = "═" * LINE_WIDTH


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_order(
    order: dict,
    restaurant_name: str,
    mode: str = "escpos",
) -> bytes:
    """Convert an order dict to printer-ready bytes.

    Args:
        order: Order data dict with keys: order_id, items, customer,
               subtotal, delivery_fee, total, order_type, created_at,
               customer_name, customer_phone.
        restaurant_name: Display name for the ticket header.
        mode: "escpos" for thermal printer bytes, "file" for HTML preview.

    Returns:
        In "escpos" mode: ESC/POS bytes with init, bold header, cut, feed.
        In "file" mode: UTF-8 HTML bytes styled like thermal paper.
    """
    text = _format_ticket(order, restaurant_name)

    if mode == "file":
        return _wrap_html(text).encode("utf-8")
    else:
        return _wrap_escpos(text)


def _wrap_escpos(text: str) -> bytes:
    """Wrap ticket text with ESC/POS commands for a professional receipt."""
    parts: list[bytes] = [INIT, ALIGN_CENTER, BOLD_ON]
    # Find the header block (first BOLD_SEPARATOR to second BOLD_SEPARATOR)
    lines = text.split("\n")
    in_header = False
    body_start = 0
    for i, line in enumerate(lines):
        if line == BOLD_SEPARATOR:
            if not in_header:
                in_header = True
            else:
                body_start = i + 1
                break
    # Add header lines centered and bold
    for line in lines[:body_start]:
        parts.append((line + "\n").encode("utf-8"))
    parts.append(BOLD_OFF)
    parts.append(ALIGN_LEFT)
    # Add body
    for line in lines[body_start:]:
        parts.append((line + "\n").encode("utf-8"))
    # Footer: cut and feed
    parts.append(CUT)
    parts.append(FEED)
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Ticket layout
# ---------------------------------------------------------------------------


def _format_ticket(order: dict, restaurant_name: str) -> str:
    """Build the ticket text."""
    lines: list[str] = []

    # Header
    lines.append(BOLD_SEPARATOR)
    lines.append(restaurant_name.center(LINE_WIDTH))
    lines.append("KITCHEN TICKET".center(LINE_WIDTH))
    lines.append(BOLD_SEPARATOR)
    lines.append("")

    # Order info
    order_id = order.get("order_id", "?")
    order_type = order.get("order_type", "pickup").upper()
    created_at = order.get("created_at", "")
    try:
        ts = datetime.fromisoformat(created_at)
        time_str = ts.strftime("%H:%M")
    except (ValueError, TypeError):
        time_str = created_at

    lines.append(f"Order: #{order_id}")
    lines.append(f"Type:  {order_type}")
    lines.append(f"Time:  {time_str}")
    lines.append("")

    # Customer
    customer = order.get("customer", {})
    name = customer.get("name", "") or order.get("customer_name", "")
    phone = customer.get("phone", "") or order.get("customer_phone", "")
    address = customer.get("address", "")

    if name:
        lines.append(f"Customer: {name}")
    if phone:
        lines.append(f"Phone:    {phone}")
    if address:
        lines.append(f"Address:  {address}")
    if name or phone:
        lines.append("")

    # Items
    lines.append(SEPARATOR)
    items = order.get("items", [])
    for item in items:
        qty = item.get("quantity", 1)
        item_name = item.get("name", "?")
        size = item.get("size")
        toppings = item.get("toppings", [])
        instructions = item.get("special_instructions")

        line = f" {qty}x {item_name}"
        if size:
            line += f" ({size})"
        lines.append(line[:LINE_WIDTH])

        if toppings:
            for top in toppings:
                top_name = top.get("name", str(top))
                lines.append(f"     + {top_name}"[:LINE_WIDTH])

        if instructions:
            lines.append(f"     [{instructions}]"[:LINE_WIDTH])

    lines.append(SEPARATOR)
    lines.append("")

    # Totals
    subtotal = order.get("subtotal", 0)
    delivery_fee = order.get("delivery_fee", 0)
    total = order.get("total", 0)

    lines.append(f"SUBTOTAL:      {subtotal:>6.2f}".rjust(LINE_WIDTH))
    if delivery_fee > 0:
        lines.append(f"DELIVERY FEE:  {delivery_fee:>6.2f}".rjust(LINE_WIDTH))
    lines.append(f"TOTAL:         {total:>6.2f}".rjust(LINE_WIDTH))
    lines.append("")

    # Special instructions on the order level (not per-item)
    order_notes = order.get("special_instructions")
    if order_notes:
        lines.append(SEPARATOR)
        lines.append(f'    "{order_notes}"')
        lines.append("")

    # Footer
    lines.append(BOLD_SEPARATOR)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML wrapper for virtual printer mode
# ---------------------------------------------------------------------------


def _wrap_html(text: str) -> str:
    """Wrap ticket text in HTML styled like 80mm thermal paper."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Kitchen Ticket</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Code+Pro&display=swap');
  body {{
    background: #f0f0f0;
    display: flex;
    justify-content: center;
    padding: 20px;
  }}
  .ticket {{
    background: white;
    width: 80mm;
    min-height: 100mm;
    padding: 5mm;
    font-family: 'Source Code Pro', 'Courier New', monospace;
    font-size: 11px;
    line-height: 1.3;
    white-space: pre;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  }}
</style>
</head>
<body>
<div class="ticket">
{escaped}
</div>
</body>
</html>"""

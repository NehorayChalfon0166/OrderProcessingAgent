"""Menu manager — validate and apply atomic edits to restaurant menu files.

Writes menu JSON atomically (temp file → fsync → rename). Never partially
writes. The caller is responsible for reloading the Catalogue and
PricingEngine after a successful edit.

Usage:
    from menu_manager import manage_menu, MenuAction

    actions = [
        MenuAction("set_price", "pizza_margherita", variant_id="large", value=60.0),
        MenuAction("out_of_stock", "pizza_margherita", variant_id="large"),
        MenuAction("describe", "drink_cola", value="Ice-cold cola"),
    ]
    result = manage_menu("menus/marios_pizzeria.json", actions)
    if result.success:
        # Restart server to reload catalogue
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MenuAction:
    """A single atomic menu edit."""

    action: str
    """One of: set_price, out_of_stock, in_stock, describe."""

    item_id: str
    """Menu item ID (e.g. 'pizza_margherita')."""

    variant_id: str | None = None
    """Size key for sized items (e.g. 'large'). None for flat-price items."""

    value: str | float | None = None
    """New price (float for set_price) or description (str for describe)."""


@dataclass
class MenuActionResult:
    """Result of a manage_menu call."""

    success: bool
    """True if all actions were applied."""

    message: str
    """Human-readable summary."""

    actions_applied: int = 0
    """Number of actions successfully applied."""

    errors: list[str] = field(default_factory=list)
    """Validation errors, one per failed action."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def manage_menu(menu_path: str, actions: list[MenuAction]) -> MenuActionResult:
    """Apply a batch of menu actions atomically.

    All actions are validated first. If any fail, no changes are written.
    If all pass, the menu is written to a temp file and atomically renamed
    over the original.

    Args:
        menu_path: Path to the restaurant's menu JSON file.
        actions: One or more MenuAction objects.

    Returns:
        MenuActionResult with success status and details.
    """
    menu_file = Path(menu_path)
    if not menu_file.exists():
        return MenuActionResult(
            success=False,
            message=f"Menu file not found: {menu_file.resolve()}",
        )

    # Load current menu
    try:
        menu = json.loads(menu_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return MenuActionResult(
            success=False,
            message=f"Invalid menu JSON: {e}",
        )

    # Build item index
    items_index = _build_item_index(menu)

    # Validate all actions first
    errors: list[str] = []
    for i, action in enumerate(actions):
        err = _validate_action(action, items_index)
        if err:
            errors.append(f"Action {i + 1} ({action.action} {action.item_id}): {err}")

    if errors:
        return MenuActionResult(
            success=False,
            message=f"{len(errors)} action(s) failed validation.",
            errors=errors,
        )

    # Apply actions to a deep copy
    menu = copy.deepcopy(menu)
    items_index = _build_item_index(menu)
    applied = 0
    for action in actions:
        _apply_action(action, items_index)
        applied += 1

    # Write atomically: temp file → fsync → rename
    _atomic_write(menu_file, menu)

    return MenuActionResult(
        success=True,
        message=f"{applied} action(s) applied. Restart server to reload.",
        actions_applied=applied,
    )


# ---------------------------------------------------------------------------
# Item index
# ---------------------------------------------------------------------------


def _build_item_index(menu: dict) -> dict[str, dict]:
    """Build a flat item_id → item_dict index from the menu."""
    index: dict[str, dict] = {}
    for category in menu.get("categories", []):
        for item in category.get("items", []):
            index[item["id"]] = item
    # Also index deals
    for deal in menu.get("deals", []):
        index[deal["id"]] = deal
    return index


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_action(action: MenuAction, index: dict[str, dict]) -> str | None:
    """Validate an action. Returns an error string or None if valid."""

    if action.action not in ("set_price", "out_of_stock", "in_stock", "describe"):
        return f"Unknown action '{action.action}'. Valid: set_price, out_of_stock, in_stock, describe"

    item = index.get(action.item_id)
    if item is None:
        return f"Item '{action.item_id}' not found in menu"

    # Variant validation for sized items
    if action.variant_id is not None:
        sizes = item.get("sizes")
        if sizes is None:
            return f"Item '{action.item_id}' is not sized — no variant '{action.variant_id}'"
        if action.variant_id not in sizes:
            valid = ", ".join(sorted(sizes.keys()))
            return f"Variant '{action.variant_id}' not found for '{action.item_id}'. Valid: {valid}"

    # Per-action validation
    if action.action == "set_price":
        if action.value is None:
            return "set_price requires a value (the new price)"
        try:
            price = float(action.value)
            if price <= 0:
                return f"Price must be positive, got {price}"
        except (TypeError, ValueError):
            return f"Invalid price value: {action.value}"

    elif action.action in ("out_of_stock", "in_stock"):
        # No extra validation — just needs item to exist
        pass

    elif action.action == "describe":
        if action.value is None:
            return "describe requires a value (the new description)"
        if not isinstance(action.value, str):
            return f"Description must be a string, got {type(action.value).__name__}"

    return None


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_action(action: MenuAction, index: dict[str, dict]) -> None:
    """Apply a validated action to the menu in-place."""
    item = index[action.item_id]

    if action.action == "set_price":
        price = float(action.value) if action.value else 0.0
        if action.variant_id is not None:
            item["sizes"][action.variant_id] = price
        else:
            item["price"] = price

    elif action.action == "out_of_stock":
        if action.variant_id is not None:
            # Mark specific variant unavailable
            item.setdefault("unavailable_variants", [])
            variants = item["unavailable_variants"]
            if action.variant_id not in variants:
                variants.append(action.variant_id)
        else:
            item["available"] = False

    elif action.action == "in_stock":
        if action.variant_id is not None:
            variants = item.get("unavailable_variants", [])
            if action.variant_id in variants:
                variants.remove(action.variant_id)
            if not variants:
                item.pop("unavailable_variants", None)
        else:
            item["available"] = True

    elif action.action == "describe":
        item["description"] = str(action.value)


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, data: dict) -> None:
    """Write data to path atomically via temp file + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_fd = os.open(str(tmp), os.O_RDONLY)
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        os.replace(str(tmp), str(path))
    except Exception:
        # Clean up temp file on failure
        if tmp.exists():
            tmp.unlink()
        raise

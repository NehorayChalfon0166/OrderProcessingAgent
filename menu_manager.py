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
from dataclasses import dataclass, field
from pathlib import Path

from utils import atomic_write_json


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MenuAction:
    """A single atomic menu edit."""

    action: str
    """One of: set_price, out_of_stock, in_stock, describe, update_item,
    add_item, remove_item, add_category, add_deal, update_deal, remove_deal,
    add_topping, update_topping, remove_topping."""

    item_id: str
    """Menu item/category/deal/topping ID."""

    variant_id: str | None = None
    """Size key for sized items. None for flat-price items."""

    value: str | float | dict | None = None
    """New price, description, or full item data dict for add/update actions."""

    extra: dict | None = None
    """Additional context (e.g. category_name for add_item)."""


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
        _apply_action(action, items_index, menu)
        # Rebuild index after structural changes (add/remove)
        if action.action in ("add_item", "remove_item", "add_deal", "remove_deal",
                             "add_topping", "remove_topping", "add_category"):
            items_index = _build_item_index(menu)
        applied += 1

    atomic_write_json(menu_file, menu)

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


_VALID_ACTIONS = {
    "set_price", "out_of_stock", "in_stock", "describe",
    "update_item", "add_item", "remove_item",
    "add_category", "add_deal", "update_deal", "remove_deal",
    "add_topping", "update_topping", "remove_topping",
}


def _validate_action(action: MenuAction, index: dict[str, dict]) -> str | None:
    """Validate an action. Returns an error string or None if valid."""

    if action.action not in _VALID_ACTIONS:
        return f"Unknown action '{action.action}'"

    # Actions that create new items don't need existing item check
    if action.action in ("add_item", "add_category", "add_deal", "add_topping"):
        if action.action == "add_item" and not action.extra:
            return "add_item requires extra.category_name"
        if action.action in ("add_item", "add_deal", "add_topping") and not isinstance(action.value, dict):
            return f"{action.action} requires value to be a dict with item data"
        if action.action == "add_category" and not isinstance(action.value, str):
            return "add_category requires value to be the category name (str)"
        return None

    # Removal actions
    if action.action in ("remove_item", "remove_deal", "remove_topping"):
        if action.item_id not in index:
            return f"Item '{action.item_id}' not found"
        return None

    # Actions that update existing items
    item = index.get(action.item_id)
    if item is None:
        return f"Item '{action.item_id}' not found in menu"

    if action.action in ("set_price", "out_of_stock", "in_stock", "describe"):
        if action.variant_id is not None:
            sizes = item.get("sizes")
            if sizes is None:
                return f"Item '{action.item_id}' is not sized"
            if action.variant_id not in sizes:
                return f"Variant '{action.variant_id}' not found"

        if action.action == "set_price":
            if action.value is None:
                return "set_price requires a value"
            try:
                if float(action.value) <= 0:
                    return "Price must be positive"
            except (TypeError, ValueError):
                return f"Invalid price: {action.value}"
        elif action.action == "describe":
            if not isinstance(action.value, str):
                return "describe requires a string value"

    if action.action == "update_item":
        if not isinstance(action.value, dict):
            return "update_item requires value to be a dict"

    return None


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_action(action: MenuAction, index: dict[str, dict], menu: dict) -> None:
    """Apply a validated action to the menu in-place."""
    item = index.get(action.item_id) if action.item_id in index else None

    if action.action == "set_price":
        price = float(action.value) if action.value else 0.0
        if action.variant_id is not None:
            item["sizes"][action.variant_id] = price
        else:
            item["price"] = price

    elif action.action == "out_of_stock":
        if action.variant_id is not None:
            item.setdefault("unavailable_variants", [])
            if action.variant_id not in item["unavailable_variants"]:
                item["unavailable_variants"].append(action.variant_id)
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
            item.pop("unavailable_variants", None)

    elif action.action == "describe":
        item["description"] = str(action.value)

    elif action.action == "update_item":
        data = action.value if isinstance(action.value, dict) else {}
        for k, v in data.items():
            if k in ("id",):
                continue  # never change ID
            item[k] = v

    elif action.action == "add_item":
        data = action.value if isinstance(action.value, dict) else {}
        cat_name = (action.extra or {}).get("category_name", "")
        for cat in menu.get("categories", []):
            if cat["name"] == cat_name:
                cat.setdefault("items", []).append(data)
                index[data.get("id", action.item_id)] = data
                break

    elif action.action == "remove_item":
        for cat in menu.get("categories", []):
            cat["items"] = [i for i in cat.get("items", []) if i["id"] != action.item_id]
        index.pop(action.item_id, None)

    elif action.action == "add_category":
        name = str(action.value) if action.value else action.item_id
        menu.setdefault("categories", []).append({"name": name, "items": []})

    elif action.action == "add_deal":
        data = action.value if isinstance(action.value, dict) else {}
        menu.setdefault("deals", []).append(data)
        index[data.get("id", action.item_id)] = data

    elif action.action == "update_deal":
        data = action.value if isinstance(action.value, dict) else {}
        for k, v in data.items():
            if k not in ("id",):
                item[k] = v

    elif action.action == "remove_deal":
        menu["deals"] = [d for d in menu.get("deals", []) if d["id"] != action.item_id]
        index.pop(action.item_id, None)

    elif action.action == "add_topping":
        data = action.value if isinstance(action.value, dict) else {}
        menu.setdefault("toppings", []).append(data)
        index[data.get("id", action.item_id)] = data

    elif action.action == "update_topping":
        data = action.value if isinstance(action.value, dict) else {}
        for k, v in data.items():
            if k not in ("id",):
                item[k] = v

    elif action.action == "remove_topping":
        menu["toppings"] = [t for t in menu.get("toppings", []) if t["id"] != action.item_id]
        index.pop(action.item_id, None)

"""Restaurant registry — multi-tenant restaurant configuration and lookups.

Loads restaurants.json at startup and provides per-restaurant Catalogue and
PricingEngine instances. Each restaurant is identified by a slug (id) and
must have a Twilio WhatsApp phone number for webhook routing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from catalogue import Catalogue
from pricing import PricingEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RestaurantConfig:
    """Immutable configuration for one restaurant tenant.

    Attributes:
        id: Short slug used as the restaurant identity everywhere
            (file paths, session keys, order output).
        name: Human-readable display name used in prompts and order files.
        menu_path: Path to the restaurant's menu JSON file.
        twilio_phone: The Twilio WhatsApp number customers message to reach
            this restaurant (e.g. "+14155238886").
        owner_phone: The restaurant owner's personal WhatsApp number.
            New-order notifications are sent here (not to twilio_phone).
    """

    id: str
    name: str
    menu_path: str
    twilio_phone: str
    owner_phone: str


@dataclass
class RestaurantContext:
    """A fully-loaded restaurant with catalogue and pricing engine ready.

    Bundles the config with its live Catalogue and PricingEngine instances
    so callers get everything they need in one object.
    """

    config: RestaurantConfig
    catalogue: Catalogue
    pricing: PricingEngine


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RestaurantRegistry:
    """Loads and manages all restaurant configurations.

    Reads restaurants.json at startup, validates every restaurant has a
    Twilio phone number, and creates Catalogue + PricingEngine for each.

    Usage::

        registry = RestaurantRegistry("restaurants.json")
        ctx = registry.get_by_id("marios_pizzeria")
        ctx2 = registry.get_by_twilio_phone("+14155238886")
    """

    def __init__(self, path: str = "restaurants.json") -> None:
        """Load and validate all restaurants from the config file.

        Args:
            path: Path to restaurants.json.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If a restaurant is missing its twilio_phone.
        """
        self._by_id: dict[str, RestaurantContext] = {}
        self._by_phone: dict[str, RestaurantContext] = {}
        self._configs: list[RestaurantConfig] = []
        self._load(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_by_id(self, restaurant_id: str) -> RestaurantContext | None:
        """Look up a restaurant by its slug (e.g. "marios_pizzeria")."""
        return self._by_id.get(restaurant_id)

    def get_by_twilio_phone(self, phone: str) -> RestaurantContext | None:
        """Look up a restaurant by its Twilio WhatsApp number.

        The phone number is cleaned of any "whatsapp:" prefix before lookup,
        so both "+14155238886" and "whatsapp:+14155238886" work.
        """
        cleaned = phone.removeprefix("whatsapp:")
        return self._by_phone.get(cleaned)

    def get_default(self) -> RestaurantContext:
        """Return the first restaurant in the configuration.

        Raises:
            ValueError: If no restaurants are configured.
        """
        if not self._configs:
            raise ValueError("No restaurants configured")
        return self._by_id[self._configs[0].id]

    def list_restaurants(self) -> list[RestaurantConfig]:
        """Return all restaurant configurations."""
        return list(self._configs)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, path: str) -> None:
        """Load restaurants.json, validate, and build contexts."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(
                f"Restaurant configuration not found: {config_path.resolve()}\n"
                f"Create a {path} file to configure restaurants."
            )

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        restaurants = raw.get("restaurants", {})
        if not restaurants:
            raise ValueError(
                f"No restaurants defined in {path}. "
                f"Add entries under the 'restaurants' key."
            )

        for rid, data in restaurants.items():
            config = self._parse_config(rid, data)
            self._configs.append(config)

            catalogue = Catalogue(config.menu_path)
            pricing = PricingEngine(catalogue.menu_data)
            ctx = RestaurantContext(
                config=config,
                catalogue=catalogue,
                pricing=pricing,
            )

            self._by_id[config.id] = ctx
            self._by_phone[config.twilio_phone] = ctx
            logger.info(
                "Loaded restaurant '%s' (%s) — menu: %s, phone: %s, owner: %s",
                config.name, config.id, config.menu_path, config.twilio_phone,
                config.owner_phone,
            )

    @staticmethod
    def _parse_config(rid: str, data: dict) -> RestaurantConfig:
        """Parse and validate a single restaurant entry."""
        name = data.get("name", "")
        menu_path = data.get("menu_path", "")
        twilio_phone = data.get("twilio_phone", "")
        owner_phone = data.get("owner_phone", "")

        if not name:
            raise ValueError(
                f"Restaurant '{rid}' is missing required field 'name'"
            )
        if not menu_path:
            raise ValueError(
                f"Restaurant '{rid}' is missing required field 'menu_path'"
            )
        if not twilio_phone:
            raise ValueError(
                f"Restaurant '{rid}' is missing required field 'twilio_phone'. "
                f"Every restaurant must have a Twilio WhatsApp number."
            )
        if not owner_phone:
            raise ValueError(
                f"Restaurant '{rid}' is missing required field 'owner_phone'. "
                f"Every restaurant must have an owner phone number for "
                f"new-order notifications."
            )

        return RestaurantConfig(
            id=rid,
            name=name,
            menu_path=menu_path,
            twilio_phone=twilio_phone,
            owner_phone=owner_phone,
        )


# ---------------------------------------------------------------------------
# Restaurant config persistence
# ---------------------------------------------------------------------------

_BLANK_MENU = {
    "restaurant_name": "",
    "currency": "USD",
    "categories": [],
    "toppings": [],
    "deals": [],
    "delivery_fee": 0.0,
    "min_order_amount": 0.0,
}


def save_restaurant(
    path: str,
    restaurant_id: str,
    name: str,
    twilio_phone: str,
    owner_phone: str,
    menu_path: str | None = None,
) -> str:
    """Add or update a restaurant in restaurants.json. Writes atomically.

    If *menu_path* is not provided, it defaults to
    ``menus/{restaurant_id}.json``. If that file doesn't exist, a blank
    menu template is created.

    Returns a human-readable result message. The caller is responsible
    for restarting the server to reload the registry.

    Raises:
        FileNotFoundError: If restaurants.json doesn't exist.
        ValueError: If required fields are missing.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Restaurant config not found: {config_path.resolve()}")

    if not name:
        raise ValueError("Restaurant name is required")
    if not twilio_phone:
        raise ValueError("Twilio phone number is required")
    if not owner_phone:
        raise ValueError("Owner phone number is required")

    menu = menu_path or f"menus/{restaurant_id}.json"
    menu_full = Path(menu)
    if not menu_full.is_absolute():
        menu_full = config_path.parent / menu

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    restaurants = raw.get("restaurants", {})

    is_new = restaurant_id not in restaurants
    restaurants[restaurant_id] = {
        "name": name,
        "menu_path": str(menu),
        "twilio_phone": twilio_phone,
        "owner_phone": owner_phone,
    }
    raw["restaurants"] = restaurants

    _atomic_write(config_path, raw)

    # Auto-create blank menu for new restaurants
    if is_new and not menu_full.exists():
        menu_full.parent.mkdir(parents=True, exist_ok=True)
        menu_data = dict(_BLANK_MENU)
        menu_data["restaurant_name"] = name
        menu_full.write_text(
            json.dumps(menu_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return (
            f"Restaurant '{restaurant_id}' added. Blank menu created at {menu}. "
            f"Restart server to apply."
        )
    else:
        action = "Added" if is_new else "Updated"
        return f"{action} restaurant '{restaurant_id}'. Restart server to apply."


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON data to *path* atomically via temp file + rename."""
    import os as _os
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        fd = _os.open(str(tmp), _os.O_RDONLY)
        _os.fsync(fd)
        _os.close(fd)
        _os.replace(str(tmp), str(path))
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise

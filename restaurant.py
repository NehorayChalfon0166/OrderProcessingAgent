"""Restaurant registry — multi-tenant restaurant configuration and lookups.

Loads restaurants.json at startup and provides per-restaurant Catalogue and
PricingEngine instances. Each restaurant is identified by a slug (id) and
must have a Twilio WhatsApp phone number for webhook routing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from catalogue import Catalogue
from pricing import PricingEngine
from utils import atomic_write_json

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
        voice_phone: Optional Twilio voice number customers call to reach
            this restaurant. If empty, voice ordering is disabled for this
            restaurant.
    """

    id: str
    name: str
    menu_path: str
    twilio_phone: str
    owner_phone: str
    voice_phone: str = ""
    printer_token: str = ""


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
        self._path = path
        self._by_id: dict[str, RestaurantContext] = {}
        self._by_phone: dict[str, RestaurantContext] = {}
        self._by_voice_phone: dict[str, RestaurantContext] = {}
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

        The phone number is cleaned of any "whatsapp:" prefix and whitespace
        before lookup, so both "+14155238886" and "whatsapp:+14155238886" work.
        """
        cleaned = phone.removeprefix("whatsapp:").strip()
        return self._by_phone.get(cleaned)

    def get_by_voice_phone(self, phone: str) -> RestaurantContext | None:
        """Look up a restaurant by its Twilio voice phone number.

        The phone number is cleaned of whitespace before lookup.
        Returns None if the number is not registered as a voice number
        for any restaurant.
        """
        cleaned = phone.strip()
        return self._by_voice_phone.get(cleaned)

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

    def reload(self) -> None:
        """Reload all restaurants from the config file."""
        self._by_id.clear()
        self._by_phone.clear()
        self._by_voice_phone.clear()
        self._configs.clear()
        self._load(self._path)

    def reload_restaurant(self, restaurant_id: str) -> None:
        """Hot-reload one restaurant's Catalogue and PricingEngine.

        Used after menu edits via the dashboard so changes take effect
        without a server restart.
        """
        ctx = self._by_id.get(restaurant_id)
        if ctx is None:
            return
        ctx.catalogue = Catalogue(ctx.config.menu_path)
        ctx.pricing = PricingEngine(ctx.catalogue.menu_data)
        logger.info(
            "Hot-reloaded menu for '%s' from %s",
            restaurant_id, ctx.config.menu_path,
        )

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
            try:
                catalogue = Catalogue(config.menu_path)
                pricing = PricingEngine(catalogue.menu_data)
            except Exception as e:
                logger.error(
                    "Failed to load menu for '%s' (%s): %s — skipping this restaurant",
                    rid, config.menu_path, e,
                )
                continue

            self._configs.append(config)
            ctx = RestaurantContext(config=config, catalogue=catalogue, pricing=pricing)
            self._by_id[config.id] = ctx
            self._by_phone[config.twilio_phone] = ctx
            if config.voice_phone:
                self._by_voice_phone[config.voice_phone] = ctx
            logger.info(
                "Loaded restaurant '%s' (%s) — menu: %s, phone: %s, owner: %s%s",
                config.name, config.id, config.menu_path, config.twilio_phone,
                config.owner_phone,
                f", voice: {config.voice_phone}" if config.voice_phone else "",
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

        voice_phone = data.get("voice_phone", "")
        printer_token = data.get("printer_token", "")

        return RestaurantConfig(
            id=rid,
            name=name,
            menu_path=menu_path,
            twilio_phone=twilio_phone,
            owner_phone=owner_phone,
            voice_phone=voice_phone,
            printer_token=printer_token,
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


_RESTAURANT_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def save_restaurant(
    path: str,
    restaurant_id: str,
    name: str,
    twilio_phone: str,
    owner_phone: str,
    menu_path: str | None = None,
    voice_phone: str = "",
) -> str:
    """Add or update a restaurant in restaurants.json. Writes atomically.

    If *menu_path* is not provided, it defaults to
    ``menus/{restaurant_id}.json``. If that file doesn't exist, a blank
    menu template is created.

    Returns a human-readable result message. The caller is responsible
    for restarting the server to reload the registry.

    Raises:
        FileNotFoundError: If restaurants.json doesn't exist.
        ValueError: If required fields are missing or invalid.
    """
    if not _RESTAURANT_ID_RE.match(restaurant_id):
        raise ValueError(
            f"Invalid restaurant ID '{restaurant_id}'. "
            f"Must be 1-64 chars: lowercase letters, digits, underscores."
        )

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
    entry: dict[str, str] = {
        "name": name,
        "menu_path": str(menu),
        "twilio_phone": twilio_phone,
        "owner_phone": owner_phone,
    }
    if voice_phone:
        entry["voice_phone"] = voice_phone
    restaurants[restaurant_id] = entry
    raw["restaurants"] = restaurants

    atomic_write_json(config_path, raw)

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

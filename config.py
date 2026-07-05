"""Application configuration for the order processing agent.

All settings come from environment variables. In development, .env is loaded
as a convenience. In production, secrets are injected via the environment
(never from files on disk).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Load .env for local development only — production uses real env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class AppConfig:
    """Immutable application configuration.

    All values are resolved from environment variables. Required secrets
    (api_key, twilio credentials) have no defaults and will raise clear
    errors if missing.
    """

    # LLM (required)
    llm_api_key: str
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_temperature: float = 0.3

    # Agent tunables
    max_iterations: int = 5
    agent_timeout_seconds: float = 45.0
    rate_limit_max_per_minute: int = 20
    session_stale_hours: float = 2.0
    lock_ttl_seconds: float = 3600.0
    session_cleanup_days: int = 30

    # Paths
    restaurants_path: str = "restaurants.json"
    db_path: str = "order_agent.db"
    orders_dir: str = "orders"
    sessions_dir: str = "sessions"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    # Stripe (dormant — not available in Israel)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Auth
    api_token: str = ""

    # Debug
    debug: bool = False
    log_json: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build configuration from environment variables.

        Required env vars (will error if missing):
            DEEPSEEK_API_KEY

        Optional (will warn if not set for server mode):
            TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER,
            API_TOKEN
        """
        api_key = os.getenv("DEEPSEEK_API_KEY", "")

        config = cls(
            llm_api_key=api_key,
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            max_iterations=int(os.getenv("MAX_ITERATIONS", "5")),
            agent_timeout_seconds=float(os.getenv("AGENT_TIMEOUT_SECONDS", "45")),
            rate_limit_max_per_minute=int(os.getenv("RATE_LIMIT_MAX_PER_MINUTE", "20")),
            session_stale_hours=float(os.getenv("SESSION_STALE_HOURS", "2")),
            lock_ttl_seconds=float(os.getenv("LOCK_TTL_SECONDS", "3600")),
            session_cleanup_days=int(os.getenv("SESSION_CLEANUP_DAYS", "30")),
            restaurants_path=os.getenv("RESTAURANTS_PATH", "restaurants.json"),
            db_path=os.getenv("DB_PATH", "order_agent.db"),
            orders_dir=os.getenv("ORDERS_DIR", "orders"),
            sessions_dir=os.getenv("SESSIONS_DIR", "sessions"),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_whatsapp_number=os.getenv("TWILIO_WHATSAPP_NUMBER", ""),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", ""),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            api_token=os.getenv("API_TOKEN", ""),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_json=os.getenv("LOG_JSON", "false").lower() == "true",
        )
        config._validate()
        return config

    def _validate(self) -> None:
        """Validate required configuration."""
        if not self.llm_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required.\n"
                "  Set it via environment variable or in .env for local dev."
            )
        if not self.llm_model:
            raise ValueError("LLM_MODEL is required.")
        if not self.llm_base_url:
            raise ValueError("LLM_BASE_URL is required.")

        # Non-fatal warnings for paths that may be created at runtime
        for attr, label in [
            ("restaurants_path", "Restaurants config"),
            ("orders_dir", "Orders directory"),
        ]:
            path = getattr(self, attr, "")
            if path and not os.path.exists(path):
                logger.warning("%s not found: %s", label, path)

    @property
    def twilio_configured(self) -> bool:
        """True if all Twilio credentials are present."""
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_whatsapp_number)

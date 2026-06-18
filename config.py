"""Application configuration for the order processing agent.

Loads settings from environment variables (with .env support). Single-provider
— DeepSeek v4-flash. No multi-provider presets; if switching providers later,
change the base URL and model here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppConfig:
    """Immutable application configuration.

    All values are resolved at construction time from environment variables.
    Use AppConfig.from_env() to build an instance.
    """

    # LLM
    llm_api_key: str
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com"

    # Paths
    menu_path: str = "menu.json"  # TODO: remove after multi-restaurant stable
    restaurants_path: str = "restaurants.json"  # NEW — multi-restaurant config
    orders_dir: str = "orders"
    sessions_dir: str = "sessions"

    # Twilio (used by integration branches, harmless on master)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "+14155238886"  # sandbox default

    # Debug
    debug: bool = False

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build configuration from environment variables.

        Raises:
            ValueError: If DEEPSEEK_API_KEY is not set.
        """
        api_key = os.getenv("DEEPSEEK_API_KEY", "")

        config = cls(
            llm_api_key=api_key,
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            menu_path=os.getenv("MENU_PATH", "menu.json"),
            restaurants_path=os.getenv("RESTAURANTS_PATH", "restaurants.json"),
            orders_dir=os.getenv("ORDERS_DIR", "orders"),
            sessions_dir=os.getenv("SESSIONS_DIR", "sessions"),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            twilio_whatsapp_number=os.getenv(
                "TWILIO_WHATSAPP_NUMBER", "+14155238886"
            ),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )

        config._validate()
        return config

    def _validate(self) -> None:
        """Validate configuration and raise clear errors for common problems."""
        if not self.llm_api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is required. "
                "Set it in your .env file or as an environment variable.\n"
                "  Hint: Copy .env.example to .env and add your DeepSeek API key."
            )

        if not self.llm_model:
            raise ValueError(
                "LLM_MODEL is required. Set it in your .env file or as an "
                "environment variable."
            )

        if not self.llm_base_url:
            raise ValueError(
                "LLM_BASE_URL is required. Set it in your .env file or as an "
                "environment variable."
            )

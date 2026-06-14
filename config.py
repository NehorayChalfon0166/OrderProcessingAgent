"""
config.py — Application configuration for the pizzeria order processing agent.

Loads settings from environment variables (with .env support) and provides
sensible defaults through provider presets. Switch LLM backends by changing
a single env var.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env file from project root (if it exists)
load_dotenv()


# =============================================================================
# Provider Presets
# =============================================================================
# Just change LLM_PROVIDER to switch between backends.
# Each preset defines a base URL and default model so you only need to set
# LLM_PROVIDER and LLM_API_KEY to get started.

PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-3.1-flash-lite",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.1",
        "api_key": "ollama",  # Ollama doesn't need a real API key
    },
}


# =============================================================================
# Application Configuration
# =============================================================================


@dataclass
class AppConfig:
    """
    Immutable application configuration.

    All values are resolved at construction time from environment variables
    and provider presets. Use AppConfig.from_env() to build an instance.
    """

    # LLM Settings
    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str

    # App Settings
    menu_path: str
    orders_dir: str
    debug: bool

    @classmethod
    def from_env(cls) -> "AppConfig":
        """
        Build configuration from environment variables.

        Resolution order for LLM settings:
          1. Explicit env var (e.g. LLM_MODEL)
          2. Provider preset default
          3. Empty string (will fail validation if required)

        Raises:
            ValueError: If the API key is missing for providers that require one.
            ValueError: If the provider is unknown and no base URL is supplied.
        """
        provider = os.getenv("LLM_PROVIDER", "groq").lower()
        preset = PROVIDER_CONFIGS.get(provider, {})

        # Resolve API key: env var > preset > empty
        api_key = os.getenv("LLM_API_KEY", preset.get("api_key", ""))

        # Resolve model and base URL with env var overrides
        model = os.getenv("LLM_MODEL", preset.get("default_model", ""))
        base_url = os.getenv("LLM_BASE_URL", preset.get("base_url", ""))

        config = cls(
            llm_provider=provider,
            llm_api_key=api_key,
            llm_model=model,
            llm_base_url=base_url,
            menu_path=os.getenv("MENU_PATH", "menu.json"),
            orders_dir=os.getenv("ORDERS_DIR", "orders"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )

        config._validate()
        return config

    def _validate(self) -> None:
        """
        Validate configuration and raise clear errors for common problems.

        Raises:
            ValueError: With a descriptive message if configuration is invalid.
        """
        # API key is required for all providers except Ollama (local)
        if self.llm_provider != "ollama" and not self.llm_api_key:
            raise ValueError(
                f"LLM_API_KEY is required for provider '{self.llm_provider}'. "
                f"Set it in your .env file or as an environment variable.\n"
                f"  Hint: Copy .env.example to .env and add your key."
            )

        if not self.llm_base_url:
            known_providers = ", ".join(sorted(PROVIDER_CONFIGS.keys()))
            raise ValueError(
                f"No base URL configured for provider '{self.llm_provider}'. "
                f"Either use a known provider ({known_providers}) or set "
                f"LLM_BASE_URL explicitly."
            )

        if not self.llm_model:
            raise ValueError(
                f"No model configured for provider '{self.llm_provider}'. "
                f"Set LLM_MODEL in your .env file or as an environment variable."
            )

"""Tests for config.py — application configuration."""

import os
from unittest import mock

import pytest

from config import AppConfig


class TestAppConfig:
    def test_from_env_with_key(self):
        with mock.patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-test-key"},
            clear=True,
        ):
            config = AppConfig.from_env()
            assert config.llm_api_key == "sk-test-key"

    def test_from_env_missing_key_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
                AppConfig.from_env()

    def test_defaults(self):
        with mock.patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True
        ):
            config = AppConfig.from_env()
            assert config.llm_model == "deepseek-v4-flash"
            assert config.llm_base_url == "https://api.deepseek.com"
            assert config.restaurants_path == "restaurants.json"
            assert config.orders_dir == "orders"
            assert config.sessions_dir == "sessions"
            assert config.debug is False

    def test_custom_values(self):
        with mock.patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "sk-test",
                "LLM_MODEL": "deepseek-v4-pro",
                "LLM_BASE_URL": "https://custom.endpoint/v1",
                "RESTAURANTS_PATH": "custom_restaurants.json",
                "ORDERS_DIR": "custom_orders",
                "SESSIONS_DIR": "custom_sessions",
                "DEBUG": "true",
            },
            clear=True,
        ):
            config = AppConfig.from_env()
            assert config.llm_model == "deepseek-v4-pro"
            assert config.llm_base_url == "https://custom.endpoint/v1"
            assert config.restaurants_path == "custom_restaurants.json"
            assert config.orders_dir == "custom_orders"
            assert config.sessions_dir == "custom_sessions"
            assert config.debug is True

    def test_debug_false_variants(self):
        for val in ("false", "False", "0", "no"):
            with mock.patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "sk-test", "DEBUG": val},
                clear=True,
            ):
                config = AppConfig.from_env()
                assert config.debug is False

    def test_debug_true(self):
        with mock.patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-test", "DEBUG": "true"},
            clear=True,
        ):
            config = AppConfig.from_env()
            assert config.debug is True

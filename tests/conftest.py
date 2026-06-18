"""Pytest configuration — shared fixtures and collection control."""

# test_integration.py is a standalone script, not a pytest module.
# It runs via: python tests/test_integration.py
collect_ignore = ["test_integration.py"]

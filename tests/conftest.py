"""Shared pytest fixtures. All fixtures use made-up data; no test touches a real environment."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dataverse_views_mcp.config import ENV_PREFIX, Settings, load_settings

FIXTURES = Path(__file__).parent / "fixtures"

BASE_URL = "https://org.example.test"
VIEW_ID = "11111111-1111-1111-1111-111111111111"
OTHER_VIEW_ID = "22222222-2222-2222-2222-222222222222"
RECORD_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

BASE_SETTINGS: dict[str, Any] = {
    "url": BASE_URL,
    "tenant_id": "99999999-9999-9999-9999-999999999999",
    "client_id": "88888888-8888-8888-8888-888888888888",
    "client_secret": "made-up-secret-value",
    "approved_view_ids": VIEW_ID,
}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No test may see real DATAVERSE_ variables or a developer's .env file."""
    for name in list(os.environ):
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        return load_settings(**{**BASE_SETTINGS, **overrides})

    return factory


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

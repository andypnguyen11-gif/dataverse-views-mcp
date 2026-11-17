"""The PRD acceptance test against a real Dataverse environment.

Skipped unless DATAVERSE_INTEGRATION=1 and a test view are configured, so the default
test run never touches real data. To run it:

    DATAVERSE_INTEGRATION=1 uv run pytest tests/integration -m integration

It reads the normal DATAVERSE_* settings (from the environment or the project's .env) plus:
    DATAVERSE_TEST_VIEW_ID          an approved view
    DATAVERSE_TEST_FILTERS          optional JSON list of filters (for example, one office)
    DATAVERSE_TEST_EXPECTED_COUNT   optional count shown in the app for the same view and filter
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from mcp import Client

from dataverse_views_mcp.acceptance import run_consistency_check
from dataverse_views_mcp.audit import AuditLog
from dataverse_views_mcp.config import ENV_PREFIX, load_settings
from dataverse_views_mcp.server import build_server
from dataverse_views_mcp.services import build_services

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Captured at import, before the shared fixture clears DATAVERSE_* for unit tests.
REAL_ENVIRONMENT = {k: v for k, v in os.environ.items() if k.startswith(ENV_PREFIX)}
ENABLED = REAL_ENVIRONMENT.get("DATAVERSE_INTEGRATION") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(not ENABLED, reason="set DATAVERSE_INTEGRATION=1 to run against Dataverse"),
]


@pytest.fixture
def real_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for name, value in REAL_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    if not REAL_ENVIRONMENT.get("DATAVERSE_TEST_VIEW_ID"):
        pytest.skip("DATAVERSE_TEST_VIEW_ID is not set")
    return REAL_ENVIRONMENT


async def test_same_count_ten_times_matches_the_app(real_environment: dict[str, str]) -> None:
    settings = load_settings(_env_file=str(PROJECT_ROOT / ".env"))
    services = build_services(settings, audit=AuditLog(settings, stream=io.StringIO()))
    expected = real_environment.get("DATAVERSE_TEST_EXPECTED_COUNT")
    filters = json.loads(real_environment.get("DATAVERSE_TEST_FILTERS", "[]"))
    try:
        report = await run_consistency_check(
            services,
            real_environment["DATAVERSE_TEST_VIEW_ID"],
            filters,
            runs=10,
            expected=int(expected) if expected else None,
        )
    finally:
        await services.client.aclose()

    # Assert on booleans only, so a failure message cannot print real figures.
    assert report.consistent, "the 10 runs did not all return the same count"
    assert report.matches_expected is not False, "the count differs from the expected value"


async def test_count_view_over_the_mcp_protocol(real_environment: dict[str, str]) -> None:
    settings = load_settings(_env_file=str(PROJECT_ROOT / ".env"))
    services = build_services(settings, audit=AuditLog(settings, stream=io.StringIO()))
    arguments = {
        "view_id": real_environment["DATAVERSE_TEST_VIEW_ID"],
        "filters": json.loads(real_environment.get("DATAVERSE_TEST_FILTERS", "[]")),
    }

    async with Client(build_server(services)) as client:
        results = [await client.call_tool("count_view", arguments) for _ in range(10)]

    assert not any(result.is_error for result in results)
    assert len({result.structured_content["count"] for result in results}) == 1  # type: ignore[index]

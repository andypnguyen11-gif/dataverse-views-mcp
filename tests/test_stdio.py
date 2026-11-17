"""Start the real server as a subprocess and talk to it over stdio, as a local client would."""

from __future__ import annotations

import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from tests.conftest import BASE_SETTINGS

pytestmark = pytest.mark.anyio


async def test_server_starts_and_speaks_mcp_over_stdio() -> None:
    env = {f"DATAVERSE_{key.upper()}": value for key, value in BASE_SETTINGS.items()}
    env["PATH"] = os.environ.get("PATH", "")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "dataverse_views_mcp.server"], env=env
    )

    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        # A refusal that needs no network: the view is not on the approved list.
        result = await client.call_tool(
            "describe_view", {"view_id": "22222222-2222-2222-2222-222222222222"}
        )

    assert "list_views" in {tool.name for tool in tools}
    assert result.is_error is True
    assert "not on the approved list" in result.content[0].text  # type: ignore[union-attr]

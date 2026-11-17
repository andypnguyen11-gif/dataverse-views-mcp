"""Confirm the configured identity can reach Dataverse. Makes one read-only call (WhoAmI).

Usage: uv run python scripts/smoke_test.py
Prints only "OK" or a plain failure message. It prints no IDs and no data.
"""

from __future__ import annotations

import sys

import anyio

from dataverse_views_mcp.auth import MsalTokenProvider
from dataverse_views_mcp.config import load_settings
from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import DataverseViewsError


async def run() -> int:
    try:
        settings = load_settings()
        client = DataverseClient(settings, MsalTokenProvider(settings))
        try:
            body = await client.get("WhoAmI")
        finally:
            await client.aclose()
    except DataverseViewsError as exc:
        print(f"FAILED ({exc.code}): {exc.message}", file=sys.stderr)
        return 1
    if "UserId" not in body:
        print("FAILED: Dataverse answered, but not with the expected response.", file=sys.stderr)
        return 1
    print("OK: signed in and read from Dataverse.")
    return 0


if __name__ == "__main__":
    sys.exit(anyio.run(run))

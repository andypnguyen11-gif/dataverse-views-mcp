"""MCP server entry point. Registers the tools and runs over stdio.

On stdio, stdout carries only MCP protocol messages; all logging goes to stderr.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from dataverse_views_mcp import __version__
from dataverse_views_mcp.config import HARD_MAX_PAGE_SIZE, load_settings
from dataverse_views_mcp.errors import ConfigurationError, DataverseViewsError
from dataverse_views_mcp.filters import OPERATOR_NAMES
from dataverse_views_mcp.services import Services, build_services
from dataverse_views_mcp.tools import count_view as count_view_tool
from dataverse_views_mcp.tools import describe_view as describe_view_tool
from dataverse_views_mcp.tools import find_record as find_record_tool
from dataverse_views_mcp.tools import list_views as list_views_tool
from dataverse_views_mcp.tools import run_view as run_view_tool

logger = logging.getLogger("dataverse_views_mcp")

INSTRUCTIONS = (
    "Read-only access to approved Dataverse views. To answer a question: find the view with "
    "list_views, check it with describe_view, then use count_view for 'how many' questions "
    "or run_view to read rows. Resolve names such as an office to a record ID with "
    "find_record before filtering. Always tell the user which view and filters an answer "
    "is based on. If no approved view fits the question, say so; do not estimate."
)

FILTERS_HELP = (
    "Optional extra filters, all of which must match. Each filter is an object with "
    "'column' (a filterable column from describe_view), 'operator' (one of: "
    + OPERATOR_NAMES
    + "), 'value' (text, number, true/false, or a list for 'in'; omit for null and "
    "not_null), and optionally 'table' (the name of a joined table from describe_view). "
    "To filter on a related record such as an office, use its record ID from find_record. "
    'Example: [{"column": "officeid", "operator": "eq", "value": "<record ID>"}]'
)

# Every tool only reads, repeats safely, and works within one Dataverse environment.
# Annotations are hints for clients; the read-only guarantee is enforced in the client code.
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


async def run_tool(name: str, call: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """Run one tool call, turning expected failures into short, plain messages.

    Anything unexpected is left to the MCP SDK, which logs it to stderr and tells the
    client only that the tool failed, so no internal detail reaches the model.
    """
    try:
        return await call()
    except DataverseViewsError as exc:
        logger.info("Tool %s refused or failed: %s", name, exc.code)
        raise ToolError(exc.message) from None


def build_server(services: Services) -> MCPServer:
    """Create the MCP server with every tool registered."""

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await services.client.aclose()

    server = MCPServer(
        name="dataverse-views",
        title="Dataverse Views (read-only)",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
    )

    @server.tool(
        name="list_views",
        title="List approved views",
        description=list_views_tool.DESCRIPTION,
        annotations=READ_ONLY,
    )
    async def list_views(
        table: Annotated[
            str, Field(description="Logical name of the Dataverse table, for example 'account'.")
        ],
    ) -> dict[str, Any]:
        return await run_tool("list_views", lambda: list_views_tool.list_views(services, table))

    @server.tool(
        name="describe_view",
        title="Describe a view",
        description=describe_view_tool.DESCRIPTION,
        annotations=READ_ONLY,
    )
    async def describe_view(
        view_id: Annotated[str, Field(description="ID of an approved view, from list_views.")],
    ) -> dict[str, Any]:
        return await run_tool(
            "describe_view", lambda: describe_view_tool.describe_view(services, view_id)
        )

    @server.tool(
        name="find_record",
        title="Find a record by name",
        description=find_record_tool.DESCRIPTION,
        annotations=READ_ONLY,
    )
    async def find_record(
        table: Annotated[
            str, Field(description="Logical name of the table to search, for example 'account'.")
        ],
        search_text: Annotated[
            str, Field(description="The name, or part of the name, to look for.")
        ],
    ) -> dict[str, Any]:
        return await run_tool(
            "find_record", lambda: find_record_tool.find_record(services, table, search_text)
        )

    @server.tool(
        name="count_view",
        title="Count the rows in a view",
        description=count_view_tool.DESCRIPTION,
        annotations=READ_ONLY,
    )
    async def count_view(
        view_id: Annotated[str, Field(description="ID of an approved view, from list_views.")],
        filters: Annotated[
            list[dict[str, Any]], Field(default_factory=list, description=FILTERS_HELP)
        ],
    ) -> dict[str, Any]:
        return await run_tool(
            "count_view", lambda: count_view_tool.count_view(services, view_id, filters)
        )

    @server.tool(
        name="run_view",
        title="Read the rows in a view",
        description=run_view_tool.DESCRIPTION,
        annotations=READ_ONLY,
    )
    async def run_view(
        view_id: Annotated[str, Field(description="ID of an approved view, from list_views.")],
        filters: Annotated[
            list[dict[str, Any]], Field(default_factory=list, description=FILTERS_HELP)
        ],
        page_marker: Annotated[
            str,
            Field(
                description="Leave empty for the first page. For later pages, pass the "
                "next_page_marker from the previous result, with the same view and filters."
            ),
        ] = "",
        page_size: Annotated[
            int,
            Field(
                ge=0,
                le=HARD_MAX_PAGE_SIZE,
                description="Rows per page. Leave at 0 for the server's default, which is "
                "also the most it will return.",
            ),
        ] = 0,
    ) -> dict[str, Any]:
        return await run_tool(
            "run_view",
            lambda: run_view_tool.run_view(services, view_id, filters, page_marker, page_size),
        )

    return server


def configure_logging(level: str) -> None:
    """Send all logs to stderr. Stdout is reserved for the MCP protocol."""
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    # These libraries can log request addresses at INFO; keep them quiet.
    for noisy in ("httpx", "httpcore", "msal"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    """Start the server over stdio. Exits with a plain message if settings are wrong."""
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        sys.stderr.write(f"dataverse-views-mcp cannot start. {exc.message}\n")
        raise SystemExit(2) from None
    configure_logging(settings.log_level)
    if settings.allow_all_system_views:
        logger.warning(
            "DATAVERSE_ALLOW_ALL_SYSTEM_VIEWS is on: every active system view is available. "
            "Use this for local testing only."
        )
    elif not settings.approved_view_ids:
        logger.warning("No approved views are configured, so no view can be used.")
    build_server(build_services(settings)).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()

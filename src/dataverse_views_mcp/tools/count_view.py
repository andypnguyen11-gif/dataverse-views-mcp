from __future__ import annotations

from typing import Any

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.errors import UpstreamError
from dataverse_views_mcp.services import Services
from dataverse_views_mcp.tools.common import view_reference
from dataverse_views_mcp.view_query import load_runnable_view

DESCRIPTION = (
    "Counts the records in one approved view, with optional extra filters, and returns a "
    "single number computed by Dataverse. Use this for every 'how many' question instead of "
    "reading rows and counting them. This tool only reads."
)


async def count_view(services: Services, view_id: str, filters: Any = None) -> dict[str, Any]:
    runnable = await load_runnable_view(
        services.views, services.metadata, services.settings, view_id, filters
    )
    query = fetchxml.to_count(runnable.filtered_query(), runnable.table.primary_id)
    page = await services.client.fetch(runnable.table.entity_set_name, fetchxml.serialize(query))

    # An aggregate query returns one row holding the number, or no row when nothing matches.
    value = page.rows[0].get(fetchxml.COUNT_ALIAS) if page.rows else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UpstreamError("Dataverse returned a count in an unexpected format.")
    return {
        "count": value,
        "view": view_reference(runnable.view.summary),
        "filters_applied": runnable.filters_applied(),
        "what_was_counted": (
            f"Distinct '{runnable.description.table}' records in the view, "
            "after the view's built-in filters and the filters listed here."
        ),
        "computed_by": "Dataverse",
    }

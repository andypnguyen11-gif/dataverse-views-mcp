from __future__ import annotations

from typing import Any

from dataverse_views_mcp.results import clean_text
from dataverse_views_mcp.services import Services

DESCRIPTION = (
    "Lists the approved Dataverse views for one table, with each view's ID, name, and "
    "description. Use this first to find the view that answers a question; if no listed "
    "view fits, say so instead of guessing. This tool only reads."
)


async def list_views(services: Services, table: str) -> dict[str, Any]:
    views = await services.views.list_views(table)
    return {
        "table": table.strip().lower(),
        "view_count": len(views),
        "views": [
            {
                "id": view.id,
                "name": clean_text(view.name, 200),
                "description": clean_text(view.description),
                "last_modified": view.modified_on,
                "shown_in_app": view.is_active,
            }
            for view in views
        ],
        "note": "Only views approved by the data owner are listed.",
    }

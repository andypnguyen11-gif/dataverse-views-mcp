"""Pieces shared by the tools."""

from __future__ import annotations

from typing import Any

from dataverse_views_mcp.results import clean_text
from dataverse_views_mcp.views import ViewSummary


def view_reference(view: ViewSummary) -> dict[str, Any]:
    """How every result names the view it came from, so answers can show their work."""
    return {
        "id": view.id,
        "name": clean_text(view.name, 200),
        "table": view.table,
        "last_modified": view.modified_on,
    }

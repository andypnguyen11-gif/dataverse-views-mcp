from __future__ import annotations

import json
from typing import Any

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.dataverse_client import FORMATTED_VALUE
from dataverse_views_mcp.errors import InvalidInputError
from dataverse_views_mcp.paging import request_binding
from dataverse_views_mcp.results import DATA_NOTICE, clean_text
from dataverse_views_mcp.services import Services
from dataverse_views_mcp.tools.common import view_reference
from dataverse_views_mcp.view_query import load_runnable_view

DESCRIPTION = (
    "Reads the rows of one approved view, one page at a time, with optional extra filters. "
    "Returns only the view's own columns and a marker for the next page. Do not use it to "
    "count; use count_view for 'how many' questions. This tool only reads."
)

# Results pass through connectors with payload limits (450 KB in some government clouds).
MAX_RESULT_BYTES = 400_000
CELL_LENGTH = 500
SHORT_CELL_LENGTH = 100


def _cell(row: dict[str, Any], key: str) -> Any:
    """A column's value, preferring Dataverse's display label over the stored code.

    Dataverse names a lookup on the main table `_<column>_value`, and may encode the dot
    in a joined column's name as `_x002e_`.
    """
    for candidate in (key, f"_{key}_value", key.replace(".", "_x002e_")):
        label = row.get(f"{candidate}@{FORMATTED_VALUE}")
        if label is not None:
            return label
        if candidate in row:
            return row[candidate]
    return None


def _shape(
    rows: list[dict[str, Any]], keys: list[str], primary_id: str, cell_length: int
) -> list[dict[str, Any]]:
    shaped = []
    for row in rows:
        values = {}
        for key in keys:
            value = _cell(row, key)
            values[key] = clean_text(value, cell_length) if isinstance(value, str) else value
        shaped.append({"id": str(row.get(primary_id, "")).lower(), "values": values})
    return shaped


async def run_view(
    services: Services,
    view_id: str,
    filters: Any = None,
    page_marker: str = "",
    page_size: int = 0,
) -> dict[str, Any]:
    maximum = services.settings.max_page_size
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 0:
        raise InvalidInputError("The page size must be a whole number of rows.")
    size = min(page_size, maximum) if page_size else maximum

    runnable = await load_runnable_view(
        services.views, services.metadata, services.settings, view_id, filters
    )
    applied = runnable.filters_applied()
    binding = request_binding(runnable.view.id, applied, size)
    page_number, cookie = (
        services.page_tokens.read(page_marker, binding) if page_marker else (1, None)
    )

    primary_id = runnable.table.primary_id
    # Dataverse's paging cookie is keyed on the main record's ID. With joined tables a
    # record can appear in more than one row, so the cookie is not used and pages are
    # fetched by page number alone, which is slower but returns every row exactly once.
    has_joins = bool(runnable.description.joins)
    if has_joins:
        cookie = None
    query = fetchxml.to_page(runnable.filtered_query(), primary_id, page_number, size, cookie)
    page = await services.client.fetch(runnable.table.entity_set_name, fetchxml.serialize(query))

    # Only the columns the view defines are returned, whatever else Dataverse sends back.
    keys = list(dict.fromkeys(column.result_key for column in runnable.description.columns))
    rows = _shape(page.rows, keys, primary_id, CELL_LENGTH)
    if len(json.dumps(rows, default=str)) > MAX_RESULT_BYTES:
        rows = _shape(page.rows, keys, primary_id, SHORT_CELL_LENGTH)
        if len(json.dumps(rows, default=str)) > MAX_RESULT_BYTES:
            raise InvalidInputError(
                "This page is too large to return. Call run_view again with a smaller page_size."
            )

    next_marker = (
        services.page_tokens.issue(binding, page_number + 1, page.paging_cookie)
        if page.more_records
        else ""
    )
    notes = []
    if fetchxml.has_joined_sort(runnable.root):
        notes.append(
            "The view sorts by a column of a joined table. That sort is not applied here; "
            "the rows are the same, in a different order."
        )
    if has_joins:
        notes.append(
            "This view joins other tables, so one record can appear in more than one row. "
            "Use count_view, which counts each record once, for 'how many' questions."
        )
    return {
        "view": view_reference(runnable.view.summary),
        "filters_applied": applied,
        "notes": notes,
        "page": page_number,
        "page_size": size,
        "row_count": len(rows),
        "columns": keys,
        "rows": rows,
        "has_more": page.more_records,
        "next_page_marker": next_marker,
        "notice": DATA_NOTICE,
    }

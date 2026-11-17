from __future__ import annotations

from typing import Any

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.errors import (
    InvalidInputError,
    PermissionDeniedError,
    UnsupportedViewError,
)
from dataverse_views_mcp.metadata import check_table_name
from dataverse_views_mcp.results import DATA_NOTICE, clean_text
from dataverse_views_mcp.services import Services

DESCRIPTION = (
    "Finds records in one table by name, such as an office, and returns each match's ID and "
    "name. Use it to turn a name the user typed into a record ID before filtering a view. "
    "If several records match, ask the user which one they mean. This tool only reads."
)

MAX_SEARCH_LENGTH = 100


def _check_search_text(text: str) -> str:
    value = text.strip() if isinstance(text, str) else ""
    if not value or len(value) > MAX_SEARCH_LENGTH:
        raise InvalidInputError(f"The search text must be 1 to {MAX_SEARCH_LENGTH} characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InvalidInputError("The search text contains control characters.")
    return value


async def find_record(services: Services, table: str, search_text: str) -> dict[str, Any]:
    name = check_table_name(table)
    text = _check_search_text(search_text)
    if name not in services.settings.searchable_tables:
        raise PermissionDeniedError(
            f"The table '{name}' is not enabled for record search. An administrator can "
            "enable it with the DATAVERSE_SEARCHABLE_TABLES setting."
        )
    metadata = await services.metadata.get_table(name)
    if metadata.primary_name is None:
        raise UnsupportedViewError(f"The table '{name}' has no name column to search.")
    limit = services.settings.find_record_limit

    async def search(exact: bool) -> list[dict[str, Any]]:
        query = fetchxml.build_name_search(
            name,
            metadata.primary_id,
            metadata.primary_name or "",
            text,
            exact=exact,
            limit=limit + 1,
        )
        page = await services.client.fetch(metadata.entity_set_name, fetchxml.serialize(query))
        return page.rows

    # An exact name that matches one record is unambiguous, so return only that record.
    rows = await search(exact=True)
    exact_match = len(rows) == 1
    if not rows:
        rows = await search(exact=False)

    more = len(rows) > limit
    matches = [
        {
            "id": str(row.get(metadata.primary_id, "")).lower(),
            "name": clean_text(str(row.get(metadata.primary_name) or ""), 200),
        }
        for row in rows[:limit]
    ]
    if not matches:
        guidance = "No record matched. Ask the user to check the name."
    elif len(matches) == 1 and not more:
        guidance = "One record matched. Use its ID as the filter value."
    else:
        guidance = "Several records matched. Ask the user which one they mean; do not pick one."
    return {
        "table": name,
        "search_text": text,
        "match_count": len(matches),
        "exact_match": exact_match,
        "more_matches_exist": more,
        "matches": matches,
        "guidance": guidance,
        "notice": DATA_NOTICE,
    }

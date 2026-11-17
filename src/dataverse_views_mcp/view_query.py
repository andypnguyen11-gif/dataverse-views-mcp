"""Rules that tie a view's query, table definitions, and settings together.

Decides which columns a caller may filter on, and turns caller-supplied filters into
checked Filter objects ready to add to the view's query.
"""

from __future__ import annotations

from typing import Any

# Used as a type reference only; parsing is done in fetchxml.py with defusedxml.
from xml.etree import ElementTree as ET  # nosec B405

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import InvalidFilterError
from dataverse_views_mcp.filters import Filter, check_filter_type, parse_filters, value_kind
from dataverse_views_mcp.metadata import MetadataService

# Scope key (None for the main table, else the join's name) to {column: Dataverse type}.
FilterableColumns = dict[str | None, dict[str, str]]


async def filterable_columns(
    root: ET.Element, metadata: MetadataService, settings: Settings
) -> FilterableColumns:
    """The columns a caller may filter on, per table in the view.

    A column is filterable when the view already shows it or filters on it, or when the
    administrator has listed it in DATAVERSE_EXTRA_FILTER_COLUMNS. This keeps filters from
    being used to learn about columns the approved view does not expose.
    """
    result: FilterableColumns = {}
    for key, scope in fetchxml.scopes(root).items():
        if scope.ambiguous:
            continue
        table = await metadata.get_table(scope.table)
        extras = {
            entry.partition(".")[2]
            for entry in settings.extra_filter_columns
            if entry.partition(".")[0] == scope.table
        }
        names = set(table.column_types) if scope.all_columns else scope.columns | extras
        result[key] = {
            name: table.column_types[name]
            for name in sorted(names)
            if name in table.column_types and value_kind(table.column_types[name]) is not None
        }
    return result


async def prepare_filters(
    raw: Any, root: ET.Element, metadata: MetadataService, settings: Settings
) -> list[Filter]:
    """Check caller-supplied filters in full: shape, allowed column, and value type."""
    filters = parse_filters(raw)
    if not filters:
        return []
    allowed = await filterable_columns(root, metadata, settings)
    for flt in filters:
        if flt.table not in allowed:
            raise InvalidFilterError(
                f"The view has no joined table named '{flt.table}' that can be filtered. "
                "Use describe_view to see the filterable columns."
            )
        column_type = allowed[flt.table].get(flt.column)
        if column_type is None:
            raise InvalidFilterError(
                f"The column '{flt.column}' cannot be filtered in this view. "
                "Use describe_view to see the filterable columns."
            )
        check_filter_type(flt, column_type)
    return filters

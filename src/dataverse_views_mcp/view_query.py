"""Rules that tie a view's query, table definitions, and settings together.

Decides which columns a caller may filter on, and turns caller-supplied filters into
checked Filter objects ready to add to the view's query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Used as a type reference only; parsing is done in fetchxml.py with defusedxml.
from xml.etree import ElementTree as ET  # nosec B405

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import InvalidFilterError, UserRelativeViewError
from dataverse_views_mcp.filters import Filter, check_filter_type, parse_filters, value_kind
from dataverse_views_mcp.metadata import MetadataService, TableMetadata
from dataverse_views_mcp.views import View, ViewService

# Scope key (None for the main table, else the join's name) to {column: Dataverse type}.
FilterableColumns = dict[str | None, dict[str, str]]


async def filterable_columns(
    root: ET.Element, metadata: MetadataService, settings: Settings
) -> FilterableColumns:
    """The columns a caller may filter on, per table in the view.

    A column is filterable when the view displays it, or when the administrator has
    listed it in DATAVERSE_EXTRA_FILTER_COLUMNS. This keeps filters from being used to
    learn about columns the approved view does not show.
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
        names = scope.columns | extras
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


@dataclass(frozen=True)
class RunnableView:
    """An approved view, checked and ready to be counted or read."""

    view: View
    root: ET.Element
    description: fetchxml.ViewDescription
    table: TableMetadata
    filters: list[Filter]

    def filtered_query(self) -> ET.Element:
        return fetchxml.apply_filters(self.root, self.filters)

    def filters_applied(self) -> list[dict[str, Any]]:
        return [flt.as_dict() for flt in self.filters]


async def load_runnable_view(
    views: ViewService,
    metadata: MetadataService,
    settings: Settings,
    view_id: str,
    raw_filters: Any,
) -> RunnableView:
    """Fetch an approved view and check everything needed before running it."""
    view = await views.get_view(view_id)
    root = fetchxml.parse(view.fetchxml)
    description = fetchxml.describe(root)
    if description.depends_on_caller:
        raise UserRelativeViewError(
            "This view's results depend on who is asking (for example, a 'my records' view). "
            "The server signs in as a service, not as the person asking, so it cannot count "
            "or read this view. Choose a view that does not depend on the current user."
        )
    table = await metadata.get_table(description.table)
    filters = await prepare_filters(raw_filters, root, metadata, settings)
    return RunnableView(view=view, root=root, description=description, table=table, filters=filters)

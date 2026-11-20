from __future__ import annotations

from typing import Any

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.filters import EXPECTED_VALUE, OPERATOR_NAMES, value_kind
from dataverse_views_mcp.metadata import CHOICE_TYPES, ColumnChoices
from dataverse_views_mcp.results import clean_text
from dataverse_views_mcp.services import Services
from dataverse_views_mcp.tools.common import view_reference
from dataverse_views_mcp.view_query import filterable_columns

DESCRIPTION = (
    "Explains one approved view in plain terms: its columns, built-in filters, sort order, "
    "joined tables, and which columns can take an extra filter. For a filterable choice "
    "column it lists each choice's label and the value to filter on. Use it to choose a "
    "view and to tell the user what a count or list is based on. This tool only reads."
)

USER_RELATIVE_REASON = (
    "This view's results depend on who is asking (for example, a 'my records' view). "
    "The server signs in as a service, not as the person asking, so the view cannot be "
    "counted or read here."
)


async def describe_view(services: Services, view_id: str) -> dict[str, Any]:
    view = await services.views.get_view(view_id)
    root = fetchxml.parse(view.fetchxml)
    description = fetchxml.describe(root)
    filterable = await filterable_columns(root, services.metadata, services.settings)
    scopes = fetchxml.scopes(root)
    # Choice labels are read only for tables with a filterable choice column, and shown
    # only on those columns, so nothing outside the view is revealed.
    choices: dict[str | None, dict[str, ColumnChoices]] = {
        key: await services.metadata.get_choices(scopes[key].table)
        for key, columns in filterable.items()
        if CHOICE_TYPES & set(columns.values())
    }

    result: dict[str, Any] = {
        "view": view_reference(view.summary),
        "description": clean_text(view.summary.description),
        "columns": [
            {"column": column.name, "table": column.table, **_joined(column.join)}
            for column in description.columns
        ],
        "built_in_filters": [clean_text(text, 500) for text in description.filters],
        "sort_order": [
            {
                "column": sort.column,
                "direction": "descending" if sort.descending else "ascending",
                **_joined(sort.join),
            }
            for sort in description.sort
        ],
        "joined_tables": [
            {
                "name": join.name,
                "table": join.table,
                "join": join.kind,
                "on": f"{join.table}.{join.from_column} = {join.parent or description.table}"
                f".{join.to_column}",
            }
            for join in description.joins
        ],
        "filterable_columns": [
            {
                "column": name,
                **_joined(key),
                "value": EXPECTED_VALUE[value_kind(column_type) or "text"],
                **_choices(choices.get(key, {}).get(name)),
            }
            for key, columns in filterable.items()
            for name, column_type in columns.items()
        ],
        "filter_operators": OPERATOR_NAMES,
        "depends_on_who_is_asking": description.depends_on_caller,
        "can_be_counted_and_read": not description.depends_on_caller,
    }
    if description.depends_on_caller:
        result["reason"] = USER_RELATIVE_REASON
    return result


def _joined(join: str | None) -> dict[str, str]:
    return {} if join is None else {"joined_table": join}


def _choices(found: ColumnChoices | None) -> dict[str, Any]:
    if found is None:
        return {}
    listed: dict[str, Any] = {
        "choices": [{"value": choice.value, "label": choice.label} for choice in found.options]
    }
    if found.truncated:
        listed["more_choices_not_shown"] = True
    return listed

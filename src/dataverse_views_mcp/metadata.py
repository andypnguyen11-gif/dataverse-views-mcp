"""Table definitions read from Dataverse: the names and column types the server needs.

Used to address a table's rows, to find its ID and display-name columns, to check that
a filter's value matches its column's type, and to list the options of choice columns so a
caller can turn a label such as "Closed" into the number a filter needs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dataverse_views_mcp.cache import TTLCache
from dataverse_views_mcp.config import LOGICAL_NAME_RE
from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import InvalidInputError, NotFoundError, UpstreamError
from dataverse_views_mcp.results import clean_text

METADATA_TTL_SECONDS = 900
MAX_CHOICES_PER_COLUMN = 50
MAX_CHOICE_LABEL_LENGTH = 100
MAX_CHOICE_COLUMNS_PER_TABLE = 200

# Column types that carry a fixed list of options, mapped to the options to ask for.
_CHOICE_EXPANSIONS = {
    "Picklist": "OptionSet($select=Options)",
    "State": "OptionSet($select=Options)",
    "Status": "OptionSet($select=Options)",
    "Boolean": "OptionSet($select=TrueOption,FalseOption)",
}
CHOICE_TYPES = frozenset(_CHOICE_EXPANSIONS)


@dataclass(frozen=True)
class TableMetadata:
    logical_name: str
    entity_set_name: str
    primary_id: str
    primary_name: str | None
    column_types: dict[str, str]
    """Column logical name to Dataverse attribute type, such as "Lookup" or "String"."""


@dataclass(frozen=True)
class Choice:
    value: int | bool
    """The value a filter must use: a number, or true/false for a yes/no column."""
    label: str


@dataclass(frozen=True)
class ColumnChoices:
    options: tuple[Choice, ...]
    truncated: bool
    """True when the column has more options than MAX_CHOICES_PER_COLUMN."""


def check_table_name(table: str) -> str:
    """Return the normalized table name, or raise if it is not a plain logical name."""
    name = table.strip().lower() if isinstance(table, str) else ""
    if not LOGICAL_NAME_RE.match(name):
        raise InvalidInputError(
            "The table name is not valid. Use the table's logical name, such as 'account'."
        )
    return name


class MetadataService:
    def __init__(
        self, client: DataverseClient, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._client = client
        self._cache: TTLCache[str, TableMetadata] = TTLCache(METADATA_TTL_SECONDS, 128, clock)
        self._choices: TTLCache[str, dict[str, ColumnChoices]] = TTLCache(
            METADATA_TTL_SECONDS, 128, clock
        )

    async def get_table(self, table: str) -> TableMetadata:
        name = check_table_name(table)
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        try:
            # `name` matched a strict pattern above, so it is safe inside the key literal.
            body = await self._client.get(
                f"EntityDefinitions(LogicalName='{name}')",
                {
                    "$select": "LogicalName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute",
                    "$expand": "Attributes($select=LogicalName,AttributeType)",
                },
            )
        except NotFoundError:
            raise NotFoundError(f"There is no table named '{name}'.") from None
        entity_set = body.get("EntitySetName")
        primary_id = body.get("PrimaryIdAttribute")
        attributes = body.get("Attributes")
        if not (
            isinstance(entity_set, str)
            and isinstance(primary_id, str)
            and isinstance(attributes, list)
        ):
            raise UpstreamError("Dataverse returned a table definition in an unexpected format.")
        column_types = {
            str(item["LogicalName"]): str(item.get("AttributeType") or "")
            for item in attributes
            if isinstance(item, dict) and item.get("LogicalName")
        }
        primary_name = body.get("PrimaryNameAttribute")
        metadata = TableMetadata(
            logical_name=name,
            entity_set_name=entity_set,
            primary_id=primary_id,
            primary_name=primary_name if isinstance(primary_name, str) and primary_name else None,
            column_types=column_types,
        )
        self._cache.put(name, metadata)
        return metadata

    async def get_choices(self, table: str) -> dict[str, ColumnChoices]:
        """The options of a table's choice, state, status, and yes/no columns, by column.

        Makes at most one read per kind of choice column the table has, then caches the
        answer. Lists and labels are capped, and labels are cleaned, because they are text
        written by other people.
        """
        metadata = await self.get_table(table)
        name = metadata.logical_name
        cached = self._choices.get(name)
        if cached is not None:
            return cached
        choices: dict[str, ColumnChoices] = {}
        for kind in sorted(CHOICE_TYPES & set(metadata.column_types.values())):
            # `name` matched a strict pattern in get_table, and `kind` is from a fixed list.
            body = await self._client.get(
                f"EntityDefinitions(LogicalName='{name}')/Attributes/"
                f"Microsoft.Dynamics.CRM.{kind}AttributeMetadata",
                {"$select": "LogicalName", "$expand": _CHOICE_EXPANSIONS[kind]},
            )
            rows = body.get("value")
            if not isinstance(rows, list):
                raise UpstreamError("Dataverse returned choice options in an unexpected format.")
            for row in rows:
                if len(choices) >= MAX_CHOICE_COLUMNS_PER_TABLE:
                    break
                if not isinstance(row, dict):
                    continue
                column = row.get("LogicalName")
                option_set = row.get("OptionSet")
                if metadata.column_types.get(str(column)) != kind or not isinstance(
                    option_set, dict
                ):
                    continue
                found = _read_options(kind, option_set)
                if found.options:
                    choices[str(column)] = found
        self._choices.put(name, choices)
        return choices


def _read_options(kind: str, option_set: dict[str, Any]) -> ColumnChoices:
    if kind == "Boolean":
        pairs = [(True, option_set.get("TrueOption")), (False, option_set.get("FalseOption"))]
        options = [
            Choice(value, label) for value, item in pairs if (label := _label(item)) is not None
        ]
        return ColumnChoices(tuple(options), truncated=False)
    raw = option_set.get("Options")
    options = []
    for item in raw if isinstance(raw, list) else []:
        value = item.get("Value") if isinstance(item, dict) else None
        label = _label(item)
        # bool is a subclass of int; a true/false here would not be a choice number.
        if isinstance(value, bool) or not isinstance(value, int) or label is None:
            continue
        options.append(Choice(value, label))
    return ColumnChoices(
        tuple(options[:MAX_CHOICES_PER_COLUMN]), truncated=len(options) > MAX_CHOICES_PER_COLUMN
    )


def _label(option: Any) -> str | None:
    """An option's display label, cleaned and capped, or None if it has none."""
    label = option.get("Label") if isinstance(option, dict) else None
    if not isinstance(label, dict):
        return None
    candidates = [label.get("UserLocalizedLabel")]
    localized = label.get("LocalizedLabels")
    if isinstance(localized, list):
        candidates.extend(localized[:1])
    for candidate in candidates:
        text = candidate.get("Label") if isinstance(candidate, dict) else None
        if isinstance(text, str) and text.strip():
            return clean_text(text.strip(), MAX_CHOICE_LABEL_LENGTH)
    return None

"""Table definitions read from Dataverse: the names and column types the server needs.

Used to address a table's rows, to find its ID and display-name columns, and to check that
a filter's value matches its column's type.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from dataverse_views_mcp.cache import TTLCache
from dataverse_views_mcp.config import LOGICAL_NAME_RE
from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import InvalidInputError, NotFoundError, UpstreamError

METADATA_TTL_SECONDS = 900


@dataclass(frozen=True)
class TableMetadata:
    logical_name: str
    entity_set_name: str
    primary_id: str
    primary_name: str | None
    column_types: dict[str, str]
    """Column logical name to Dataverse attribute type, such as "Lookup" or "String"."""


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

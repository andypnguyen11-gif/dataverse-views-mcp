"""A fake Dataverse client for tests of everything above the HTTP layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dataverse_views_mcp.dataverse_client import FetchPage
from dataverse_views_mcp.errors import NotFoundError


class FakeDataverse:
    """Answers `get` from a path-to-body map and `fetch` from a queue. Records every call."""

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any] | Exception] = {}
        self.fetch_pages: list[FetchPage | Exception] = []
        self.gets: list[tuple[str, dict[str, str]]] = []
        self.fetches: list[tuple[str, str]] = []

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        self.gets.append((path, dict(params or {})))
        response = self.responses.get(path)
        if response is None:
            raise NotFoundError("Dataverse could not find what was requested.")
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch(self, entity_set: str, fetchxml: str) -> FetchPage:
        self.fetches.append((entity_set, fetchxml))
        item = self.fetch_pages.pop(0) if len(self.fetch_pages) > 1 else self.fetch_pages[0]
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def view_row(
    view_id: str,
    name: str = "Active Things",
    *,
    table: str = "thing",
    fetchxml: str | None = "<fetch><entity name='thing'><attribute name='name'/></entity></fetch>",
    querytype: int = 0,
    statecode: int = 0,
    description: str = "Made-up view",
) -> dict[str, Any]:
    return {
        "savedqueryid": view_id,
        "name": name,
        "description": description,
        "returnedtypecode": table,
        "modifiedon": "2026-01-02T03:04:05Z",
        "statecode": statecode,
        "querytype": querytype,
        "fetchxml": fetchxml,
    }


def table_definition(
    table: str = "thing",
    *,
    entity_set: str = "things",
    primary_name: str | None = "name",
    columns: dict[str, str] | None = None,
) -> dict[str, Any]:
    column_types = columns or {
        f"{table}id": "Uniqueidentifier",
        "name": "String",
        "officeid": "Lookup",
        "statecode": "State",
        "amount": "Money",
        "quantity": "Integer",
        "createdon": "DateTime",
        "isurgent": "Boolean",
    }
    return {
        "LogicalName": table,
        "EntitySetName": entity_set,
        "PrimaryIdAttribute": f"{table}id",
        "PrimaryNameAttribute": primary_name,
        "Attributes": [
            {"LogicalName": name, "AttributeType": kind} for name, kind in column_types.items()
        ],
    }

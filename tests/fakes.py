"""A fake Dataverse client for tests of everything above the HTTP layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TextIO, cast

from dataverse_views_mcp.audit import AuditLog
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import DataverseClient, FetchPage
from dataverse_views_mcp.errors import NotFoundError
from dataverse_views_mcp.services import Services, build_services


class FakeDataverse:
    """Answers `get` from a path-to-body map and `fetch` from a queue. Records every call."""

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any] | Exception] = {}
        self.fetch_pages: list[FetchPage | Exception] = []
        self.gets: list[tuple[str, dict[str, str]]] = []
        self.fetches: list[tuple[str, str]] = []
        self.closed = False

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

    async def aclose(self) -> None:
        self.closed = True


def make_services(
    settings: Settings, fake: FakeDataverse, audit_stream: TextIO | None = None
) -> Services:
    audit = AuditLog(settings, stream=audit_stream) if audit_stream is not None else None
    return build_services(settings, client=cast(DataverseClient, fake), audit=audit)


def add_view(fake: FakeDataverse, view_id: str, fetchxml: str, name: str = "Active Things") -> None:
    fake.responses[f"savedqueries({view_id})"] = view_row(view_id, name, fetchxml=fetchxml)


def add_table(fake: FakeDataverse, table: str = "thing", **kwargs: Any) -> None:
    """Register a table definition. Its choice columns start with no options listed."""
    fake.responses[f"EntityDefinitions(LogicalName='{table}')"] = table_definition(table, **kwargs)
    for kind in CHOICE_KINDS:
        fake.responses.setdefault(choices_path(table, kind), {"value": []})


CHOICE_KINDS = ("Picklist", "State", "Status", "Boolean")


def choices_path(table: str, kind: str) -> str:
    return (
        f"EntityDefinitions(LogicalName='{table}')/Attributes/"
        f"Microsoft.Dynamics.CRM.{kind}AttributeMetadata"
    )


def option(value: Any, label: Any) -> dict[str, Any]:
    return {"Value": value, "Label": {"UserLocalizedLabel": {"Label": label}}}


def add_choices(
    fake: FakeDataverse, table: str, kind: str, columns: dict[str, list[tuple[Any, Any]]]
) -> None:
    """Register the options of a table's columns of one kind, as (value, label) pairs.

    A yes/no column takes two pairs: the "true" option first, then the "false" one.
    """
    rows = []
    for column, pairs in columns.items():
        if kind == "Boolean":
            option_set = {"TrueOption": option(*pairs[0]), "FalseOption": option(*pairs[1])}
        else:
            option_set = {"Options": [option(value, label) for value, label in pairs]}
        rows.append({"LogicalName": column, "OptionSet": option_set})
    fake.responses[choices_path(table, kind)] = {"value": rows}


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

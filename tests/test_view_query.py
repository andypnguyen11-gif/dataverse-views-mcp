from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import InvalidFilterError
from dataverse_views_mcp.view_query import filterable_columns, prepare_filters
from tests.conftest import RECORD_ID, read_fixture
from tests.fakes import FakeDataverse, add_table, make_services

pytestmark = pytest.mark.anyio

OFFICE_COLUMNS = {
    "officeid": "Uniqueidentifier",
    "name": "String",
    "regionid": "Lookup",
    "budget": "Money",
}
NOTE_COLUMNS = {"noteid": "Uniqueidentifier", "subject": "String", "category": "Picklist"}


def fake_with_tables() -> FakeDataverse:
    fake = FakeDataverse()
    add_table(fake, "thing")
    add_table(fake, "office", entity_set="offices", columns=OFFICE_COLUMNS)
    add_table(fake, "note", entity_set="notes", columns=NOTE_COLUMNS)
    return fake


async def columns_for(source: str, settings: Settings) -> dict[str | None, dict[str, str]]:
    services = make_services(settings, fake_with_tables())
    return await filterable_columns(fetchxml.parse(source), services.metadata, settings)


async def prepare(raw: Any, settings: Settings, source: str = "view_joined.xml") -> Any:
    services = make_services(settings, fake_with_tables())
    root = fetchxml.parse(read_fixture(source))
    return await prepare_filters(raw, root, services.metadata, settings)


async def test_only_columns_the_view_uses_are_filterable(settings: Settings) -> None:
    allowed = await columns_for(read_fixture("view_joined.xml"), settings)

    assert allowed == {
        None: {
            "createdon": "DateTime",
            "isurgent": "Boolean",
            "name": "String",
            "quantity": "Integer",
            "statecode": "State",
        },
        "off": {"name": "String", "regionid": "Lookup"},
        "nt": {"category": "Picklist", "subject": "String"},
    }
    assert "officeid" not in allowed[None], "a column the view does not use is not filterable"


async def test_administrator_can_allow_extra_columns(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(extra_filter_columns="thing.officeid,office.budget,note.missing")

    allowed = await columns_for(read_fixture("view_joined.xml"), settings)

    assert allowed[None]["officeid"] == "Lookup"
    assert allowed["off"]["budget"] == "Money"
    assert "missing" not in allowed["nt"]


async def test_view_showing_all_columns_allows_every_supported_column(settings: Settings) -> None:
    source = "<fetch><entity name='thing'><all-attributes/></entity></fetch>"

    allowed = await columns_for(source, settings)

    assert set(allowed[None]) == {
        "thingid",
        "name",
        "officeid",
        "statecode",
        "amount",
        "quantity",
        "createdon",
        "isurgent",
    }


async def test_ambiguous_joins_are_not_filterable(settings: Settings) -> None:
    source = (
        "<fetch><entity name='thing'><attribute name='name'/>"
        "<link-entity name='note' from='a' to='b'><attribute name='subject'/></link-entity>"
        "<link-entity name='note' from='a' to='c'/></entity></fetch>"
    )

    assert set(await columns_for(source, settings)) == {None}


async def test_no_filters_needs_no_metadata(settings: Settings) -> None:
    fake = FakeDataverse()
    services = make_services(settings, fake)
    root = fetchxml.parse(read_fixture("view_simple.xml"))

    assert await prepare_filters(None, root, services.metadata, settings) == []
    assert fake.gets == []


async def test_accepts_filters_on_main_and_joined_tables(settings: Settings) -> None:
    filters = await prepare(
        [
            {"column": "quantity", "operator": "ge", "value": 3},
            {"column": "regionid", "operator": "eq", "value": RECORD_ID, "table": "off"},
        ],
        settings,
    )

    assert [(f.table, f.column) for f in filters] == [(None, "quantity"), ("off", "regionid")]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([{"column": "officeid", "operator": "eq", "value": RECORD_ID}], "cannot be filtered"),
        ([{"column": "nosuchcolumn", "operator": "null"}], "cannot be filtered"),
        ([{"column": "budget", "operator": "gt", "value": 1, "table": "off"}], "cannot be filter"),
        ([{"column": "name", "operator": "null", "table": "nope"}], "no joined table named"),
        (
            [{"column": "regionid", "operator": "eq", "value": "North", "table": "off"}],
            "record ID from find_record",
        ),
        ([{"column": "quantity", "operator": "eq", "value": "many"}], "whole number"),
        ("quantity eq 1", "must be a list"),
    ],
)
async def test_rejects_filters_outside_the_view_or_of_the_wrong_type(
    settings: Settings, raw: Any, message: str
) -> None:
    with pytest.raises(InvalidFilterError, match=message):
        await prepare(raw, settings)

from __future__ import annotations

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import NotFoundError, UnsupportedViewError, ViewNotApprovedError
from dataverse_views_mcp.tools.describe_view import describe_view
from tests.conftest import OTHER_VIEW_ID, VIEW_ID, read_fixture
from tests.fakes import FakeDataverse, add_table, add_view, make_services

pytestmark = pytest.mark.anyio


def fake_for(fixture: str) -> FakeDataverse:
    fake = FakeDataverse()
    add_view(fake, VIEW_ID, read_fixture(fixture))
    add_table(fake, "thing")
    add_table(
        fake,
        "office",
        entity_set="offices",
        columns={"name": "String", "regionid": "Lookup", "owningbusinessunit": "Lookup"},
    )
    add_table(
        fake, "note", entity_set="notes", columns={"subject": "String", "category": "Picklist"}
    )
    return fake


async def test_describes_a_simple_view(settings: Settings) -> None:
    result = await describe_view(make_services(settings, fake_for("view_simple.xml")), VIEW_ID)

    assert result["view"] == {
        "id": VIEW_ID,
        "name": "Active Things",
        "table": "thing",
        "last_modified": "2026-01-02T03:04:05Z",
    }
    assert result["description"] == "Made-up view"
    assert result["columns"] == [
        {"column": "name", "table": "thing"},
        {"column": "officeid", "table": "thing"},
        {"column": "statecode", "table": "thing"},
        {"column": "createdon", "table": "thing"},
    ]
    assert result["built_in_filters"] == ["statecode eq 0"]
    assert result["sort_order"] == [{"column": "name", "direction": "ascending"}]
    assert result["joined_tables"] == []
    assert {"column": "officeid", "value": "a record ID from find_record, not a name"} in result[
        "filterable_columns"
    ]
    assert "begins_with" in result["filter_operators"]
    assert result["depends_on_who_is_asking"] is False
    assert result["can_be_counted_and_read"] is True
    assert "reason" not in result


async def test_describes_joined_tables_and_their_filterable_columns(settings: Settings) -> None:
    result = await describe_view(make_services(settings, fake_for("view_joined.xml")), VIEW_ID)

    assert result["joined_tables"] == [
        {
            "name": "off",
            "table": "office",
            "join": "outer",
            "on": "office.officeid = thing.officeid",
        },
        {"name": "nt", "table": "note", "join": "inner", "on": "note.thingid = thing.thingid"},
    ]
    assert {"column": "regionid", "table": "office", "joined_table": "off"} in result["columns"]
    assert {
        "column": "regionid",
        "joined_table": "off",
        "value": "a record ID from find_record, not a name",
    } in result["filterable_columns"]
    assert result["sort_order"] == [{"column": "createdon", "direction": "descending"}]


async def test_flags_a_view_that_depends_on_who_is_asking(settings: Settings) -> None:
    services = make_services(settings, fake_for("view_user_relative.xml"))

    result = await describe_view(services, VIEW_ID)

    assert result["depends_on_who_is_asking"] is True
    assert result["can_be_counted_and_read"] is False
    assert "depend on who is asking" in result["reason"]


async def test_unapproved_missing_and_unreadable_views(settings: Settings) -> None:
    fake = FakeDataverse()
    services = make_services(settings, fake)

    with pytest.raises(ViewNotApprovedError):
        await describe_view(services, OTHER_VIEW_ID)
    with pytest.raises(NotFoundError):
        await describe_view(services, VIEW_ID)

    add_view(fake, VIEW_ID, "<fetch><broken")
    with pytest.raises(UnsupportedViewError):
        await describe_view(make_services(settings, fake), VIEW_ID)

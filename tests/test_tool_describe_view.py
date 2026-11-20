from __future__ import annotations

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import (
    NotFoundError,
    ThrottledError,
    UnsupportedViewError,
    ViewNotApprovedError,
)
from dataverse_views_mcp.metadata import MAX_CHOICES_PER_COLUMN
from dataverse_views_mcp.tools.describe_view import describe_view
from tests.conftest import OTHER_VIEW_ID, VIEW_ID, read_fixture
from tests.fakes import (
    FakeDataverse,
    add_choices,
    add_table,
    add_view,
    choices_path,
    make_services,
)

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


# --- choice labels ---------------------------------------------------------------------------

JOINED_CHOICE_VIEW = """
<fetch>
  <entity name="thing">
    <attribute name="name" />
    <attribute name="isurgent" />
    <link-entity name="note" from="thingid" to="thingid" link-type="inner" alias="nt">
      <attribute name="category" />
    </link-entity>
  </entity>
</fetch>
"""


def filterable(result: dict[str, object], column: str) -> dict[str, object]:
    entries = result["filterable_columns"]
    assert isinstance(entries, list)
    return next(entry for entry in entries if entry["column"] == column)


async def test_lists_the_choices_of_a_filterable_choice_column(settings: Settings) -> None:
    fake = fake_for("view_simple.xml")
    add_choices(fake, "thing", "State", {"statecode": [(0, "Active"), (1, "Inactive")]})

    result = await describe_view(make_services(settings, fake), VIEW_ID)

    assert filterable(result, "statecode") == {
        "column": "statecode",
        "value": "a whole number (for a choice column, the choice's number)",
        "choices": [{"value": 0, "label": "Active"}, {"value": 1, "label": "Inactive"}],
    }
    assert "choices" not in filterable(result, "officeid")
    assert "choices" not in filterable(result, "name")


async def test_lists_choices_for_yes_no_and_joined_table_columns(settings: Settings) -> None:
    fake = fake_for("view_simple.xml")
    add_view(fake, VIEW_ID, JOINED_CHOICE_VIEW)
    add_choices(fake, "thing", "Boolean", {"isurgent": [(1, "Urgent"), (0, "Not urgent")]})
    add_choices(fake, "note", "Picklist", {"category": [(1, "Routine"), (2, "Follow-up")]})

    result = await describe_view(make_services(settings, fake), VIEW_ID)

    assert filterable(result, "isurgent")["choices"] == [
        {"value": True, "label": "Urgent"},
        {"value": False, "label": "Not urgent"},
    ]
    assert filterable(result, "category") == {
        "column": "category",
        "joined_table": "nt",
        "value": "a whole number (for a choice column, the choice's number)",
        "choices": [{"value": 1, "label": "Routine"}, {"value": 2, "label": "Follow-up"}],
    }


async def test_choices_of_columns_the_view_does_not_show_are_not_revealed(
    settings: Settings,
) -> None:
    fake = fake_for("view_joined.xml")
    add_choices(fake, "thing", "State", {"statecode": [(0, "Active")]})
    add_choices(fake, "thing", "Boolean", {"isurgent": [(1, "Urgent"), (0, "Not urgent")]})
    add_choices(fake, "note", "Picklist", {"category": [(1, "Routine")]})

    result = await describe_view(make_services(settings, fake), VIEW_ID)

    assert "choices" not in str(result)
    assert "Routine" not in str(result)
    assert "Urgent" not in str(result)
    assert not any("AttributeMetadata" in path for path, _ in fake.gets)


async def test_flags_a_choice_list_that_was_cut_short(settings: Settings) -> None:
    fake = fake_for("view_simple.xml")
    pairs = [(number, f"Option {number}") for number in range(MAX_CHOICES_PER_COLUMN + 1)]
    add_choices(fake, "thing", "State", {"statecode": pairs})

    entry = filterable(await describe_view(make_services(settings, fake), VIEW_ID), "statecode")

    assert isinstance(entry["choices"], list)
    assert len(entry["choices"]) == MAX_CHOICES_PER_COLUMN
    assert entry["more_choices_not_shown"] is True


async def test_a_choice_column_with_no_readable_options_is_still_described(
    settings: Settings,
) -> None:
    result = await describe_view(make_services(settings, fake_for("view_simple.xml")), VIEW_ID)

    assert filterable(result, "statecode") == {
        "column": "statecode",
        "value": "a whole number (for a choice column, the choice's number)",
    }


async def test_a_dataverse_failure_while_reading_choices_is_reported(settings: Settings) -> None:
    fake = fake_for("view_simple.xml")
    fake.responses[choices_path("thing", "State")] = ThrottledError("Busy.")

    with pytest.raises(ThrottledError):
        await describe_view(make_services(settings, fake), VIEW_ID)

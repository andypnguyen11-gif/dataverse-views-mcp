from __future__ import annotations

from typing import Any, cast

import pytest

from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import (
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    UpstreamError,
)
from dataverse_views_mcp.metadata import (
    MAX_CHOICE_LABEL_LENGTH,
    MAX_CHOICES_PER_COLUMN,
    METADATA_TTL_SECONDS,
    Choice,
    MetadataService,
)
from tests.fakes import (
    Clock,
    FakeDataverse,
    add_choices,
    add_table,
    choices_path,
    option,
    table_definition,
)

pytestmark = pytest.mark.anyio
PATH = "EntityDefinitions(LogicalName='thing')"


def make(fake: FakeDataverse, clock: Clock | None = None) -> MetadataService:
    return MetadataService(cast(DataverseClient, fake), clock or Clock())


async def test_reads_names_and_column_types() -> None:
    fake = FakeDataverse()
    fake.responses[PATH] = table_definition()

    table = await make(fake).get_table(" Thing ")

    assert table.logical_name == "thing"
    assert table.entity_set_name == "things"
    assert table.primary_id == "thingid"
    assert table.primary_name == "name"
    assert table.column_types["officeid"] == "Lookup"
    assert "$expand" in fake.gets[0][1]


async def test_definition_is_cached_then_refreshed() -> None:
    fake, clock = FakeDataverse(), Clock()
    fake.responses[PATH] = table_definition()
    service = make(fake, clock)

    await service.get_table("thing")
    await service.get_table("thing")
    assert len(fake.gets) == 1

    clock.now += METADATA_TTL_SECONDS
    await service.get_table("thing")
    assert len(fake.gets) == 2


async def test_table_without_a_name_column() -> None:
    fake = FakeDataverse()
    fake.responses[PATH] = table_definition(primary_name=None)

    assert (await make(fake).get_table("thing")).primary_name is None


async def test_unknown_table_is_reported_plainly() -> None:
    with pytest.raises(NotFoundError, match="no table named 'thing'"):
        await make(FakeDataverse()).get_table("thing")


@pytest.mark.parametrize("name", ["", "thing')/x", "a b", "Thing-1", "1thing", "x" * 200])
async def test_bad_table_names_never_reach_dataverse(name: str) -> None:
    fake = FakeDataverse()

    with pytest.raises(InvalidInputError):
        await make(fake).get_table(name)

    assert fake.gets == []


@pytest.mark.parametrize(
    "body", [{}, {"EntitySetName": "things"}, {**table_definition(), "Attributes": "nope"}]
)
async def test_unexpected_definition_shape(body: dict[str, Any]) -> None:
    fake = FakeDataverse()
    fake.responses[PATH] = body

    with pytest.raises(UpstreamError):
        await make(fake).get_table("thing")


# --- choice labels ---------------------------------------------------------------------------


def choice_fake() -> FakeDataverse:
    fake = FakeDataverse()
    add_table(fake, "thing", columns={"name": "String", "category": "Picklist"})
    return fake


async def test_reads_the_options_of_choice_state_status_and_yes_no_columns() -> None:
    fake = FakeDataverse()
    add_table(
        fake,
        "thing",
        columns={
            "name": "String",
            "category": "Picklist",
            "statecode": "State",
            "statuscode": "Status",
            "isurgent": "Boolean",
        },
    )
    add_choices(fake, "thing", "Picklist", {"category": [(1, "Routine"), (2, "Follow-up")]})
    add_choices(fake, "thing", "State", {"statecode": [(0, "Active"), (1, "Inactive")]})
    add_choices(fake, "thing", "Status", {"statuscode": [(1, "Open")]})
    add_choices(fake, "thing", "Boolean", {"isurgent": [(1, "Urgent"), (0, "Not urgent")]})

    choices = await make(fake).get_choices(" Thing ")

    assert choices["category"].options == (Choice(1, "Routine"), Choice(2, "Follow-up"))
    assert choices["category"].truncated is False
    assert choices["statecode"].options == (Choice(0, "Active"), Choice(1, "Inactive"))
    assert choices["statuscode"].options == (Choice(1, "Open"),)
    assert choices["isurgent"].options == (Choice(True, "Urgent"), Choice(False, "Not urgent"))
    assert "name" not in choices


async def test_asks_only_for_the_kinds_of_choice_column_the_table_has() -> None:
    fake = choice_fake()

    await make(fake).get_choices("thing")

    assert [path for path, _ in fake.gets[1:]] == [choices_path("thing", "Picklist")]
    assert fake.gets[1][1] == {"$select": "LogicalName", "$expand": "OptionSet($select=Options)"}


async def test_a_table_without_choice_columns_needs_no_extra_request() -> None:
    fake = FakeDataverse()
    add_table(fake, "thing", columns={"name": "String"})

    assert await make(fake).get_choices("thing") == {}
    assert len(fake.gets) == 1


async def test_choices_are_cached_then_refreshed() -> None:
    fake, clock = choice_fake(), Clock()
    service = make(fake, clock)

    await service.get_choices("thing")
    await service.get_choices("thing")
    assert len(fake.gets) == 2

    clock.now += METADATA_TTL_SECONDS
    await service.get_choices("thing")
    assert len(fake.gets) == 4


async def test_a_long_list_of_options_is_cut_short_and_flagged() -> None:
    fake = choice_fake()
    pairs = [(number, f"Option {number}") for number in range(MAX_CHOICES_PER_COLUMN + 5)]
    add_choices(fake, "thing", "Picklist", {"category": pairs})

    column = (await make(fake).get_choices("thing"))["category"]

    assert len(column.options) == MAX_CHOICES_PER_COLUMN
    assert column.truncated is True


async def test_labels_are_cleaned_and_capped() -> None:
    fake = choice_fake()
    add_choices(
        fake,
        "thing",
        "Picklist",
        {"category": [(1, "Rou\x00tine\x1b[31m"), (2, "x" * 5000)]},
    )

    first, second = (await make(fake).get_choices("thing"))["category"].options

    assert first.label == "Routine[31m"
    assert len(second.label) < MAX_CHOICE_LABEL_LENGTH + 20
    assert second.label.endswith("[truncated]")


async def test_label_falls_back_to_the_first_localized_label() -> None:
    fake = choice_fake()
    fake.responses[choices_path("thing", "Picklist")] = {
        "value": [
            {
                "LogicalName": "category",
                "OptionSet": {
                    "Options": [
                        {
                            "Value": 1,
                            "Label": {
                                "UserLocalizedLabel": None,
                                "LocalizedLabels": [{"Label": "Routine"}],
                            },
                        }
                    ]
                },
            }
        ]
    }

    assert (await make(fake).get_choices("thing"))["category"].options == (Choice(1, "Routine"),)


async def test_malformed_options_and_unknown_columns_are_left_out() -> None:
    fake = choice_fake()
    fake.responses[choices_path("thing", "Picklist")] = {
        "value": [
            "nope",
            {"LogicalName": "not_a_column", "OptionSet": {"Options": [option(1, "Hidden")]}},
            {"LogicalName": "category", "OptionSet": None},
            {
                "LogicalName": "category",
                "OptionSet": {
                    "Options": [
                        option("1; drop", "Text value"),
                        option(True, "Boolean value"),
                        option(3, None),
                        option(4, ""),
                        {"Value": 5},
                        "nope",
                        option(6, "Kept"),
                    ]
                },
            },
        ]
    }

    choices = await make(fake).get_choices("thing")

    assert set(choices) == {"category"}
    assert choices["category"].options == (Choice(6, "Kept"),)


@pytest.mark.parametrize("body", [{}, {"value": "nope"}, {"value": None}])
async def test_unexpected_choices_shape(body: dict[str, Any]) -> None:
    fake = choice_fake()
    fake.responses[choices_path("thing", "Picklist")] = body

    with pytest.raises(UpstreamError):
        await make(fake).get_choices("thing")


async def test_a_failed_choices_request_is_reported_and_not_cached() -> None:
    fake = choice_fake()
    fake.responses[choices_path("thing", "Picklist")] = PermissionDeniedError("Not allowed.")
    service = make(fake)

    with pytest.raises(PermissionDeniedError):
        await service.get_choices("thing")

    add_choices(fake, "thing", "Picklist", {"category": [(1, "Routine")]})
    assert (await service.get_choices("thing"))["category"].options == (Choice(1, "Routine"),)


@pytest.mark.parametrize("name", ["", "thing')/x", "a b", "x" * 200])
async def test_bad_table_names_never_reach_dataverse_for_choices(name: str) -> None:
    fake = FakeDataverse()

    with pytest.raises(InvalidInputError):
        await make(fake).get_choices(name)

    assert fake.gets == []

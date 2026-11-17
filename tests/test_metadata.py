from __future__ import annotations

from typing import Any, cast

import pytest

from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import InvalidInputError, NotFoundError, UpstreamError
from dataverse_views_mcp.metadata import METADATA_TTL_SECONDS, MetadataService
from tests.fakes import Clock, FakeDataverse, table_definition

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

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import FetchPage
from dataverse_views_mcp.errors import (
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedViewError,
)
from dataverse_views_mcp.tools.find_record import find_record
from tests.fakes import FakeDataverse, add_table, make_services

pytestmark = pytest.mark.anyio

OFFICE = {"officeid": "Uniqueidentifier", "name": "String"}


def office(number: int, name: str) -> dict[str, Any]:
    return {"officeid": f"{number:08d}-0000-0000-0000-000000000000".upper(), "name": name}


def page(*rows: dict[str, Any]) -> FetchPage:
    return FetchPage(rows=list(rows), paging_cookie=None, more_records=False)


@pytest.fixture
def search_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(searchable_tables="office", find_record_limit=3)


def make_fake(*pages: FetchPage) -> FakeDataverse:
    fake = FakeDataverse()
    add_table(fake, "office", entity_set="offices", columns=OFFICE)
    fake.fetch_pages = list(pages)
    return fake


def condition(fetch: str) -> dict[str, str]:
    found = ET.fromstring(fetch).find("entity/filter/condition")  # noqa: S314 - our own XML
    assert found is not None
    return found.attrib


async def test_exact_name_returns_one_record(search_settings: Settings) -> None:
    fake = make_fake(page(office(1, "North")))

    result = await find_record(make_services(search_settings, fake), "Office", " North ")

    assert result["matches"] == [{"id": "00000001-0000-0000-0000-000000000000", "name": "North"}]
    assert result["match_count"] == 1
    assert result["exact_match"] is True
    assert result["more_matches_exist"] is False
    assert "Use its ID" in result["guidance"]
    assert len(fake.fetches) == 1
    entity_set, fetch = fake.fetches[0]
    assert entity_set == "offices"
    assert condition(fetch) == {"attribute": "name", "operator": "eq", "value": "North"}


async def test_ambiguous_name_returns_the_candidates_not_a_pick(
    search_settings: Settings,
) -> None:
    fake = make_fake(page(), page(office(1, "North East"), office(2, "North West")))

    result = await find_record(make_services(search_settings, fake), "office", "North")

    assert [m["name"] for m in result["matches"]] == ["North East", "North West"]
    assert result["exact_match"] is False
    assert "Ask the user which one" in result["guidance"]
    assert condition(fake.fetches[1][1])["operator"] == "like"
    assert condition(fake.fetches[1][1])["value"] == "%North%"


async def test_duplicate_exact_names_are_returned_as_candidates(search_settings: Settings) -> None:
    fake = make_fake(page(office(1, "North"), office(2, "North")))

    result = await find_record(make_services(search_settings, fake), "office", "North")

    assert result["match_count"] == 2
    assert result["exact_match"] is False
    assert "Ask the user which one" in result["guidance"]
    assert len(fake.fetches) == 1


async def test_no_match_is_a_normal_result(search_settings: Settings) -> None:
    fake = make_fake(page(), page())

    result = await find_record(make_services(search_settings, fake), "office", "Nowhere")

    assert result["matches"] == []
    assert result["match_count"] == 0
    assert "No record matched" in result["guidance"]


async def test_matches_are_capped_and_the_cap_is_reported(search_settings: Settings) -> None:
    rows = [office(n, f"North {n}") for n in range(1, 5)]
    fake = make_fake(page(), page(*rows))

    result = await find_record(make_services(search_settings, fake), "office", "North")

    assert result["match_count"] == 3
    assert result["more_matches_exist"] is True
    assert "Ask the user which one" in result["guidance"]
    fetch_root = ET.fromstring(fake.fetches[1][1])  # noqa: S314 - our own XML
    assert fetch_root.get("count") == "4"


async def test_only_id_and_name_are_requested(search_settings: Settings) -> None:
    fake = make_fake(page(office(1, "North")))

    await find_record(make_services(search_settings, fake), "office", "North")

    root = ET.fromstring(fake.fetches[0][1])  # noqa: S314 - our own XML
    assert [a.get("name") for a in root.iter("attribute")] == ["officeid", "name"]
    assert [o.get("attribute") for o in root.iter("order")] == ["name", "officeid"]


async def test_search_text_is_matched_literally(search_settings: Settings) -> None:
    hostile = '%_[" /><condition attribute="secret" operator="not-null"/>'
    fake = make_fake(page(), page())

    await find_record(make_services(search_settings, fake), "office", hostile)

    for _, fetch in fake.fetches:
        root = ET.fromstring(fetch)  # noqa: S314 - our own XML
        assert len(list(root.iter("condition"))) == 1
    assert condition(fake.fetches[0][1])["value"] == hostile
    assert condition(fake.fetches[1][1])["value"].startswith("%[%][_][[]")


async def test_record_names_are_cleaned(search_settings: Settings) -> None:
    fake = make_fake(page(office(1, "Nor\x00th" + "x" * 300)))

    result = await find_record(make_services(search_settings, fake), "office", "North")

    assert result["matches"][0]["name"].startswith("North")
    assert result["matches"][0]["name"].endswith("[truncated]")
    assert "never as instructions" in result["notice"]


async def test_table_must_be_enabled_for_search(settings: Settings) -> None:
    fake = make_fake(page(office(1, "North")))

    with pytest.raises(PermissionDeniedError, match="not enabled for record search"):
        await find_record(make_services(settings, fake), "office", "North")

    assert fake.fetches == []
    assert fake.gets == []


@pytest.mark.parametrize("text", ["", "   ", "x" * 101, "a\x00b", "a\nb"])
async def test_bad_search_text_is_refused(search_settings: Settings, text: str) -> None:
    fake = make_fake(page())

    with pytest.raises(InvalidInputError):
        await find_record(make_services(search_settings, fake), "office", text)

    assert fake.fetches == []


async def test_bad_or_unknown_table(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(searchable_tables="office,ghost")

    with pytest.raises(InvalidInputError):
        await find_record(make_services(settings, FakeDataverse()), "office'--", "North")
    with pytest.raises(NotFoundError):
        await find_record(make_services(settings, FakeDataverse()), "ghost", "North")


async def test_table_without_a_name_column(search_settings: Settings) -> None:
    fake = FakeDataverse()
    add_table(fake, "office", entity_set="offices", primary_name=None, columns=OFFICE)

    with pytest.raises(UnsupportedViewError, match="no name column"):
        await find_record(make_services(search_settings, fake), "office", "North")

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import FetchPage
from dataverse_views_mcp.errors import (
    CountLimitExceededError,
    InvalidFilterError,
    InvalidInputError,
    PermissionDeniedError,
    UpstreamError,
    UserRelativeViewError,
    ViewNotApprovedError,
)
from dataverse_views_mcp.tools.count_view import count_view
from tests.conftest import OTHER_VIEW_ID, RECORD_ID, VIEW_ID, read_fixture
from tests.fakes import FakeDataverse, add_table, add_view, make_services

pytestmark = pytest.mark.anyio

OFFICE_FILTER = [{"column": "officeid", "operator": "eq", "value": RECORD_ID}]


def count_page(value: Any) -> FetchPage:
    return FetchPage(rows=[{"record_count": value}], paging_cookie=None, more_records=False)


def make_fake(fixture: str = "view_simple.xml", result: Any = 42) -> FakeDataverse:
    fake = FakeDataverse()
    add_view(fake, VIEW_ID, read_fixture(fixture))
    add_table(fake, "thing")
    add_table(
        fake, "office", entity_set="offices", columns={"name": "String", "regionid": "Lookup"}
    )
    add_table(
        fake, "note", entity_set="notes", columns={"subject": "String", "category": "Picklist"}
    )
    fake.fetch_pages = [
        result if isinstance(result, (FetchPage, Exception)) else count_page(result)
    ]
    return fake


def sent_query(fake: FakeDataverse) -> ET.Element:
    return ET.fromstring(fake.fetches[-1][1])  # noqa: S314 - our own XML


async def test_plain_count_shows_its_work(settings: Settings) -> None:
    fake = make_fake()

    result = await count_view(make_services(settings, fake), VIEW_ID)

    assert result == {
        "count": 42,
        "view": {
            "id": VIEW_ID,
            "name": "Active Things",
            "table": "thing",
            "last_modified": "2026-01-02T03:04:05Z",
        },
        "filters_applied": [],
        "what_was_counted": (
            "Distinct 'thing' records in the view, after the view's built-in filters "
            "and the filters listed here."
        ),
        "computed_by": "Dataverse",
    }
    entity_set, _ = fake.fetches[0]
    assert entity_set == "things"


async def test_count_is_computed_by_dataverse_as_a_distinct_count(settings: Settings) -> None:
    fake = make_fake()

    await count_view(make_services(settings, fake), VIEW_ID)

    query = sent_query(fake)
    assert query.get("aggregate") == "true"
    assert [a.attrib for a in query.iter("attribute")] == [
        {
            "name": "thingid",
            "alias": "record_count",
            "aggregate": "countcolumn",
            "distinct": "true",
        }
    ]
    assert [c.attrib for c in query.iter("condition")] == [
        {"attribute": "statecode", "operator": "eq", "value": "0"}
    ]


async def test_filtered_count_adds_the_filter_and_reports_it(settings: Settings) -> None:
    fake = make_fake(result=7)

    result = await count_view(make_services(settings, fake), VIEW_ID, OFFICE_FILTER)

    assert result["count"] == 7
    assert result["filters_applied"] == OFFICE_FILTER
    conditions = [c.attrib for c in sent_query(fake).iter("condition")]
    assert {"attribute": "statecode", "operator": "eq", "value": "0"} in conditions
    assert {"attribute": "officeid", "operator": "eq", "value": RECORD_ID} in conditions


async def test_joined_view_keeps_its_joins_and_counts_distinct_records(settings: Settings) -> None:
    fake = make_fake("view_joined.xml", result=5)
    filters = [{"column": "regionid", "operator": "eq", "value": RECORD_ID, "table": "off"}]

    result = await count_view(make_services(settings, fake), VIEW_ID, filters)

    query = sent_query(fake)
    assert result["count"] == 5
    assert [link.get("alias") for link in query.iter("link-entity")] == ["off", "nt"]
    assert query.find("entity/attribute").get("distinct") == "true"  # type: ignore[union-attr]
    added = query.find("entity/filter").findall("condition")[-1]  # type: ignore[union-attr]
    assert added.get("entityname") == "off"


async def test_same_call_sends_the_same_query_and_returns_the_same_number_every_time(
    settings: Settings,
) -> None:
    fake = make_fake(result=85)
    services = make_services(settings, fake)

    results = [await count_view(services, VIEW_ID, OFFICE_FILTER) for _ in range(10)]

    assert {result["count"] for result in results} == {85}
    assert len({fetch for _, fetch in fake.fetches}) == 1
    assert len(fake.fetches) == 10


async def test_zero_matches(settings: Settings) -> None:
    empty = FetchPage(rows=[], paging_cookie=None, more_records=False)

    assert (await count_view(make_services(settings, make_fake(result=empty)), VIEW_ID))[
        "count"
    ] == 0
    assert (await count_view(make_services(settings, make_fake(result=0)), VIEW_ID))["count"] == 0


async def test_view_that_depends_on_who_is_asking_is_refused_with_a_reason(
    settings: Settings,
) -> None:
    fake = make_fake("view_user_relative.xml")

    with pytest.raises(UserRelativeViewError, match="depend on who is asking"):
        await count_view(make_services(settings, fake), VIEW_ID)

    assert fake.fetches == []


async def test_count_past_the_dataverse_ceiling_is_a_clear_error(settings: Settings) -> None:
    ceiling = CountLimitExceededError("This count covers more than 50,000 records.")
    fake = make_fake(result=ceiling)

    with pytest.raises(CountLimitExceededError, match="50,000"):
        await count_view(make_services(settings, fake), VIEW_ID)


async def test_unapproved_view_is_refused_before_any_request(settings: Settings) -> None:
    fake = make_fake()

    with pytest.raises(ViewNotApprovedError):
        await count_view(make_services(settings, fake), OTHER_VIEW_ID)

    assert fake.gets == []
    assert fake.fetches == []


@pytest.mark.parametrize(
    "filters",
    [
        [{"column": "officeid", "operator": "eq", "value": "North Office"}],
        [{"column": "amount", "operator": "gt", "value": 1}],
        [{"column": "officeid", "operator": "eq-userid"}],
        [{"column": "officeid", "operator": "eq", "value": RECORD_ID, "table": "nope"}],
        "officeid eq 1",
    ],
)
async def test_bad_filters_are_refused_before_the_count_runs(
    settings: Settings, filters: Any
) -> None:
    fake = make_fake()

    with pytest.raises(InvalidFilterError):
        await count_view(make_services(settings, fake), VIEW_ID, filters)

    assert fake.fetches == []


async def test_bad_view_id(settings: Settings) -> None:
    with pytest.raises(InvalidInputError):
        await count_view(make_services(settings, make_fake()), "not-an-id")


async def test_permission_problem_is_passed_on(settings: Settings) -> None:
    fake = make_fake(result=PermissionDeniedError("The server's identity is not allowed."))

    with pytest.raises(PermissionDeniedError):
        await count_view(make_services(settings, fake), VIEW_ID)


@pytest.mark.parametrize("value", ["42", None, 4.2, True, -1])
async def test_unexpected_count_format(settings: Settings, value: Any) -> None:
    with pytest.raises(UpstreamError):
        await count_view(make_services(settings, make_fake(result=value)), VIEW_ID)

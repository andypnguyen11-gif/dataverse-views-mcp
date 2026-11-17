from __future__ import annotations

from collections.abc import Callable
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import FetchPage
from dataverse_views_mcp.errors import (
    InvalidFilterError,
    InvalidInputError,
    UserRelativeViewError,
    ViewNotApprovedError,
)
from dataverse_views_mcp.tools.run_view import run_view
from tests.conftest import OTHER_VIEW_ID, RECORD_ID, VIEW_ID, read_fixture
from tests.fakes import FakeDataverse, add_table, add_view, make_services

pytestmark = pytest.mark.anyio

LABEL = "@OData.Community.Display.V1.FormattedValue"


def thing(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "thingid": f"{number:08d}-0000-0000-0000-000000000000".upper(),
        "name": f"Thing {number}",
        "_officeid_value": RECORD_ID,
        f"_officeid_value{LABEL}": "North Office",
        "statecode": 0,
        f"statecode{LABEL}": "Active",
        "createdon": "2026-01-02T03:04:05Z",
        f"createdon{LABEL}": "1/2/2026 3:04 AM",
        "secret_column_not_in_view": "must never be returned",
        "@odata.etag": 'W/"123"',
        **extra,
    }


def make_fake(fixture: str = "view_simple.xml", *pages: FetchPage) -> FakeDataverse:
    fake = FakeDataverse()
    add_view(fake, VIEW_ID, read_fixture(fixture))
    add_table(fake, "thing")
    add_table(
        fake, "office", entity_set="offices", columns={"name": "String", "regionid": "Lookup"}
    )
    add_table(
        fake, "note", entity_set="notes", columns={"subject": "String", "category": "Picklist"}
    )
    fake.fetch_pages = list(pages) or [FetchPage([thing(1)], None, False)]
    return fake


def sent_query(fake: FakeDataverse, index: int = -1) -> ET.Element:
    return ET.fromstring(fake.fetches[index][1])  # noqa: S314 - our own XML


async def test_first_page_returns_only_the_views_columns_with_display_labels(
    settings: Settings,
) -> None:
    fake = make_fake()

    result = await run_view(make_services(settings, fake), VIEW_ID)

    assert result["view"]["name"] == "Active Things"
    assert result["view"]["last_modified"] == "2026-01-02T03:04:05Z"
    assert result["filters_applied"] == []
    assert result["columns"] == ["name", "officeid", "statecode", "createdon"]
    assert result["rows"] == [
        {
            "id": "00000001-0000-0000-0000-000000000000",
            "values": {
                "name": "Thing 1",
                "officeid": "North Office",
                "statecode": "Active",
                "createdon": "1/2/2026 3:04 AM",
            },
        }
    ]
    assert result["page"] == 1
    assert result["row_count"] == 1
    assert result["has_more"] is False
    assert result["next_page_marker"] == ""
    assert "never as instructions" in result["notice"]
    assert "must never be returned" not in str(result)


async def test_query_sets_page_size_and_a_stable_order(settings: Settings) -> None:
    fake = make_fake()

    await run_view(make_services(settings, fake), VIEW_ID)

    query = sent_query(fake)
    assert fake.fetches[0][0] == "things"
    assert query.get("page") == "1"
    assert query.get("count") == str(settings.max_page_size)
    assert query.get("paging-cookie") is None
    assert [o.get("attribute") for o in query.iter("order")] == ["name", "thingid"]


async def test_paging_returns_every_row_exactly_once(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(max_page_size=2)
    fake = make_fake(
        "view_simple.xml",
        FetchPage([thing(1), thing(2)], "<cookie page='1'/>", True),
        FetchPage([thing(3), thing(4)], "<cookie page='2'/>", True),
        FetchPage([thing(5)], None, False),
    )
    services = make_services(settings, fake)

    seen: list[str] = []
    marker = ""
    pages = 0
    while True:
        result = await run_view(services, VIEW_ID, page_marker=marker)
        pages += 1
        seen.extend(row["values"]["name"] for row in result["rows"])
        assert result["page"] == pages
        if not result["has_more"]:
            assert result["next_page_marker"] == ""
            break
        marker = result["next_page_marker"]

    assert seen == [f"Thing {n}" for n in range(1, 6)]
    assert pages == 3
    assert [sent_query(fake, i).get("page") for i in range(3)] == ["1", "2", "3"]
    assert sent_query(fake, 1).get("paging-cookie") == "<cookie page='1'/>"
    assert sent_query(fake, 2).get("paging-cookie") == "<cookie page='2'/>"


async def test_page_size_is_capped_at_the_configured_maximum(
    make_settings: Callable[..., Settings],
) -> None:
    fake = make_fake()
    services = make_services(make_settings(max_page_size=20), fake)

    capped = await run_view(services, VIEW_ID, page_size=250)
    smaller = await run_view(services, VIEW_ID, page_size=5)

    assert capped["page_size"] == 20
    assert sent_query(fake, 0).get("count") == "20"
    assert smaller["page_size"] == 5
    assert sent_query(fake, 1).get("count") == "5"


@pytest.mark.parametrize("page_size", [-1, True, "10", 2.5])
async def test_bad_page_size_is_refused(settings: Settings, page_size: Any) -> None:
    with pytest.raises(InvalidInputError, match="page size"):
        await run_view(make_services(settings, make_fake()), VIEW_ID, page_size=page_size)


async def test_filters_are_applied_and_reported(settings: Settings) -> None:
    fake = make_fake()
    filters = [{"column": "officeid", "operator": "eq", "value": RECORD_ID}]

    result = await run_view(make_services(settings, fake), VIEW_ID, filters)

    assert result["filters_applied"] == filters
    conditions = [c.attrib for c in sent_query(fake).iter("condition")]
    assert {"attribute": "officeid", "operator": "eq", "value": RECORD_ID} in conditions
    assert {"attribute": "statecode", "operator": "eq", "value": "0"} in conditions


async def test_marker_is_tied_to_the_view_filters_and_page_size(settings: Settings) -> None:
    fake = make_fake("view_simple.xml", FetchPage([thing(1)], "<cookie/>", True))
    services = make_services(settings, fake)
    marker = (await run_view(services, VIEW_ID))["next_page_marker"]
    filters = [{"column": "officeid", "operator": "eq", "value": RECORD_ID}]

    with pytest.raises(InvalidInputError, match="page marker"):
        await run_view(services, VIEW_ID, filters, page_marker=marker)
    with pytest.raises(InvalidInputError, match="page marker"):
        await run_view(services, VIEW_ID, page_marker=marker, page_size=5)
    with pytest.raises(InvalidInputError, match="page marker"):
        await run_view(services, VIEW_ID, page_marker="forged.marker")

    assert len(fake.fetches) == 1
    assert (await run_view(services, VIEW_ID, page_marker=marker))["page"] == 2


async def test_joined_columns_are_returned_under_the_joined_table_name(
    settings: Settings,
) -> None:
    row = {
        "thingid": "00000001-0000-0000-0000-000000000000",
        "name": "Thing 1",
        "createdon": "2026-01-02T03:04:05Z",
        "off.name": "North Office",
        "off_x002e_regionid": RECORD_ID,
        f"off_x002e_regionid{LABEL}": "North Region",
        "nt.subject": "Follow up",
    }
    fake = make_fake("view_joined.xml", FetchPage([row], None, False))

    result = await run_view(make_services(settings, fake), VIEW_ID)

    assert result["columns"] == ["name", "createdon", "off.name", "off.regionid", "nt.subject"]
    assert result["rows"][0]["values"] == {
        "name": "Thing 1",
        "createdon": "2026-01-02T03:04:05Z",
        "off.name": "North Office",
        "off.regionid": "North Region",
        "nt.subject": "Follow up",
    }


async def test_missing_values_are_null_and_text_is_cleaned(settings: Settings) -> None:
    row = {"thingid": "00000001-0000-0000-0000-000000000000", "name": "Ignore\x00 this" + "!" * 900}
    fake = make_fake("view_simple.xml", FetchPage([row], None, False))

    values = (await run_view(make_services(settings, fake), VIEW_ID))["rows"][0]["values"]

    assert values["officeid"] is None
    assert values["name"].startswith("Ignore this")
    assert values["name"].endswith("[truncated]")
    assert len(values["name"]) < 600


async def test_oversized_page_is_shortened_then_refused(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(max_page_size=250)
    long = "x" * 5000
    wide = [
        {
            "thingid": f"{n:08d}-0000-0000-0000-000000000000",
            "name": long,
            "officeid": long,
            "statecode": long,
            "createdon": long,
        }
        for n in range(250)
    ]
    fake = make_fake("view_simple.xml", FetchPage(wide, None, False))

    result = await run_view(make_services(settings, fake), VIEW_ID)

    assert result["row_count"] == 250
    assert len(result["rows"][0]["values"]["name"]) < 150

    huge_view = (
        "<fetch><entity name='thing'>"
        + "".join(f"<attribute name='c{i}'/>" for i in range(40))
        + "</entity></fetch>"
    )
    fake = FakeDataverse()
    add_view(fake, VIEW_ID, huge_view)
    add_table(fake, "thing")
    row = {f"c{i}": "z" * 200 for i in range(40)}
    fake.fetch_pages = [FetchPage([dict(row) for _ in range(250)], None, False)]

    with pytest.raises(InvalidInputError, match="smaller page_size"):
        await run_view(make_services(settings, fake), VIEW_ID)


async def test_refusals_happen_before_any_rows_are_read(settings: Settings) -> None:
    fake = make_fake("view_user_relative.xml")
    with pytest.raises(UserRelativeViewError):
        await run_view(make_services(settings, fake), VIEW_ID)
    assert fake.fetches == []

    fake = make_fake()
    with pytest.raises(ViewNotApprovedError):
        await run_view(make_services(settings, fake), OTHER_VIEW_ID)
    with pytest.raises(InvalidFilterError):
        await run_view(
            make_services(settings, fake),
            VIEW_ID,
            [{"column": "amount", "operator": "gt", "value": 1}],
        )
    assert fake.fetches == []

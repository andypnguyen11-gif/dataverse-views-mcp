from __future__ import annotations

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import InvalidInputError
from dataverse_views_mcp.tools.list_views import list_views
from tests.conftest import OTHER_VIEW_ID, VIEW_ID
from tests.fakes import FakeDataverse, make_services, view_row

pytestmark = pytest.mark.anyio


async def test_returns_approved_views_with_id_name_and_description(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {
        "value": [
            view_row(VIEW_ID, "Active Things", description="Open things"),
            view_row(OTHER_VIEW_ID, "Not approved"),
        ]
    }

    result = await list_views(make_services(settings, fake), " Thing ")

    assert result == {
        "table": "thing",
        "view_count": 1,
        "views": [
            {
                "id": VIEW_ID,
                "name": "Active Things",
                "description": "Open things",
                "last_modified": "2026-01-02T03:04:05Z",
                "shown_in_app": True,
            }
        ],
        "note": "Only views approved by the data owner are listed.",
    }


async def test_a_table_with_no_approved_views_is_an_empty_result(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {"value": []}

    result = await list_views(make_services(settings, fake), "thing")

    assert result["view_count"] == 0
    assert result["views"] == []


async def test_view_text_is_cleaned(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {
        "value": [view_row(VIEW_ID, "Na\x00me" + "x" * 300, description="D\x1bescription")]
    }

    view = (await list_views(make_services(settings, fake), "thing"))["views"][0]

    assert view["name"].startswith("Name")
    assert view["name"].endswith("[truncated]")
    assert view["description"] == "Description"


async def test_bad_table_name_is_refused(settings: Settings) -> None:
    with pytest.raises(InvalidInputError):
        await list_views(make_services(settings, FakeDataverse()), "thing'; --")

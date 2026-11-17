from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import (
    InvalidInputError,
    NotFoundError,
    UnsupportedViewError,
    UpstreamError,
    ViewNotApprovedError,
)
from dataverse_views_mcp.views import ViewService
from tests.conftest import OTHER_VIEW_ID, VIEW_ID
from tests.fakes import Clock, FakeDataverse, view_row

pytestmark = pytest.mark.anyio
THIRD_VIEW_ID = "33333333-3333-3333-3333-333333333333"


def make(fake: FakeDataverse, settings: Settings, clock: Clock | None = None) -> ViewService:
    return ViewService(cast(DataverseClient, fake), settings, clock or Clock())


async def test_list_returns_only_approved_views(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {
        "value": [view_row(VIEW_ID, "Approved"), view_row(OTHER_VIEW_ID, "Not approved")]
    }

    views = await make(fake, settings).list_views("Thing")

    assert [(v.id, v.name) for v in views] == [(VIEW_ID, "Approved")]
    assert views[0].modified_on == "2026-01-02T03:04:05Z"
    assert views[0].table == "thing"


async def test_list_asks_dataverse_for_public_views_of_the_table_only(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {"value": []}

    assert await make(fake, settings).list_views("thing") == []

    path, params = fake.gets[0]
    assert path == "savedqueries"
    assert params["$filter"] == "returnedtypecode eq 'thing' and querytype eq 0"
    assert "fetchxml" not in params["$select"]


async def test_list_includes_approved_views_the_app_hides(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {"value": [view_row(VIEW_ID, "Hidden", statecode=1)]}

    views = await make(fake, settings).list_views("thing")

    assert [v.name for v in views] == ["Hidden"]
    assert views[0].is_active is False


async def test_allow_all_lists_every_active_view(make_settings: Callable[..., Settings]) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {
        "value": [
            view_row(VIEW_ID, "Approved but inactive", statecode=1),
            view_row(OTHER_VIEW_ID, "Active"),
            view_row(THIRD_VIEW_ID, "Inactive", statecode=1),
        ]
    }

    views = await make(fake, make_settings(allow_all_system_views=True)).list_views("thing")

    assert [v.name for v in views] == ["Approved but inactive", "Active"]


@pytest.mark.parametrize("table", ["thing' or 1 eq 1 or 'a' eq 'a", "", "a/b", "Thing 1"])
async def test_list_rejects_bad_table_names_before_any_request(
    settings: Settings, table: str
) -> None:
    fake = FakeDataverse()

    with pytest.raises(InvalidInputError):
        await make(fake, settings).list_views(table)

    assert fake.gets == []


async def test_list_unexpected_shape(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {"value": "nope"}
    with pytest.raises(UpstreamError):
        await make(fake, settings).list_views("thing")

    fake.responses["savedqueries"] = {"value": [{"name": "no id"}]}
    with pytest.raises(UpstreamError):
        await make(fake, settings).list_views("thing")


async def test_get_returns_the_stored_query(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses[f"savedqueries({VIEW_ID})"] = view_row(VIEW_ID)

    view = await make(fake, settings).get_view(f"{{{VIEW_ID.upper()}}}")

    assert view.id == VIEW_ID
    assert view.name == "Active Things"
    assert view.fetchxml.startswith("<fetch>")


async def test_unapproved_view_is_refused_without_asking_dataverse(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses[f"savedqueries({OTHER_VIEW_ID})"] = view_row(OTHER_VIEW_ID)

    with pytest.raises(ViewNotApprovedError, match="not on the approved list"):
        await make(fake, settings).get_view(OTHER_VIEW_ID)

    assert fake.gets == []


async def test_empty_approved_list_refuses_everything(
    make_settings: Callable[..., Settings],
) -> None:
    fake = FakeDataverse()

    with pytest.raises(ViewNotApprovedError):
        await make(fake, make_settings(approved_view_ids="")).get_view(VIEW_ID)


@pytest.mark.parametrize("view_id", ["", "abc", f"{VIEW_ID})/x", "'; drop", VIEW_ID + "0"])
async def test_get_rejects_ids_that_are_not_guids(settings: Settings, view_id: str) -> None:
    fake = FakeDataverse()

    with pytest.raises(InvalidInputError):
        await make(fake, settings).get_view(view_id)

    assert fake.gets == []


async def test_approved_view_that_no_longer_exists(settings: Settings) -> None:
    with pytest.raises(NotFoundError, match="no longer exists"):
        await make(FakeDataverse(), settings).get_view(VIEW_ID)


@pytest.mark.parametrize("querytype", [1, 2, 4, 64])
async def test_behind_the_scenes_view_types_are_refused(settings: Settings, querytype: int) -> None:
    fake = FakeDataverse()
    fake.responses[f"savedqueries({VIEW_ID})"] = view_row(VIEW_ID, querytype=querytype)

    with pytest.raises(UnsupportedViewError):
        await make(fake, settings).get_view(VIEW_ID)


async def test_view_without_a_stored_query_is_refused(settings: Settings) -> None:
    fake = FakeDataverse()
    fake.responses[f"savedqueries({VIEW_ID})"] = view_row(VIEW_ID, fetchxml=None)

    with pytest.raises(UnsupportedViewError):
        await make(fake, settings).get_view(VIEW_ID)


async def test_allow_all_refuses_inactive_views(make_settings: Callable[..., Settings]) -> None:
    fake = FakeDataverse()
    fake.responses[f"savedqueries({OTHER_VIEW_ID})"] = view_row(OTHER_VIEW_ID, statecode=1)
    service = make(fake, make_settings(allow_all_system_views=True))

    with pytest.raises(ViewNotApprovedError, match="inactive"):
        await service.get_view(OTHER_VIEW_ID)


async def test_view_is_cached_briefly_then_read_again(settings: Settings) -> None:
    fake, clock = FakeDataverse(), Clock()
    fake.responses[f"savedqueries({VIEW_ID})"] = view_row(VIEW_ID, "Before")
    service = make(fake, settings, clock)

    assert (await service.get_view(VIEW_ID)).name == "Before"
    fake.responses[f"savedqueries({VIEW_ID})"] = view_row(VIEW_ID, "After")
    assert (await service.get_view(VIEW_ID)).name == "Before"
    assert len(fake.gets) == 1

    clock.now += settings.view_cache_ttl_seconds
    assert (await service.get_view(VIEW_ID)).name == "After"


async def test_cache_can_be_disabled(make_settings: Callable[..., Settings]) -> None:
    fake = FakeDataverse()
    fake.responses[f"savedqueries({VIEW_ID})"] = view_row(VIEW_ID)
    service = make(fake, make_settings(view_cache_ttl_seconds=0))

    await service.get_view(VIEW_ID)
    await service.get_view(VIEW_ID)

    assert len(fake.gets) == 2

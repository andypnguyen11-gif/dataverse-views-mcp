"""Find system views in Dataverse and limit them to the approved set.

Only views on the approved list (a setting agreed with the data owner) are available.
The approval check happens before any request is made, and the refusal is the same whether
or not the view exists, so the server cannot be used to probe for views.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dataverse_views_mcp.cache import TTLCache
from dataverse_views_mcp.config import GUID_RE, Settings
from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.errors import (
    InvalidInputError,
    NotFoundError,
    UnsupportedViewError,
    UpstreamError,
    ViewNotApprovedError,
)
from dataverse_views_mcp.metadata import check_table_name

# savedquery.querytype 0 is a public view. Quick find (4), lookup (64), advanced find (1),
# and associated (2) views are behind-the-scenes definitions and are never exposed.
PUBLIC_VIEW_QUERY_TYPE = 0
STATE_ACTIVE = 0

_SUMMARY_COLUMNS = "savedqueryid,name,description,returnedtypecode,modifiedon,statecode,querytype"
_DETAIL_COLUMNS = _SUMMARY_COLUMNS + ",fetchxml"


@dataclass(frozen=True)
class ViewSummary:
    id: str
    name: str
    description: str
    table: str
    modified_on: str
    is_active: bool


@dataclass(frozen=True)
class View:
    summary: ViewSummary
    fetchxml: str

    @property
    def id(self) -> str:
        return self.summary.id

    @property
    def name(self) -> str:
        return self.summary.name


def check_view_id(view_id: str) -> str:
    """Return the normalized view ID, or raise if it is not a GUID."""
    value = view_id.strip().strip("{}").lower() if isinstance(view_id, str) else ""
    if not GUID_RE.match(value):
        raise InvalidInputError("The view ID is not valid. Use an ID returned by list_views.")
    return value


def _summary(row: dict[str, Any]) -> ViewSummary:
    view_id = row.get("savedqueryid")
    if not isinstance(view_id, str):
        raise UpstreamError("Dataverse returned a view in an unexpected format.")
    return ViewSummary(
        id=view_id.lower(),
        name=str(row.get("name") or ""),
        description=str(row.get("description") or ""),
        table=str(row.get("returnedtypecode") or ""),
        modified_on=str(row.get("modifiedon") or ""),
        is_active=row.get("statecode") == STATE_ACTIVE,
    )


class ViewService:
    def __init__(
        self,
        client: DataverseClient,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._settings = settings
        self._cache: TTLCache[str, View] = TTLCache(settings.view_cache_ttl_seconds, 256, clock)

    async def list_views(self, table: str) -> list[ViewSummary]:
        """Approved public system views for a table, sorted by name."""
        name = check_table_name(table)
        # `name` matched a strict pattern, so it is safe inside the filter's string literal.
        body = await self._client.get(
            "savedqueries",
            {
                "$select": _SUMMARY_COLUMNS,
                "$filter": (
                    f"returnedtypecode eq '{name}' and querytype eq {PUBLIC_VIEW_QUERY_TYPE}"
                ),
                "$orderby": "name asc",
            },
        )
        rows = body.get("value")
        if not isinstance(rows, list):
            raise UpstreamError("Dataverse returned a list of views in an unexpected format.")
        views = [_summary(row) for row in rows if isinstance(row, dict)]
        return [view for view in views if self._is_available(view)]

    async def get_view(self, view_id: str) -> View:
        """One approved view with its stored query."""
        normalized = check_view_id(view_id)
        if not self._settings.is_view_approved(normalized):
            raise ViewNotApprovedError(
                "This view is not on the approved list, so it cannot be used. "
                "Use list_views to see the approved views."
            )
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached
        try:
            row = await self._client.get(
                f"savedqueries({normalized})", {"$select": _DETAIL_COLUMNS}
            )
        except NotFoundError:
            raise NotFoundError("This view no longer exists in Dataverse.") from None
        summary = _summary(row)
        fetchxml = row.get("fetchxml")
        if row.get("querytype") != PUBLIC_VIEW_QUERY_TYPE:
            raise UnsupportedViewError("This is not a standard system view, so it cannot be used.")
        if not self._is_available(summary):
            raise ViewNotApprovedError(
                "This view is inactive and has not been approved by ID, so it cannot be used."
            )
        if not isinstance(fetchxml, str) or not fetchxml.strip():
            raise UnsupportedViewError("This view has no stored query, so it cannot be used.")
        view = View(summary=summary, fetchxml=fetchxml)
        self._cache.put(normalized, view)
        return view

    def _is_available(self, view: ViewSummary) -> bool:
        """A view named on the approved list is available even if the app hides it or it is
        inactive. Under "allow all", only active views are available."""
        if view.id in self._settings.approved_view_ids:
            return True
        return self._settings.allow_all_system_views and view.is_active

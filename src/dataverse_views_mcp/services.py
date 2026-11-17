"""The objects every tool needs, built once at startup."""

from __future__ import annotations

from dataclasses import dataclass

from dataverse_views_mcp.audit import AuditLog
from dataverse_views_mcp.auth import MsalTokenProvider, TokenProvider
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import DataverseClient
from dataverse_views_mcp.metadata import MetadataService
from dataverse_views_mcp.paging import PageTokens
from dataverse_views_mcp.views import ViewService


@dataclass(frozen=True)
class Services:
    settings: Settings
    client: DataverseClient
    views: ViewService
    metadata: MetadataService
    page_tokens: PageTokens
    audit: AuditLog


def build_services(
    settings: Settings,
    client: DataverseClient | None = None,
    token_provider: TokenProvider | None = None,
    audit: AuditLog | None = None,
) -> Services:
    dataverse = client or DataverseClient(settings, token_provider or MsalTokenProvider(settings))
    return Services(
        settings=settings,
        client=dataverse,
        views=ViewService(dataverse, settings),
        metadata=MetadataService(dataverse),
        page_tokens=PageTokens(),
        audit=audit or AuditLog(settings),
    )

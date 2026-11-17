"""Sign-in for the service identity, using MSAL's client-credentials flow.

MSAL keeps the token in an in-memory cache and returns the cached token until it nears
expiry, so callers ask for a token on every request and no token is stored by this module.
Tokens are never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

import anyio
import msal

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import AuthenticationError, ConfigurationError

logger = logging.getLogger(__name__)


class TokenProvider(Protocol):
    """Anything that can supply a bearer token for Dataverse."""

    async def get_token(self) -> str: ...


def _client_credential(settings: Settings) -> str | dict[str, str]:
    if settings.client_certificate_path is not None:
        try:
            private_key = settings.client_certificate_path.read_text(encoding="utf-8")
        except OSError:
            raise ConfigurationError(
                "DATAVERSE_CLIENT_CERTIFICATE_PATH could not be read"
            ) from None
        return {
            "private_key": private_key,
            "thumbprint": settings.client_certificate_thumbprint or "",
        }
    if settings.client_secret is None:  # pragma: no cover - Settings validation prevents this
        raise ConfigurationError("No credential configured")
    return settings.client_secret.get_secret_value()


class MsalTokenProvider:
    """Gets tokens for the configured Dataverse environment as the service identity."""

    def __init__(
        self,
        settings: Settings,
        app_factory: Callable[..., Any] = msal.ConfidentialClientApplication,
    ) -> None:
        self._settings = settings
        self._app_factory = app_factory
        self._app: Any | None = None

    def _get_app(self) -> Any:
        if self._app is None:
            try:
                self._app = self._app_factory(
                    client_id=self._settings.client_id,
                    client_credential=_client_credential(self._settings),
                    authority=self._settings.authority,
                )
            except ConfigurationError:
                raise
            except Exception as exc:  # noqa: BLE001 - MSAL raises varied types on bad authority
                logger.error("Sign-in client could not be created (%s)", type(exc).__name__)
                raise AuthenticationError(
                    "Could not sign in to Dataverse. Check the sign-in settings."
                ) from None
        return self._app

    def _acquire(self) -> str:
        app = self._get_app()
        try:
            result = app.acquire_token_for_client(scopes=[self._settings.scope])
        except Exception as exc:  # noqa: BLE001 - network and library errors alike
            logger.error("Token request failed (%s)", type(exc).__name__)
            raise AuthenticationError(
                "Could not sign in to Dataverse. The sign-in service could not be reached."
            ) from None
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            # Log only the short error identifiers. The description can hold tenant detail.
            error = result.get("error") if isinstance(result, dict) else None
            codes = result.get("error_codes") if isinstance(result, dict) else None
            logger.error("Token request was refused (error=%s, codes=%s)", error, codes)
            raise AuthenticationError(
                "Could not sign in to Dataverse. Check the server's identity and credentials."
            )
        return str(token)

    async def get_token(self) -> str:
        """Return a valid token. MSAL is blocking, so it runs in a worker thread."""
        return await anyio.to_thread.run_sync(self._acquire)

"""The only module that talks to Dataverse, and it can only read.

Read-only is enforced twice. The public methods only issue GET requests (a long query is
sent as a single GET wrapped in a `$batch` envelope, which Dataverse requires to be a POST).
Independently, a hook on the HTTP client inspects every outgoing request and refuses
anything that is not a GET or a `$batch` envelope holding exactly one GET.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlencode

import anyio
import httpx
from defusedxml import ElementTree as SafeET

from dataverse_views_mcp import __version__
from dataverse_views_mcp.auth import TokenProvider
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.errors import (
    AuthenticationError,
    BadRequestError,
    CountLimitExceededError,
    DataverseViewsError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    ReadOnlyViolationError,
    ThrottledError,
    UnsupportedViewError,
    UpstreamError,
)

logger = logging.getLogger(__name__)

FORMATTED_VALUE = "OData.Community.Display.V1.FormattedValue"
PAGING_COOKIE = "Microsoft.Dynamics.CRM.fetchxmlpagingcookie"
MORE_RECORDS = "Microsoft.Dynamics.CRM.morerecords"
_PREFER = f'odata.include-annotations="{FORMATTED_VALUE},{PAGING_COOKIE},{MORE_RECORDS}"'

# Dataverse accepts GET addresses up to 32 KB and 64 KB inside a batch. Proxies in front of
# it are often stricter, so switch to the batch form early.
MAX_GET_URL_LENGTH = 8_000
MAX_BATCH_URL_LENGTH = 65_536

# "AggregateQueryRecordLimit exceeded": the count spans more than 50,000 records.
_AGGREGATE_LIMIT_CODES = {"0x8004e023", "8004e023", "-2147164125"}

_ENTITY_SET_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_BATCH_GET_RE = re.compile(rb"^(GET|POST|PUT|PATCH|DELETE|MERGE|HEAD|OPTIONS) ", re.MULTILINE)
_BATCH_STATUS_RE = re.compile(r"^HTTP/1\.1 (\d{3})", re.MULTILINE)
_RETRYABLE_STATUS = {429, 503}


@dataclass(frozen=True)
class FetchPage:
    """One page of rows from a FetchXML query."""

    rows: list[dict[str, Any]]
    paging_cookie: str | None
    more_records: bool


async def _enforce_read_only(request: httpx.Request) -> None:
    """Second line of defense: runs for every request the HTTP client sends."""
    if request.method == "GET":
        return
    if request.method == "POST" and request.url.path.endswith("/$batch"):
        verbs = _BATCH_GET_RE.findall(request.content)
        if verbs == [b"GET"]:
            return
    logger.critical("Blocked a non-read request (%s). This is a bug.", request.method)
    raise ReadOnlyViolationError("This server is read-only and refused to send a write request.")


def _decode_paging_cookie(annotation: str | None) -> str | None:
    """Extract the cookie Dataverse expects back in the next request.

    The annotation is a small XML element whose `pagingcookie` attribute is URL-encoded twice.
    """
    if not annotation:
        return None
    try:
        element = SafeET.fromstring(annotation)
    except Exception:  # noqa: BLE001 - any parse failure means an unusable cookie
        raise UpstreamError("Dataverse returned a paging marker that could not be read.") from None
    encoded = element.get("pagingcookie")
    return unquote(unquote(encoded)) if encoded else None


class DataverseClient:
    """Read-only client for the Dataverse Web API."""

    def __init__(
        self,
        settings: Settings,
        token_provider: TokenProvider,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._settings = settings
        self._tokens = token_provider
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            # Never follow redirects: a redirect could carry the token to another host.
            follow_redirects=False,
            verify=True,
            transport=transport,
            event_hooks={"request": [_enforce_read_only]},
            headers={
                "Accept": "application/json",
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0",
                "User-Agent": f"dataverse-views-mcp/{__version__}",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        """GET a path relative to the Web API root and return the JSON body."""
        self._check_path(path)
        response = await self._send("GET", path, params=dict(params or {}))
        return self._json(response.status_code, response.text)

    async def fetch(self, entity_set: str, fetchxml: str) -> FetchPage:
        """Run a FetchXML query against a table's entity set and return one page."""
        if not _ENTITY_SET_RE.match(entity_set):
            raise InvalidInputError("The table name is not valid.")
        query = urlencode({"fetchXml": fetchxml})
        relative_url = f"{entity_set}?{query}"
        full_length = len(self._settings.api_base_url) + len(relative_url)
        if full_length <= MAX_GET_URL_LENGTH:
            response = await self._send(
                "GET", entity_set, params={"fetchXml": fetchxml}, headers={"Prefer": _PREFER}
            )
            body = self._json(response.status_code, response.text)
        elif full_length <= MAX_BATCH_URL_LENGTH:
            body = await self._fetch_in_batch(relative_url)
        else:
            raise UnsupportedViewError("This view's query is too long to send to Dataverse.")
        rows = body.get("value")
        if not isinstance(rows, list):
            raise UpstreamError("Dataverse returned a response in an unexpected format.")
        return FetchPage(
            rows=rows,
            paging_cookie=_decode_paging_cookie(body.get(f"@{PAGING_COOKIE}")),
            more_records=bool(body.get(f"@{MORE_RECORDS}", False)),
        )

    @staticmethod
    def _check_path(path: str) -> None:
        if (
            not path
            or path.startswith(("/", "\\"))
            or "://" in path
            or ".." in path
            or "?" in path
            or "#" in path
            or any(ord(char) < 32 for char in path)
        ):
            raise InvalidInputError("The request path is not valid.")

    async def _fetch_in_batch(self, relative_url: str) -> dict[str, Any]:
        """Send one GET inside a `$batch` envelope, for queries too long for an address."""
        boundary = f"batch_{uuid.uuid4().hex}"
        api_path = httpx.URL(self._settings.api_base_url).path
        payload = "\r\n".join(
            [
                f"--{boundary}",
                "Content-Type: application/http",
                "Content-Transfer-Encoding: binary",
                "",
                f"GET {api_path}{relative_url} HTTP/1.1",
                "Accept: application/json",
                f"Prefer: {_PREFER}",
                "",
                "",
                f"--{boundary}--",
                "",
            ]
        ).encode("utf-8")
        response = await self._send(
            "POST",
            "$batch",
            content=payload,
            headers={"Content-Type": f'multipart/mixed; boundary="{boundary}"'},
        )
        text = response.text
        status = _BATCH_STATUS_RE.search(text)
        if status is None:
            raise UpstreamError("Dataverse returned a response in an unexpected format.")
        # The inner response is: status line, headers, blank line, JSON body, closing boundary.
        _, _, after_headers = text[status.start() :].partition("\r\n\r\n")
        start, end = after_headers.find("{"), after_headers.rfind("}")
        inner_body = after_headers[start : end + 1] if start != -1 and end > start else ""
        inner_status = int(status.group(1))
        if inner_status >= 400:
            raise self._error_for(inner_status, inner_body)
        return self._json(inner_status, inner_body)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        attempts = self._settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            token = await self._tokens.get_token()
            request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
            try:
                response = await self._http.request(
                    method, path, params=params, headers=request_headers, content=content
                )
            except DataverseViewsError:
                raise
            except httpx.TimeoutException:
                failure: DataverseViewsError = UpstreamError(
                    "Dataverse did not respond in time. Try again shortly."
                )
            except httpx.HTTPError as exc:
                logger.warning("Dataverse request failed (%s)", type(exc).__name__)
                failure = UpstreamError("Dataverse could not be reached. Try again shortly.")
            else:
                if response.status_code in _RETRYABLE_STATUS:
                    wait = self._retry_after(response, attempt)
                    if attempt < attempts and wait <= self._settings.max_retry_wait_seconds:
                        logger.warning(
                            "Dataverse asked the server to slow down (HTTP %s); waiting %.1fs",
                            response.status_code,
                            wait,
                        )
                        await self._sleep(wait)
                        continue
                    raise ThrottledError(
                        "Dataverse is limiting requests right now. Try again in a few minutes."
                    )
                if response.status_code >= 400:
                    raise self._error_for(response.status_code, response.text)
                return response
            if attempt == attempts:
                raise failure
            await self._sleep(min(2.0 ** (attempt - 1), self._settings.max_retry_wait_seconds))
        raise UpstreamError("Dataverse could not be reached.")  # pragma: no cover

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        """Honor Dataverse's retry signal; fall back to exponential backoff."""
        header = response.headers.get("Retry-After", "")
        try:
            return max(0.0, float(header))
        except ValueError:
            return float(2 ** (attempt - 1))

    @staticmethod
    def _json(status: int, text: str) -> dict[str, Any]:
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            logger.warning("Dataverse returned a non-JSON body (HTTP %s)", status)
            raise UpstreamError("Dataverse returned a response in an unexpected format.")
        return body

    @staticmethod
    def _error_for(status: int, text: str) -> DataverseViewsError:
        """Map an API failure to a plain error. Dataverse's own message is never relayed."""
        code = ""
        try:
            body = json.loads(text)
            code = str(body["error"]["code"]).lower()
        except (ValueError, KeyError, TypeError):
            pass
        logger.warning("Dataverse returned HTTP %s (code=%s)", status, code or "none")
        if code in _AGGREGATE_LIMIT_CODES:
            return CountLimitExceededError(
                "This count covers more than 50,000 records, which is more than Dataverse "
                "can count in one query. Add a filter to narrow it."
            )
        if status == 401:
            return AuthenticationError("Dataverse did not accept the server's sign-in.")
        if status == 403:
            return PermissionDeniedError("The server's identity is not allowed to read this data.")
        if status == 404:
            return NotFoundError("Dataverse could not find what was requested.")
        if status in (400, 412, 413, 414):
            return BadRequestError("Dataverse rejected the query.")
        return UpstreamError("Dataverse returned an unexpected error. Try again shortly.")

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import (
    MAX_GET_URL_LENGTH,
    DataverseClient,
    _enforce_read_only,
)
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
from tests.conftest import FakeTokens, Sleeper

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx.Request], httpx.Response]
FETCH = '<fetch><entity name="thing"><attribute name="name"/></entity></fetch>'


class Recorder:
    """A fake Dataverse: replays queued responses and records every request."""

    def __init__(self, *responses: httpx.Response | Exception | Handler) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return item(request)


def make_client(
    settings: Settings, recorder: Recorder, sleeper: Sleeper | None = None
) -> DataverseClient:
    return DataverseClient(
        settings,
        FakeTokens(),
        transport=httpx.MockTransport(recorder),
        sleep=sleeper or Sleeper(),
    )


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


def api_error(status: int, code: str = "0x0", message: str = "internal detail") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


async def test_get_attaches_token_and_standard_headers(settings: Settings) -> None:
    recorder = Recorder(ok({"UserId": "x"}))
    client = make_client(settings, recorder)

    body = await client.get("WhoAmI")

    request = recorder.requests[0]
    assert body == {"UserId": "x"}
    assert request.method == "GET"
    assert str(request.url) == "https://org.example.test/api/data/v9.2/WhoAmI"
    assert request.headers["Authorization"] == "Bearer made-up-token"
    assert request.headers["OData-Version"] == "4.0"
    assert request.headers["Accept"] == "application/json"


async def test_get_passes_query_parameters_encoded(settings: Settings) -> None:
    recorder = Recorder(ok({"value": []}))
    client = make_client(settings, recorder)

    await client.get("savedqueries", {"$filter": "name eq 'a&b'"})

    assert recorder.requests[0].url.params["$filter"] == "name eq 'a&b'"
    assert "a%26b" in str(recorder.requests[0].url)


@pytest.mark.parametrize(
    "path",
    ["", "/absolute", "https://evil.example/x", "../up", "a?b=1", "a#frag", "a\nb", "\\\\host"],
)
async def test_get_rejects_paths_that_could_leave_the_api(settings: Settings, path: str) -> None:
    recorder = Recorder(ok({}))
    client = make_client(settings, recorder)

    with pytest.raises(InvalidInputError):
        await client.get(path)

    assert recorder.requests == []


async def test_fetch_requests_display_labels_and_returns_rows(settings: Settings) -> None:
    recorder = Recorder(ok({"value": [{"name": "A"}, {"name": "B"}]}))
    client = make_client(settings, recorder)

    page = await client.fetch("things", FETCH)

    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/data/v9.2/things"
    assert request.url.params["fetchXml"] == FETCH
    assert "OData.Community.Display.V1.FormattedValue" in request.headers["Prefer"]
    assert "fetchxmlpagingcookie" in request.headers["Prefer"]
    assert page.rows == [{"name": "A"}, {"name": "B"}]
    assert page.more_records is False
    assert page.paging_cookie is None


async def test_fetch_returns_decoded_paging_marker(settings: Settings) -> None:
    inner = '<cookie page="1"><thingid last="{AAA}" first="{BBB}" /></cookie>'
    annotation = (
        f'<cookie pagenumber="2" pagingcookie="{quote(quote(inner, safe=""), safe="")}" '
        'istracking="False" />'
    )
    recorder = Recorder(
        ok(
            {
                "value": [{"name": "A"}],
                "@Microsoft.Dynamics.CRM.fetchxmlpagingcookie": annotation,
                "@Microsoft.Dynamics.CRM.morerecords": True,
            }
        )
    )
    client = make_client(settings, recorder)

    page = await client.fetch("things", FETCH)

    assert page.more_records is True
    assert page.paging_cookie == inner


async def test_fetch_rejects_unreadable_paging_marker(settings: Settings) -> None:
    body = {"value": [], "@Microsoft.Dynamics.CRM.fetchxmlpagingcookie": "<not xml"}
    client = make_client(settings, Recorder(ok(body)))

    with pytest.raises(UpstreamError):
        await client.fetch("things", FETCH)


@pytest.mark.parametrize("entity_set", ["Things", "things/../x", "things?x", "", "a b"])
async def test_fetch_rejects_bad_entity_set_names(settings: Settings, entity_set: str) -> None:
    recorder = Recorder(ok({"value": []}))
    client = make_client(settings, recorder)

    with pytest.raises(InvalidInputError):
        await client.fetch(entity_set, FETCH)

    assert recorder.requests == []


def batch_response(status: int, body: dict[str, Any]) -> httpx.Response:
    text = "\r\n".join(
        [
            "--batchresponse_1",
            "Content-Type: application/http",
            "Content-Transfer-Encoding: binary",
            "",
            f"HTTP/1.1 {status} Whatever",
            "Content-Type: application/json; odata.metadata=minimal",
            "OData-Version: 4.0",
            "",
            json.dumps(body),
            "--batchresponse_1--",
            "",
        ]
    )
    return httpx.Response(
        200, text=text, headers={"Content-Type": "multipart/mixed; boundary=batchresponse_1"}
    )


def long_fetch() -> str:
    columns = "".join(f'<attribute name="column_{i:04d}"/>' for i in range(400))
    return f'<fetch><entity name="thing">{columns}</entity></fetch>'


async def test_long_query_is_sent_as_one_get_inside_a_batch(settings: Settings) -> None:
    recorder = Recorder(batch_response(200, {"value": [{"name": "A"}]}))
    client = make_client(settings, recorder)
    fetch = long_fetch()
    assert len(fetch) > MAX_GET_URL_LENGTH

    page = await client.fetch("things", fetch)

    request = recorder.requests[0]
    content = request.content.decode()
    assert request.method == "POST"
    assert request.url.path == "/api/data/v9.2/$batch"
    assert request.headers["Content-Type"].startswith("multipart/mixed; boundary=")
    assert content.count("GET /api/data/v9.2/things?fetchXml=") == 1
    assert "column_0399" in content
    assert "FormattedValue" in content
    assert page.rows == [{"name": "A"}]


async def test_error_inside_a_batch_is_mapped(settings: Settings) -> None:
    body = {"error": {"code": "0x80040220", "message": "principal lacks prvReadThing"}}
    client = make_client(settings, Recorder(batch_response(403, body)))

    with pytest.raises(PermissionDeniedError) as excinfo:
        await client.fetch("things", long_fetch())

    assert "prvReadThing" not in excinfo.value.message


async def test_unreadable_batch_response_is_an_upstream_error(settings: Settings) -> None:
    client = make_client(settings, Recorder(httpx.Response(200, text="garbage")))

    with pytest.raises(UpstreamError):
        await client.fetch("things", long_fetch())


async def test_query_too_long_even_for_a_batch_is_refused(settings: Settings) -> None:
    recorder = Recorder(ok({"value": []}))
    client = make_client(settings, recorder)

    with pytest.raises(UnsupportedViewError):
        await client.fetch("things", "<fetch>" + "x" * 70_000 + "</fetch>")

    assert recorder.requests == []


async def test_throttling_waits_for_the_retry_signal_then_succeeds(settings: Settings) -> None:
    recorder = Recorder(
        httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"code": "0x80072322"}}),
        ok({"value": []}),
    )
    sleeper = Sleeper()
    client = make_client(settings, recorder, sleeper)

    await client.fetch("things", FETCH)

    assert sleeper.waits == [7.0]
    assert len(recorder.requests) == 2


async def test_throttling_without_a_signal_backs_off_exponentially(settings: Settings) -> None:
    recorder = Recorder(httpx.Response(503), httpx.Response(503), ok({"value": []}))
    sleeper = Sleeper()

    await make_client(settings, recorder, sleeper).fetch("things", FETCH)

    assert sleeper.waits == [1.0, 2.0]


async def test_throttling_gives_up_after_max_retries(settings: Settings) -> None:
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "1"}))
    sleeper = Sleeper()

    with pytest.raises(ThrottledError):
        await make_client(settings, recorder, sleeper).fetch("things", FETCH)

    assert len(recorder.requests) == settings.max_retries + 1
    assert sleeper.waits == [1.0] * settings.max_retries


async def test_a_very_long_retry_signal_is_not_waited_out(settings: Settings) -> None:
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "900"}))
    sleeper = Sleeper()

    with pytest.raises(ThrottledError):
        await make_client(settings, recorder, sleeper).fetch("things", FETCH)

    assert sleeper.waits == []
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (api_error(401), AuthenticationError),
        (api_error(403), PermissionDeniedError),
        (api_error(404), NotFoundError),
        (api_error(400), BadRequestError),
        (api_error(500), UpstreamError),
        (api_error(400, code="0x8004E023"), CountLimitExceededError),
        (httpx.Response(502, text="<html>bad gateway</html>"), UpstreamError),
    ],
)
async def test_api_errors_map_to_plain_errors_without_upstream_text(
    settings: Settings,
    response: httpx.Response,
    expected: type[DataverseViewsError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client(settings, Recorder(response))

    with caplog.at_level(logging.WARNING), pytest.raises(expected) as excinfo:
        await client.get("WhoAmI")

    assert "internal detail" not in excinfo.value.message
    assert "internal detail" not in caplog.text


async def test_non_json_success_body_is_an_upstream_error(settings: Settings) -> None:
    client = make_client(settings, Recorder(httpx.Response(200, text="<html>sign in</html>")))

    with pytest.raises(UpstreamError):
        await client.get("WhoAmI")


async def test_fetch_without_value_list_is_an_upstream_error(settings: Settings) -> None:
    with pytest.raises(UpstreamError):
        await make_client(settings, Recorder(ok({"unexpected": 1}))).fetch("things", FETCH)


async def test_redirects_are_not_followed(settings: Settings) -> None:
    recorder = Recorder(httpx.Response(302, headers={"Location": "https://evil.example/steal"}))
    client = make_client(settings, recorder)

    with pytest.raises(UpstreamError):
        await client.get("WhoAmI")

    assert len(recorder.requests) == 1


async def test_network_failures_are_retried_then_reported_plainly(settings: Settings) -> None:
    recorder = Recorder(httpx.ConnectError("dns failure for org.example.test"))
    sleeper = Sleeper()

    with pytest.raises(UpstreamError) as excinfo:
        await make_client(settings, recorder, sleeper).get("WhoAmI")

    assert "dns failure" not in excinfo.value.message
    assert len(recorder.requests) == settings.max_retries + 1


async def test_timeout_then_success(settings: Settings) -> None:
    recorder = Recorder(httpx.ReadTimeout("slow"), ok({"UserId": "x"}))

    assert await make_client(settings, recorder).get("WhoAmI") == {"UserId": "x"}


async def test_timeouts_are_reported_plainly(make_settings: Callable[..., Settings]) -> None:
    recorder = Recorder(httpx.ReadTimeout("slow"))

    with pytest.raises(UpstreamError, match="did not respond in time"):
        await make_client(make_settings(max_retries=0), recorder).get("WhoAmI")


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
async def test_write_requests_are_refused_before_they_are_sent(
    settings: Settings, method: str
) -> None:
    recorder = Recorder(ok({}))
    client = make_client(settings, recorder)

    with pytest.raises(ReadOnlyViolationError):
        await client._send(method, "things", content=b"{}")

    assert recorder.requests == []


@pytest.mark.parametrize(
    "body",
    [
        b"--b\r\n\r\nPOST /api/data/v9.2/things HTTP/1.1\r\n\r\n{}\r\n--b--",
        b"--b\r\n\r\nGET /a HTTP/1.1\r\n\r\n--b\r\n\r\nDELETE /a(1) HTTP/1.1\r\n\r\n--b--",
        b"--b\r\n\r\nGET /a HTTP/1.1\r\n\r\n--b\r\n\r\nGET /b HTTP/1.1\r\n\r\n--b--",
        b"",
    ],
)
async def test_batches_holding_anything_but_one_get_are_refused(body: bytes) -> None:
    request = httpx.Request("POST", "https://org.example.test/api/data/v9.2/$batch", content=body)

    with pytest.raises(ReadOnlyViolationError):
        await _enforce_read_only(request)


async def test_token_is_requested_for_each_attempt(settings: Settings) -> None:
    tokens = FakeTokens()
    recorder = Recorder(httpx.Response(429, headers={"Retry-After": "0"}), ok({"UserId": "x"}))
    client = DataverseClient(
        settings, tokens, transport=httpx.MockTransport(recorder), sleep=Sleeper()
    )

    await client.get("WhoAmI")
    await client.aclose()

    assert tokens.calls == 2

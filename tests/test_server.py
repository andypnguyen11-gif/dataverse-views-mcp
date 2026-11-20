from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client

from dataverse_views_mcp import server as server_module
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.server import build_server, configure_logging, main
from tests.conftest import BASE_SETTINGS, OTHER_VIEW_ID, VIEW_ID, read_fixture
from tests.fakes import (
    FakeDataverse,
    add_choices,
    add_table,
    add_view,
    make_services,
    view_row,
)

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {"list_views", "describe_view", "find_record", "count_view", "run_view"}
# Schema constructs that Copilot Studio drops or mishandles.
FORBIDDEN_SCHEMA_KEYS = {"$ref", "$defs", "anyOf", "oneOf", "allOf", "exclusiveMinimum"}


def ready_fake() -> FakeDataverse:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {"value": [view_row(VIEW_ID)]}
    add_view(fake, VIEW_ID, read_fixture("view_simple.xml"))
    add_table(fake, "thing")
    return fake


def schema_problems(node: Any, path: str = "") -> list[str]:
    problems: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_SCHEMA_KEYS:
                problems.append(f"{path}/{key}")
            if key == "type" and isinstance(value, list):
                problems.append(f"{path}/type is a list")
            problems.extend(schema_problems(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            problems.extend(schema_problems(value, f"{path}[{index}]"))
    return problems


async def test_registers_the_expected_tools_all_marked_read_only(settings: Settings) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.annotations.idempotent_hint is True, tool.name
        assert tool.description, tool.name
        assert "only reads" in tool.description, tool.name


async def test_no_tool_name_suggests_a_write(settings: Settings) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]

    for name in names:
        assert not any(
            verb in name for verb in ("create", "update", "delete", "write", "set", "execute")
        )


async def test_input_schemas_avoid_constructs_copilot_studio_rejects(settings: Settings) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        assert schema_problems(tool.input_schema) == [], tool.name
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description"), f"{tool.name}.{name} needs a description"


async def test_tool_call_returns_structured_and_text_results(settings: Settings) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        result = await client.call_tool("list_views", {"table": "thing"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["views"][0]["id"] == VIEW_ID
    assert json.loads(result.content[0].text)["view_count"] == 1  # type: ignore[union-attr]


async def test_describe_view_through_the_protocol(settings: Settings) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        result = await client.call_tool("describe_view", {"view_id": VIEW_ID})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["view"]["name"] == "Active Things"


async def test_describe_view_lists_choice_labels_through_the_protocol(settings: Settings) -> None:
    fake = ready_fake()
    add_choices(fake, "thing", "State", {"statecode": [(0, "Active"), (1, "Inactive")]})

    async with Client(build_server(make_services(settings, fake))) as client:
        result = await client.call_tool("describe_view", {"view_id": VIEW_ID})
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert result.is_error is False
    assert result.structured_content is not None
    statecode = next(
        entry
        for entry in result.structured_content["filterable_columns"]
        if entry["column"] == "statecode"
    )
    assert statecode["choices"] == [
        {"value": 0, "label": "Active"},
        {"value": 1, "label": "Inactive"},
    ]
    assert "choice" in (tools["describe_view"].description or "")


async def test_expected_failures_reach_the_client_as_short_plain_messages(
    settings: Settings,
) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        result = await client.call_tool("describe_view", {"view_id": OTHER_VIEW_ID})

    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "not on the approved list" in text
    assert "Traceback" not in text


async def test_unexpected_failures_reveal_nothing_internal(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    fake = ready_fake()
    fake.responses["savedqueries"] = RuntimeError("secret internal detail at https://org.internal")

    with caplog.at_level(logging.CRITICAL):
        async with Client(build_server(make_services(settings, fake))) as client:
            result = await client.call_tool("list_views", {"table": "thing"})

    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "secret internal detail" not in text
    assert "org.internal" not in text


async def test_missing_and_wrongly_typed_arguments_are_rejected(settings: Settings) -> None:
    async with Client(build_server(make_services(settings, ready_fake()))) as client:
        missing = await client.call_tool("list_views", {})
        wrong = await client.call_tool("list_views", {"table": {"nested": "object"}})

    assert missing.is_error is True
    assert wrong.is_error is True


async def test_http_client_is_closed_when_the_server_stops(settings: Settings) -> None:
    fake = ready_fake()

    async with Client(build_server(make_services(settings, fake))):
        assert fake.closed is False

    assert fake.closed is True


def test_logging_goes_to_stderr_never_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    logging.getLogger("dataverse_views_mcp").info("hello from the test")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "hello from the test" in captured.err
    assert logging.getLogger("httpx").level == logging.WARNING


def test_main_exits_with_a_plain_message_when_settings_are_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main()

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert captured.out == ""
    assert "DATAVERSE_URL is required" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("extra", "warning"),
    [
        ({"DATAVERSE_ALLOW_ALL_SYSTEM_VIEWS": "true"}, "local testing only"),
        ({"DATAVERSE_APPROVED_VIEW_IDS": ""}, "No approved views"),
        ({}, None),
    ],
)
def test_main_starts_over_stdio_and_warns_about_risky_settings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    extra: dict[str, str],
    warning: str | None,
) -> None:
    for key, value in {**BASE_SETTINGS, **{}}.items():
        monkeypatch.setenv(f"DATAVERSE_{key.upper()}", value)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    started: list[str] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            started.append(transport)

    factory: Callable[[Any], FakeServer] = lambda _services: FakeServer()  # noqa: E731
    monkeypatch.setattr(server_module, "build_server", factory)
    monkeypatch.setattr(server_module, "configure_logging", lambda _level: None)

    with caplog.at_level(logging.WARNING, logger="dataverse_views_mcp"):
        main()

    assert started == ["stdio"]
    if warning is None:
        assert caplog.text == ""
    else:
        assert warning in caplog.text


async def test_count_view_through_the_protocol_with_and_without_filters(
    settings: Settings,
) -> None:
    from dataverse_views_mcp.dataverse_client import FetchPage

    fake = ready_fake()
    fake.fetch_pages = [
        FetchPage(rows=[{"record_count": 12}], paging_cookie=None, more_records=False)
    ]
    record = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    async with Client(build_server(make_services(settings, fake))) as client:
        plain = await client.call_tool("count_view", {"view_id": VIEW_ID})
        filtered = await client.call_tool(
            "count_view",
            {
                "view_id": VIEW_ID,
                "filters": [{"column": "officeid", "operator": "eq", "value": record}],
            },
        )
        bad = await client.call_tool(
            "count_view",
            {
                "view_id": VIEW_ID,
                "filters": [{"column": "officeid", "operator": "eq", "value": "N"}],
            },
        )

    assert plain.structured_content is not None
    assert plain.structured_content["count"] == 12
    assert filtered.structured_content is not None
    assert filtered.structured_content["filters_applied"][0]["value"] == record
    assert bad.is_error is True
    assert "record ID from find_record" in bad.content[0].text  # type: ignore[union-attr]


async def test_run_view_through_the_protocol(settings: Settings) -> None:
    from dataverse_views_mcp.dataverse_client import FetchPage

    fake = ready_fake()
    row = {"thingid": "00000001-0000-0000-0000-000000000000", "name": "Thing 1"}
    fake.fetch_pages = [FetchPage(rows=[row], paging_cookie="<cookie/>", more_records=True)]

    async with Client(build_server(make_services(settings, fake))) as client:
        first = await client.call_tool("run_view", {"view_id": VIEW_ID})
        assert first.structured_content is not None
        marker = first.structured_content["next_page_marker"]
        second = await client.call_tool("run_view", {"view_id": VIEW_ID, "page_marker": marker})
        too_big = await client.call_tool("run_view", {"view_id": VIEW_ID, "page_size": 100000})

    assert first.structured_content["rows"][0]["values"]["name"] == "Thing 1"
    assert second.structured_content is not None
    assert second.structured_content["page"] == 2
    assert too_big.is_error is True

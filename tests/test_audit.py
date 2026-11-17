from __future__ import annotations

import io
import json
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import Implementation

from dataverse_views_mcp.audit import AuditError, AuditLog, Caller, outcome_for
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import FetchPage
from dataverse_views_mcp.errors import (
    InvalidFilterError,
    ThrottledError,
    UpstreamError,
    ViewNotApprovedError,
)
from dataverse_views_mcp.server import build_server
from tests.conftest import OTHER_VIEW_ID, RECORD_ID, VIEW_ID, read_fixture
from tests.fakes import FakeDataverse, add_table, add_view, make_services, view_row

pytestmark = pytest.mark.anyio

FIXED_TIME = datetime(2026, 11, 16, 12, 30, 45, 123000, tzinfo=UTC)
CALLER = Caller(client="test-client", client_version="1.2", request_id="7")
SECRET_ROW_TEXT = "ROW-CONTENT-THAT-MUST-NOT-BE-LOGGED"
FILTERS = [{"column": "officeid", "operator": "eq", "value": RECORD_ID}]


def lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def record(
    log: AuditLog,
    *,
    tool: str = "count_view",
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    log.record(
        tool=tool,
        caller=CALLER,
        arguments=arguments if arguments is not None else {"view_id": VIEW_ID},
        result=result,
        error=error,
        duration_ms=12,
    )


# --- one line, with the fields an auditor needs ----------------------------------------------


def test_success_line_has_time_tool_view_filters_caller_and_outcome(settings: Settings) -> None:
    stream = io.StringIO()
    log = AuditLog(settings, stream=stream, clock=lambda: FIXED_TIME)

    record(
        log,
        arguments={"view_id": VIEW_ID, "filters": FILTERS},
        result={
            "count": 4821,
            "view": {"id": VIEW_ID, "name": "Active Things", "table": "thing"},
            "filters_applied": FILTERS,
        },
    )

    assert stream.getvalue().count("\n") == 1
    entry = lines(stream)[0]
    assert entry["time"] == "2026-11-16T12:30:45.123+00:00"
    assert entry["event"] == "tool_call"
    assert len(entry["event_id"]) == 36
    assert entry["environment"] == "org.example.test"
    assert entry["tool"] == "count_view"
    assert entry["view_id"] == VIEW_ID
    assert entry["view_name"] == "Active Things"
    assert entry["table"] == "thing"
    assert entry["filters"] == FILTERS
    assert entry["caller"]["client_declared"] == "test-client"
    assert entry["caller"]["client_version"] == "1.2"
    assert entry["caller"]["request_id"] == "7"
    assert entry["caller"]["service_identity"] == settings.client_id
    assert entry["caller"]["os_user"]
    assert entry["outcome"] == "success"
    assert entry["error_code"] == ""
    assert entry["duration_ms"] == 12


def test_a_count_is_never_logged(settings: Settings) -> None:
    stream = io.StringIO()

    record(AuditLog(settings, stream=stream), result={"count": 4821, "filters_applied": []})

    assert "4821" not in stream.getvalue()
    assert "items_returned" not in lines(stream)[0]


def test_paging_is_logged_but_rows_and_row_counts_are_not(settings: Settings) -> None:
    stream = io.StringIO()
    result = {
        "view": {"id": VIEW_ID, "name": "Active Things", "table": "thing"},
        "filters_applied": [],
        "page": 2,
        "page_size": 50,
        "row_count": 1,
        "rows": [{"id": RECORD_ID, "values": {"name": SECRET_ROW_TEXT}}],
    }

    record(AuditLog(settings, stream=stream), tool="run_view", result=result)

    entry = lines(stream)[0]
    assert (entry["page"], entry["page_size"]) == (2, 50)
    assert "items_returned" not in entry
    assert "row_count" not in entry
    assert SECRET_ROW_TEXT not in stream.getvalue()
    assert "rows" not in entry


def test_secret_is_never_logged(settings: Settings) -> None:
    stream = io.StringIO()

    record(AuditLog(settings, stream=stream))

    assert "made-up-secret-value" not in stream.getvalue()


# --- failures --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "outcome", "code"),
    [
        (ViewNotApprovedError("no"), "refused", "view_not_approved"),
        (InvalidFilterError("no"), "refused", "invalid_filter"),
        (ThrottledError("busy"), "error", "throttled"),
        (UpstreamError("down"), "error", "upstream_error"),
        (RuntimeError("bug with internal detail"), "error", "internal_error"),
    ],
)
def test_failures_are_logged_with_outcome_and_code(
    settings: Settings, error: Exception, outcome: str, code: str
) -> None:
    stream = io.StringIO()

    record(
        AuditLog(settings, stream=stream),
        arguments={"view_id": OTHER_VIEW_ID, "filters": FILTERS},
        error=error,
    )

    entry = lines(stream)[0]
    assert (entry["outcome"], entry["error_code"]) == (outcome, code)
    assert entry["view_id"] == OTHER_VIEW_ID
    assert entry["view_name"] is None
    assert entry["filters"] == FILTERS
    assert "internal detail" not in stream.getvalue()
    assert outcome_for(None) == ("success", "")


def test_hostile_arguments_cannot_forge_or_flood_the_log(settings: Settings) -> None:
    stream = io.StringIO()
    forged = 'x"}\n{"time":"2020-01-01","tool":"forged","outcome":"success"}'
    arguments = {
        "view_id": forged + "y" * 5000,
        "filters": [{"column": "c", "operator": "eq", "value": "v" * 5000}] * 500,
        "search_text": "line one\nline two\x00",
    }

    record(
        AuditLog(settings, stream=stream),
        tool="find_record",
        arguments=arguments,
        error=InvalidFilterError("no"),
    )

    assert stream.getvalue().count("\n") == 1, "one call must be exactly one line"
    entry = lines(stream)[0]
    assert entry["tool"] == "find_record"
    assert len(entry["view_id"]) < 250
    assert len(entry["filters"]) == 10
    assert len(entry["filters"][0]["value"]) < 250
    assert entry["search_text"] == "line one line two"
    assert len(stream.getvalue()) < 16_500


def test_deeply_nested_and_odd_arguments_are_bounded(settings: Settings) -> None:
    stream = io.StringIO()
    nested: Any = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}

    record(AuditLog(settings, stream=stream), arguments={"filters": [nested, object(), (1, 2)]})

    filters = lines(stream)[0]["filters"]
    assert "deep" not in json.dumps(filters)
    assert filters[1] == "[omitted]"
    assert filters[2] == [1, 2]


def test_one_call_cannot_write_an_enormous_line(settings: Settings) -> None:
    stream = io.StringIO()
    wide = {f"key{k}": ["v" * 200] * 50 for k in range(50)}

    record(
        AuditLog(settings, stream=stream),
        arguments={"view_id": VIEW_ID, "filters": [wide] * 50, "search_text": "s" * 100},
        error=InvalidFilterError("no"),
    )

    assert stream.getvalue().count("\n") == 1
    assert len(stream.getvalue()) < 16_500
    entry = lines(stream)[0]
    assert entry["tool"] == "count_view"
    assert entry["outcome"] == "refused"


def test_line_that_is_still_too_large_keeps_a_fingerprint_instead(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dataverse_views_mcp.audit.MAX_LINE_LENGTH", 600)
    stream = io.StringIO()

    record(
        AuditLog(settings, stream=stream),
        arguments={
            "view_id": VIEW_ID,
            "filters": [{"column": "c", "value": "v" * 150}] * 10,
            "search_text": "x",
        },
        error=InvalidFilterError("no"),
    )

    entry = lines(stream)[0]
    assert entry["filters"].startswith("[omitted: too large, sha256:")
    assert "search_text" not in entry
    assert entry["view_id"] == VIEW_ID


# --- privacy option --------------------------------------------------------------------------


def test_values_can_be_replaced_by_fingerprints(make_settings: Callable[..., Settings]) -> None:
    stream = io.StringIO()
    log = AuditLog(make_settings(audit_log_values=False), stream=stream)

    record(
        log,
        tool="find_record",
        arguments={
            "table": "office",
            "search_text": "Pat Example",
            "filters": [*FILTERS, {"column": "x", "operator": "null"}, "not-a-dict"],
        },
        error=InvalidFilterError("no"),
    )

    entry = lines(stream)[0]
    assert "Pat Example" not in stream.getvalue()
    assert RECORD_ID not in stream.getvalue()
    assert entry["search_text"].startswith("sha256:")
    assert entry["filters"][0]["column"] == "officeid"
    assert entry["filters"][0]["value"].startswith("sha256:")
    assert entry["filters"][1] == {"column": "x", "operator": "null"}
    assert entry["table"] == "office"


# --- where the log goes ----------------------------------------------------------------------


def test_writes_to_stderr_by_default(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    record(AuditLog(settings))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["tool"] == "count_view"


def test_appends_to_a_file_readable_only_by_its_owner(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    path = tmp_path / "audit.log"
    log = AuditLog(make_settings(audit_log_path=str(path)))

    record(log)
    record(log, tool="run_view")

    written = [json.loads(line) for line in path.read_text().splitlines()]
    assert [entry["tool"] for entry in written] == ["count_view", "run_view"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_log_file_is_tightened_and_symlinks_are_refused(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    path = tmp_path / "audit.log"
    path.write_text("")
    path.chmod(0o644)
    record(AuditLog(make_settings(audit_log_path=str(path))))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    target = tmp_path / "elsewhere.txt"
    target.write_text("")
    link = tmp_path / "link.log"
    link.symlink_to(target)
    with pytest.raises(AuditError):
        record(AuditLog(make_settings(audit_log_path=str(link))))
    assert target.read_text() == ""


def test_unwritable_log_raises(make_settings: Callable[..., Settings], tmp_path: Path) -> None:
    log = AuditLog(make_settings(audit_log_path=str(tmp_path / "missing-folder" / "audit.log")))

    with pytest.raises(AuditError, match="audit log could not be written"):
        record(log)


def test_unknown_os_user_does_not_break_logging(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_user() -> str:
        raise KeyError("no user")

    monkeypatch.setattr("getpass.getuser", no_user)
    stream = io.StringIO()

    record(AuditLog(settings, stream=stream))

    assert lines(stream)[0]["caller"]["os_user"] == "unknown"


# --- through the server: every tool call is audited ------------------------------------------


def full_fake() -> FakeDataverse:
    fake = FakeDataverse()
    fake.responses["savedqueries"] = {"value": [view_row(VIEW_ID)]}
    add_view(fake, VIEW_ID, read_fixture("view_simple.xml"))
    add_table(fake, "thing")
    add_table(
        fake,
        "office",
        entity_set="offices",
        columns={"officeid": "Uniqueidentifier", "name": "String"},
    )
    return fake


async def test_all_five_tools_write_five_lines_with_no_record_contents(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(searchable_tables="office")
    fake = full_fake()
    row = {
        "thingid": RECORD_ID,
        "officeid": RECORD_ID,
        "name": SECRET_ROW_TEXT,
        "record_count": 31337,
    }
    fake.fetch_pages = [FetchPage(rows=[row], paging_cookie=None, more_records=False)]
    stream = io.StringIO()
    server = build_server(make_services(settings, fake, stream))
    client_info = Implementation(name="inspector", version="3.1")

    async with Client(server, client_info=client_info) as client:
        results = [
            await client.call_tool("list_views", {"table": "thing"}),
            await client.call_tool("describe_view", {"view_id": VIEW_ID}),
            await client.call_tool("find_record", {"table": "office", "search_text": "North"}),
            await client.call_tool("count_view", {"view_id": VIEW_ID, "filters": FILTERS}),
            await client.call_tool("run_view", {"view_id": VIEW_ID, "filters": FILTERS}),
        ]

    assert [result.is_error for result in results] == [False] * 5
    entries = lines(stream)
    assert [entry["tool"] for entry in entries] == [
        "list_views",
        "describe_view",
        "find_record",
        "count_view",
        "run_view",
    ]
    assert {entry["outcome"] for entry in entries} == {"success"}
    assert {entry["caller"]["client_declared"] for entry in entries} == {"inspector"}
    assert {entry["caller"]["client_version"] for entry in entries} == {"3.1"}
    assert entries[0]["table"] == "thing"
    assert all("items_returned" not in entry for entry in entries)
    assert entries[2]["search_text"] == "North"
    assert entries[3]["filters"] == FILTERS
    assert entries[3]["view_name"] == "Active Things"
    assert entries[4]["page"] == 1
    assert SECRET_ROW_TEXT not in stream.getvalue()
    assert "31337" not in stream.getvalue()
    assert "made-up-token" not in stream.getvalue()


async def test_refused_and_crashed_calls_are_audited_too(settings: Settings) -> None:
    fake = full_fake()
    fake.responses["savedqueries"] = RuntimeError("bug")
    stream = io.StringIO()

    async with Client(build_server(make_services(settings, fake, stream))) as client:
        refused = await client.call_tool("count_view", {"view_id": OTHER_VIEW_ID})
        crashed = await client.call_tool("list_views", {"table": "thing"})

    assert refused.is_error is True
    assert crashed.is_error is True
    entries = lines(stream)
    assert [(e["tool"], e["outcome"], e["error_code"]) for e in entries] == [
        ("count_view", "refused", "view_not_approved"),
        ("list_views", "error", "internal_error"),
    ]


async def test_calls_rejected_before_reaching_a_tool_are_audited_too(settings: Settings) -> None:
    stream = io.StringIO()

    async with Client(build_server(make_services(settings, full_fake(), stream))) as client:
        results = [
            await client.call_tool("run_view", {"view_id": VIEW_ID, "page_size": 999}),
            await client.call_tool("count_view", {"view_id": VIEW_ID, "filters": "x"}),
            await client.call_tool("describe_view", {}),
            await client.call_tool("drop_everything", {"table": "thing"}),
        ]

    assert [result.is_error for result in results] == [True] * 4
    entries = lines(stream)
    assert [(e["tool"], e["outcome"], e["error_code"]) for e in entries] == [
        ("run_view", "refused", "invalid_input"),
        ("count_view", "refused", "invalid_input"),
        ("describe_view", "refused", "invalid_input"),
        ("drop_everything", "refused", "invalid_input"),
    ]
    assert entries[0]["view_id"] == VIEW_ID
    assert entries[1]["filters"] == "x"


async def test_each_call_writes_exactly_one_line(settings: Settings) -> None:
    stream = io.StringIO()

    async with Client(build_server(make_services(settings, full_fake(), stream))) as client:
        await client.call_tool("list_views", {"table": "thing"})
        await client.call_tool("count_view", {"view_id": OTHER_VIEW_ID})
        await client.call_tool("list_views", {})

    assert len(lines(stream)) == 3


async def test_a_crash_logs_no_exception_text(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    fake = full_fake()
    fake.responses["savedqueries"] = RuntimeError("detail at https://org.internal/secret")

    with caplog.at_level(logging.DEBUG):
        async with Client(build_server(make_services(settings, fake, io.StringIO()))) as client:
            result = await client.call_tool("list_views", {"table": "thing"})

    assert result.is_error is True
    assert "unexpected problem" in result.content[0].text  # type: ignore[union-attr]
    assert "org.internal" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_a_call_that_cannot_be_audited_returns_no_data(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    settings = make_settings(audit_log_path=str(tmp_path / "missing-folder" / "audit.log"))
    fake = full_fake()
    fake.fetch_pages = [
        FetchPage(rows=[{"record_count": 31337}], paging_cookie=None, more_records=False)
    ]

    async with Client(build_server(make_services(settings, fake))) as client:
        result = await client.call_tool("count_view", {"view_id": VIEW_ID})

    assert result.is_error is True
    assert result.structured_content is None
    text = result.content[0].text  # type: ignore[union-attr]
    assert "audit log could not be written" in text
    assert "31337" not in text

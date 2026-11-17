from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from dataverse_views_mcp.acceptance import ConsistencyReport, run_consistency_check
from dataverse_views_mcp.config import Settings
from dataverse_views_mcp.dataverse_client import FetchPage
from dataverse_views_mcp.errors import ViewNotApprovedError
from tests.conftest import BASE_SETTINGS, OTHER_VIEW_ID, RECORD_ID, VIEW_ID, read_fixture
from tests.fakes import FakeDataverse, add_table, add_view, make_services

FILTERS = [{"column": "officeid", "operator": "eq", "value": RECORD_ID}]
SCRIPT = Path(__file__).parent.parent / "scripts" / "consistency_check.py"


def counts(*values: int) -> FakeDataverse:
    fake = FakeDataverse()
    add_view(fake, VIEW_ID, read_fixture("view_simple.xml"))
    add_table(fake, "thing")
    fake.fetch_pages = [FetchPage([{"record_count": value}], None, False) for value in values]
    return fake


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("consistency_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
async def test_ten_identical_runs_that_match_the_app_pass(settings: Settings) -> None:
    fake = counts(85)
    services = make_services(settings, fake, io.StringIO())

    report = await run_consistency_check(services, VIEW_ID, FILTERS, runs=10, expected=85)

    assert report.passed is True
    assert report.counts == [85] * 10
    assert len(fake.fetches) == 10
    assert len({query for _, query in fake.fetches}) == 1, "every run must send the same query"
    assert "RESULT: PASS" in report.summary()
    assert "All runs identical: yes" in report.summary()
    assert "Equals expected count (85): yes" in report.summary()
    assert "View: Active Things" in report.summary()


@pytest.mark.anyio
async def test_differing_runs_fail(settings: Settings) -> None:
    services = make_services(settings, counts(100, 85, 90), io.StringIO())

    report = await run_consistency_check(services, VIEW_ID, runs=3)

    assert report.passed is False
    assert report.consistent is False
    assert "All runs identical: NO" in report.summary()
    assert "[85, 90, 100]" in report.summary()
    assert "RESULT: FAIL" in report.summary()


@pytest.mark.anyio
async def test_consistent_but_different_from_the_app_fails(settings: Settings) -> None:
    services = make_services(settings, counts(85), io.StringIO())

    report = await run_consistency_check(services, VIEW_ID, runs=10, expected=90)

    assert report.consistent is True
    assert report.matches_expected is False
    assert report.passed is False
    assert "Equals expected count (90): NO" in report.summary()


@pytest.mark.anyio
async def test_without_an_expected_value_only_consistency_is_checked(settings: Settings) -> None:
    services = make_services(settings, counts(85), io.StringIO())

    report = await run_consistency_check(services, VIEW_ID, runs=2)

    assert report.matches_expected is None
    assert report.passed is True
    assert "only consistency was checked" in report.summary()


@pytest.mark.anyio
async def test_needs_at_least_two_runs_and_passes_errors_on(settings: Settings) -> None:
    services = make_services(settings, counts(85), io.StringIO())

    with pytest.raises(ValueError, match="At least 2 runs"):
        await run_consistency_check(services, VIEW_ID, runs=1)
    with pytest.raises(ViewNotApprovedError):
        await run_consistency_check(services, OTHER_VIEW_ID)


def test_report_properties() -> None:
    assert ConsistencyReport(2, [1, 1], None, "v").passed is True
    assert ConsistencyReport(2, [1, 2], 1, "v").matches_expected is False


def test_script_reads_arguments_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script()

    args = script.parse_args(["--view-id", VIEW_ID, "--filters", "[]", "--expected", "85"])
    assert (args.view_id, args.filters, args.expected, args.runs) == (VIEW_ID, [], 85, 10)

    monkeypatch.setenv("DATAVERSE_TEST_VIEW_ID", VIEW_ID)
    monkeypatch.setenv("DATAVERSE_TEST_FILTERS", '[{"column": "a", "operator": "null"}]')
    args = script.parse_args(["--runs", "3"])
    assert args.view_id == VIEW_ID
    assert args.filters == [{"column": "a", "operator": "null"}]
    assert args.expected is None
    assert args.runs == 3


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--view-id", VIEW_ID, "--filters", "not json"],
        ["--view-id", VIEW_ID, "--expected", "many"],
        ["--view-id", VIEW_ID, "--runs", "1"],
    ],
)
def test_script_rejects_bad_arguments(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        load_script().parse_args(argv)

    assert excinfo.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize(("values", "expected_exit"), [((85,), 0), ((100, 85, 90), 1)])
def test_script_exit_code_reports_pass_or_fail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_settings: Any,
    values: tuple[int, ...],
    expected_exit: int,
) -> None:
    script = load_script()
    services = make_services(make_settings(), counts(*values), io.StringIO())
    monkeypatch.setattr(script, "load_settings", lambda: services.settings)
    monkeypatch.setattr(script, "build_services", lambda _settings: services)

    exit_code = script.main(["--view-id", VIEW_ID, "--runs", "3"])

    assert exit_code == expected_exit
    assert ("RESULT: PASS" if expected_exit == 0 else "RESULT: FAIL") in capsys.readouterr().out


def test_script_reports_configuration_problems_plainly(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = load_script().main(["--view-id", VIEW_ID])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "FAILED (configuration_error)" in captured.err
    assert "Traceback" not in captured.err
    assert BASE_SETTINGS["client_secret"] not in captured.err
    assert sys.modules.get("consistency_check") is None

"""Acceptance test: ask count_view the same question several times and compare the answers.

Usage:
    uv run python scripts/consistency_check.py --view-id <view ID> \\
        --filters '[{"column": "officeid", "operator": "eq", "value": "<record ID>"}]' \\
        --expected <count shown in the app>

Every argument can also come from the environment (or .env):
    DATAVERSE_TEST_VIEW_ID, DATAVERSE_TEST_FILTERS (JSON), DATAVERSE_TEST_EXPECTED_COUNT

Exits 0 on PASS and 1 on FAIL. Prints the view name and the counts to this terminal only;
do not paste real names or figures into the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import anyio

from dataverse_views_mcp.acceptance import DEFAULT_RUNS, run_consistency_check
from dataverse_views_mcp.config import load_settings
from dataverse_views_mcp.errors import DataverseViewsError
from dataverse_views_mcp.services import build_services


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--view-id", default=os.environ.get("DATAVERSE_TEST_VIEW_ID"))
    parser.add_argument("--filters", default=os.environ.get("DATAVERSE_TEST_FILTERS", "[]"))
    parser.add_argument("--expected", default=os.environ.get("DATAVERSE_TEST_EXPECTED_COUNT"))
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    args = parser.parse_args(argv)
    if not args.view_id:
        parser.error("--view-id (or DATAVERSE_TEST_VIEW_ID) is required")
    try:
        args.filters = json.loads(args.filters)
    except ValueError:
        parser.error("--filters must be JSON, for example '[]'")
    try:
        args.expected = int(args.expected) if args.expected not in (None, "") else None
    except ValueError:
        parser.error("--expected must be a whole number")
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    return args


async def run(args: argparse.Namespace) -> int:
    try:
        services = build_services(load_settings())
        try:
            report = await run_consistency_check(
                services, args.view_id, args.filters, args.runs, args.expected
            )
        finally:
            await services.client.aclose()
    except DataverseViewsError as exc:
        print(f"FAILED ({exc.code}): {exc.message}", file=sys.stderr)
        return 1
    print(report.summary())
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return anyio.run(run, args)


if __name__ == "__main__":
    sys.exit(main())

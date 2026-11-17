"""The PRD's acceptance test: the same count question, asked repeatedly, gives one answer.

Calls count_view a number of times with the same view and filters, and reports whether
every result is identical and, when an expected value is given, equal to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dataverse_views_mcp.services import Services
from dataverse_views_mcp.tools.count_view import count_view

DEFAULT_RUNS = 10


@dataclass(frozen=True)
class ConsistencyReport:
    runs: int
    counts: list[int]
    expected: int | None
    view_name: str

    @property
    def consistent(self) -> bool:
        return len(set(self.counts)) == 1

    @property
    def matches_expected(self) -> bool | None:
        """None when no expected value was given."""
        if self.expected is None:
            return None
        return self.consistent and self.counts[0] == self.expected

    @property
    def passed(self) -> bool:
        return self.consistent and self.matches_expected is not False

    def summary(self) -> str:
        lines = [
            f"View: {self.view_name}",
            f"Runs: {self.runs}",
            f"Distinct results: {sorted(set(self.counts))}",
            f"All runs identical: {'yes' if self.consistent else 'NO'}",
        ]
        if self.expected is not None:
            lines.append(
                f"Equals expected count ({self.expected}): "
                f"{'yes' if self.matches_expected else 'NO'}"
            )
        else:
            lines.append("Expected count: not given, so only consistency was checked")
        lines.append("RESULT: PASS" if self.passed else "RESULT: FAIL")
        return "\n".join(lines)


async def run_consistency_check(
    services: Services,
    view_id: str,
    filters: Any = None,
    runs: int = DEFAULT_RUNS,
    expected: int | None = None,
) -> ConsistencyReport:
    if runs < 2:
        raise ValueError("At least 2 runs are needed to check consistency.")
    counts: list[int] = []
    view_name = ""
    for _ in range(runs):
        result = await count_view(services, view_id, filters)
        counts.append(int(result["count"]))
        view_name = str(result["view"]["name"])
    return ConsistencyReport(runs=runs, counts=counts, expected=expected, view_name=view_name)

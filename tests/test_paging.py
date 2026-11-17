from __future__ import annotations

import pytest

from dataverse_views_mcp.errors import InvalidInputError
from dataverse_views_mcp.paging import PageTokens, _encode, request_binding
from tests.conftest import VIEW_ID

BINDING = request_binding(VIEW_ID, [], 50)


def test_marker_round_trips() -> None:
    tokens = PageTokens()
    marker = tokens.issue(BINDING, 2, "<cookie page='1'/>")

    assert tokens.read(marker, BINDING) == (2, "<cookie page='1'/>")
    assert tokens.read(tokens.issue(BINDING, 3, None), BINDING) == (3, None)


def test_binding_changes_with_view_filters_or_page_size() -> None:
    flt = [{"column": "a", "operator": "eq", "value": 1}]

    assert request_binding(VIEW_ID, [], 50) == BINDING
    assert request_binding(VIEW_ID, flt, 50) != BINDING
    assert request_binding(VIEW_ID, [], 25) != BINDING
    assert request_binding("other", [], 50) != BINDING


def test_marker_cannot_be_reused_for_a_different_request() -> None:
    tokens = PageTokens()
    marker = tokens.issue(BINDING, 2, "c")

    with pytest.raises(InvalidInputError, match="not valid for this request"):
        tokens.read(marker, request_binding(VIEW_ID, [], 25))


def test_marker_from_another_server_run_is_refused() -> None:
    marker = PageTokens().issue(BINDING, 2, "c")

    with pytest.raises(InvalidInputError):
        PageTokens().read(marker, BINDING)


def test_altered_marker_is_refused() -> None:
    tokens = PageTokens()
    payload, _, signature = tokens.issue(BINDING, 2, "c").partition(".")
    forged_payload = _encode(b'{"b":"' + BINDING.encode() + b'","p":9,"c":"evil"}')

    for marker in (f"{forged_payload}.{signature}", f"{payload}.{signature[:-2]}AA", payload):
        with pytest.raises(InvalidInputError):
            tokens.read(marker, BINDING)


@pytest.mark.parametrize("marker", ["", ".", "not a marker", "a.b", "é.é", "x" * 20_000, "!!!.???"])
def test_garbage_markers_are_refused(marker: str) -> None:
    with pytest.raises(InvalidInputError):
        PageTokens().read(marker, BINDING)


def test_signed_marker_with_out_of_range_page_is_refused() -> None:
    tokens = PageTokens(key=b"k" * 32)

    for page in (0, -1, 10**9):
        with pytest.raises(InvalidInputError):
            tokens.read(tokens.issue(BINDING, page, None), BINDING)

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from dataverse_views_mcp.errors import InvalidInputError
from dataverse_views_mcp.paging import PageTokens, request_binding
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
    marker = tokens.issue(BINDING, 2, "c")
    flipped = marker[:-4] + ("AAAA" if not marker.endswith("AAAA") else "BBBB")

    for bad in (flipped, marker[:-10], marker + "x", marker[5:]):
        with pytest.raises(InvalidInputError):
            tokens.read(bad, BINDING)


def test_marker_contents_cannot_be_read_by_the_client() -> None:
    import base64

    cookie = "<cookie page='1'><name last='Sensitive Sort Value'/></cookie>"
    marker = PageTokens().issue(BINDING, 2, cookie)

    decoded = base64.urlsafe_b64decode(marker + "=" * (-len(marker) % 4))
    assert b"Sensitive Sort Value" not in decoded
    assert "Sensitive" not in marker
    assert BINDING.encode() not in decoded


@pytest.mark.parametrize("marker", ["", ".", "not a marker", "a.b", "é.é", "x" * 20_000, "!!!.???"])
def test_garbage_markers_are_refused(marker: str) -> None:
    with pytest.raises(InvalidInputError):
        PageTokens().read(marker, BINDING)


def test_signed_marker_with_out_of_range_page_is_refused() -> None:
    tokens = PageTokens(key=Fernet.generate_key())

    for page in (0, -1, 10**9):
        with pytest.raises(InvalidInputError):
            tokens.read(tokens.issue(BINDING, page, None), BINDING)

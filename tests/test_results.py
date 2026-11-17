from __future__ import annotations

from dataverse_views_mcp.results import clean_text


def test_removes_control_characters_but_keeps_line_breaks() -> None:
    assert clean_text("a\x00b\x1b[31mc\x7f\nd\te") == "ab[31mc\nd\te"


def test_caps_length_and_says_so() -> None:
    cleaned = clean_text("x" * 50, max_length=10)

    assert cleaned == "x" * 10 + "… [truncated]"
    assert clean_text("short", max_length=10) == "short"

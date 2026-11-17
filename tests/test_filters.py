from __future__ import annotations

from typing import Any

import pytest

from dataverse_views_mcp.errors import InvalidFilterError
from dataverse_views_mcp.filters import (
    MAX_FILTERS,
    Filter,
    Operator,
    check_filter_type,
    condition_parts,
    parse_filters,
)
from tests.conftest import RECORD_ID


def one(**item: Any) -> Filter:
    return parse_filters([item])[0]


def test_no_filters() -> None:
    assert parse_filters(None) == []
    assert parse_filters([]) == []


def test_parses_a_filter_on_the_main_table() -> None:
    flt = one(column="officeid", operator="eq", value=RECORD_ID)

    assert flt == Filter("officeid", Operator.EQ, RECORD_ID, None)
    assert flt.as_dict() == {"column": "officeid", "operator": "eq", "value": RECORD_ID}


def test_parses_a_filter_on_a_joined_table() -> None:
    flt = one(column="regionid", operator="in", value=[RECORD_ID], table="off")

    assert flt.table == "off"
    assert flt.value == (RECORD_ID,)
    assert flt.as_dict()["value"] == [RECORD_ID]
    assert flt.as_dict()["table"] == "off"


def test_null_operators_take_no_value() -> None:
    assert one(column="officeid", operator="null").value is None
    assert one(column="officeid", operator="not_null", value="").value is None
    assert "value" not in one(column="officeid", operator="null").as_dict()
    with pytest.raises(InvalidFilterError, match="does not take a value"):
        one(column="officeid", operator="null", value=1)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("statecode eq 0", "must be a list"),
        ([{"column": "a", "operator": "eq", "value": 1}] * (MAX_FILTERS + 1), "At most"),
        (["statecode eq 0"], "must be an object"),
        ([{"column": "a", "operator": "eq", "value": 1, "fetchxml": "<x/>"}], "only column"),
        ([{"operator": "eq", "value": 1}], "column must be"),
        ([{"column": "Name", "operator": "eq", "value": 1}], "column must be"),
        ([{"column": "a' or '1'='1", "operator": "eq", "value": 1}], "column must be"),
        ([{"column": 'a"/><x', "operator": "eq", "value": 1}], "column must be"),
        ([{"column": "a", "operator": "like", "value": "x"}], "operator must be one of"),
        ([{"column": "a", "operator": "eq-userid"}], "operator must be one of"),
        ([{"column": "a", "operator": 5, "value": 1}], "operator must be one of"),
        ([{"column": "a", "operator": "eq"}], "needs a single value"),
        ([{"column": "a", "operator": "eq", "value": [1]}], "needs a single value"),
        ([{"column": "a", "operator": "eq", "value": {"x": 1}}], "needs a single value"),
        ([{"column": "a", "operator": "in", "value": 1}], "needs a list"),
        ([{"column": "a", "operator": "in", "value": []}], "needs a list"),
        ([{"column": "a", "operator": "in", "value": list(range(51))}], "needs a list"),
        ([{"column": "a", "operator": "in", "value": [[1]]}], "must be text, a number"),
        ([{"column": "a", "operator": "eq", "value": ""}], "1 to 500 characters"),
        ([{"column": "a", "operator": "eq", "value": "x" * 501}], "1 to 500 characters"),
        ([{"column": "a", "operator": "eq", "value": "a\x00b"}], "control characters"),
        ([{"column": "a", "operator": "eq", "value": "a\nb"}], "control characters"),
        ([{"column": "a", "operator": "eq", "value": float("nan")}], "out of range"),
        ([{"column": "a", "operator": "eq", "value": float("inf")}], "out of range"),
        ([{"column": "a", "operator": "eq", "value": 10**30}], "out of range"),
        ([{"column": "a", "operator": "eq", "value": 1, "table": "o'ff"}], "table must be"),
        ([{"column": "a", "operator": "eq", "value": 1, "table": 3}], "table must be"),
    ],
)
def test_malformed_filters_are_rejected(raw: Any, message: str) -> None:
    with pytest.raises(InvalidFilterError, match=message):
        parse_filters(raw)


@pytest.mark.parametrize(
    ("column_type", "operator", "value"),
    [
        ("Lookup", "eq", RECORD_ID),
        ("Lookup", "in", [RECORD_ID, RECORD_ID.upper()]),
        ("Owner", "ne", RECORD_ID),
        ("Customer", "null", None),
        ("Uniqueidentifier", "eq", RECORD_ID),
        ("Picklist", "eq", 3),
        ("State", "in", [0, 1]),
        ("Integer", "ge", 10),
        ("Money", "lt", 99.5),
        ("Decimal", "eq", 2),
        ("Boolean", "eq", True),
        ("DateTime", "on_or_after", "2026-01-31"),
        ("DateTime", "lt", "2026-01-31T10:00:00Z"),
        ("String", "begins_with", "Nor"),
        ("String", "contains", "th"),
        ("Memo", "not_null", None),
    ],
)
def test_values_that_suit_their_column_type(column_type: str, operator: str, value: Any) -> None:
    item = {"column": "col", "operator": operator}
    if value is not None:
        item["value"] = value

    check_filter_type(one(**item), column_type)


@pytest.mark.parametrize(
    ("column_type", "operator", "value", "message"),
    [
        ("Lookup", "eq", "North Office", "record ID from find_record"),
        ("Lookup", "contains", "North", "does not suit"),
        ("Lookup", "eq", 5, "record ID from find_record"),
        ("Owner", "in", [RECORD_ID, "Pat"], "record ID from find_record"),
        ("Picklist", "eq", "Active", "whole number"),
        ("Integer", "eq", True, "whole number"),
        ("Integer", "eq", 1.5, "whole number"),
        ("Money", "eq", "ten", "must be a number"),
        ("Money", "eq", False, "must be a number"),
        ("Boolean", "eq", 1, "true or false"),
        ("Boolean", "gt", True, "does not suit"),
        ("DateTime", "on", "yesterday", "a date such as"),
        ("DateTime", "on", 20260131, "a date such as"),
        ("DateTime", "on", "20260131", "a date such as"),
        ("DateTime", "on", "2026-W05-1", "a date such as"),
        ("DateTime", "on", "2026-02-30", "a date such as"),
        ("DateTime", "on", "2026-01-31 10:00", "a date such as"),
        ("DateTime", "eq", "2026-01-31", "does not suit"),
        ("String", "gt", "a", "does not suit"),
        ("String", "eq", 5, "must be text"),
        ("Virtual", "eq", "x", "cannot be used in a filter"),
        ("", "eq", "x", "cannot be used in a filter"),
    ],
)
def test_values_that_do_not_suit_their_column_type(
    column_type: str, operator: str, value: Any, message: str
) -> None:
    flt = one(column="col", operator=operator, value=value)

    with pytest.raises(InvalidFilterError, match=message):
        check_filter_type(flt, column_type)


def test_condition_parts_for_each_operator() -> None:
    assert condition_parts(one(column="c", operator="eq", value="x")) == ("eq", "x", ())
    assert condition_parts(one(column="c", operator="ne", value=5)) == ("ne", "5", ())
    assert condition_parts(one(column="c", operator="ge", value=1.5)) == ("ge", "1.5", ())
    assert condition_parts(one(column="c", operator="eq", value=True)) == ("eq", "1", ())
    assert condition_parts(one(column="c", operator="eq", value=False)) == ("eq", "0", ())
    assert condition_parts(one(column="c", operator="null")) == ("null", None, ())
    assert condition_parts(one(column="c", operator="not_null")) == ("not-null", None, ())
    assert condition_parts(one(column="c", operator="in", value=[1, 2])) == ("in", None, ("1", "2"))
    assert condition_parts(one(column="c", operator="on", value="2026-01-31")) == (
        "on",
        "2026-01-31",
        (),
    )
    assert condition_parts(one(column="c", operator="on_or_before", value="2026-01-31"))[0] == (
        "on-or-before"
    )
    assert condition_parts(one(column="c", operator="eq", value=RECORD_ID.upper())) == (
        "eq",
        RECORD_ID,
        (),
    )


def test_text_search_treats_wildcards_in_caller_text_literally() -> None:
    assert condition_parts(one(column="c", operator="begins_with", value="50%_[x")) == (
        "like",
        "50[%][_][[]x%",
        (),
    )
    assert condition_parts(one(column="c", operator="contains", value="%")) == ("like", "%[%]%", ())


def test_numbers_are_written_in_plain_decimal_notation() -> None:
    assert condition_parts(one(column="c", operator="eq", value=1e17))[1] == "100000000000000000"
    assert condition_parts(one(column="c", operator="eq", value=1.5e-7))[1] == "0.00000015"
    assert condition_parts(one(column="c", operator="eq", value=10**17))[1] == "100000000000000000"

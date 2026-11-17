"""Structured filters: a column, an operator from a fixed list, and a value.

The model never writes query text. It supplies filters in this shape, and every part is
checked before it is added to a view's query:

1. Shape: known keys, a well-formed column name, an operator from the fixed list, and a
   value whose shape fits the operator.
2. Scope: the column must be one the view already shows or filters on (or one the
   administrator has explicitly allowed), so filters cannot be used to probe hidden columns.
3. Type: the value must match the column's type. A filter on a related record must use the
   record's ID, as returned by find_record, never free text.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from dataverse_views_mcp.config import GUID_RE, LOGICAL_NAME_RE
from dataverse_views_mcp.errors import InvalidFilterError

MAX_FILTERS = 10
MAX_IN_VALUES = 50
MAX_TEXT_LENGTH = 500
MAX_NUMBER = 10**18

_JOIN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_ALLOWED_KEYS = {"column", "operator", "value", "table"}

Scalar = str | int | float | bool


class Operator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    IN = "in"
    NULL = "null"
    NOT_NULL = "not_null"
    BEGINS_WITH = "begins_with"
    CONTAINS = "contains"
    ON = "on"
    ON_OR_AFTER = "on_or_after"
    ON_OR_BEFORE = "on_or_before"


OPERATOR_NAMES = ", ".join(op.value for op in Operator)

_NO_VALUE = {Operator.NULL, Operator.NOT_NULL}
_TEXT_ONLY = {Operator.BEGINS_WITH, Operator.CONTAINS}
_DATE_ONLY = {Operator.ON, Operator.ON_OR_AFTER, Operator.ON_OR_BEFORE}
_ORDERED = {Operator.GT, Operator.GE, Operator.LT, Operator.LE}
_EQUALITY = {Operator.EQ, Operator.NE, Operator.IN}

# Dataverse attribute types, grouped by the kind of value a filter must supply.
_ID_TYPES = {"Lookup", "Customer", "Owner", "Uniqueidentifier"}
_INTEGER_TYPES = {"Integer", "BigInt", "Picklist", "State", "Status"}
_NUMBER_TYPES = {"Decimal", "Double", "Money"}
_TEXT_TYPES = {"String", "Memo"}

_OPERATORS_BY_KIND: dict[str, set[Operator]] = {
    "id": _EQUALITY | _NO_VALUE,
    "integer": _EQUALITY | _ORDERED | _NO_VALUE,
    "number": _EQUALITY | _ORDERED | _NO_VALUE,
    "boolean": {Operator.EQ, Operator.NE} | _NO_VALUE,
    "date": _DATE_ONLY | _ORDERED | _NO_VALUE,
    "text": _EQUALITY | _TEXT_ONLY | _NO_VALUE,
}


@dataclass(frozen=True)
class Filter:
    column: str
    operator: Operator
    value: Scalar | tuple[Scalar, ...] | None = None
    table: str | None = None
    """Alias or name of a joined table in the view. None means the view's main table."""

    def as_dict(self) -> dict[str, Any]:
        """The filter as reported back to the caller and written to the audit log."""
        result: dict[str, Any] = {"column": self.column, "operator": self.operator.value}
        if self.value is not None:
            result["value"] = list(self.value) if isinstance(self.value, tuple) else self.value
        if self.table is not None:
            result["table"] = self.table
        return result


def _check_scalar(value: Any, position: str) -> Scalar:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or abs(value) > MAX_NUMBER:
            raise InvalidFilterError(f"{position}: the number is out of range.")
        return value
    if isinstance(value, str):
        if not value or len(value) > MAX_TEXT_LENGTH:
            raise InvalidFilterError(
                f"{position}: text values must be 1 to {MAX_TEXT_LENGTH} characters."
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise InvalidFilterError(f"{position}: the value contains control characters.")
        return value
    raise InvalidFilterError(f"{position}: the value must be text, a number, or true/false.")


def parse_filters(raw: Any) -> list[Filter]:
    """Check the shape of caller-supplied filters and return them as Filter objects."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InvalidFilterError("Filters must be a list.")
    if len(raw) > MAX_FILTERS:
        raise InvalidFilterError(f"At most {MAX_FILTERS} filters are allowed.")
    return [_parse_one(item, f"Filter {index}") for index, item in enumerate(raw, start=1)]


def _parse_one(item: Any, position: str) -> Filter:
    if not isinstance(item, dict):
        raise InvalidFilterError(
            f"{position}: each filter must be an object with column, operator, and value."
        )
    unknown = set(item) - _ALLOWED_KEYS
    if unknown:
        raise InvalidFilterError(
            f"{position}: only column, operator, value, and table are allowed."
        )
    column = item.get("column")
    if not isinstance(column, str) or not LOGICAL_NAME_RE.match(column):
        raise InvalidFilterError(
            f"{position}: the column must be a column logical name from describe_view."
        )
    operator_text = item.get("operator")
    try:
        operator = Operator(operator_text) if isinstance(operator_text, str) else None
    except ValueError:
        operator = None
    if operator is None:
        raise InvalidFilterError(f"{position}: the operator must be one of: {OPERATOR_NAMES}.")
    table = item.get("table")
    if table in (None, ""):
        table = None
    elif not isinstance(table, str) or not _JOIN_NAME_RE.match(table):
        raise InvalidFilterError(
            f"{position}: the table must be a joined table name from describe_view."
        )
    raw_value = item.get("value")
    value: Scalar | tuple[Scalar, ...] | None
    if operator in _NO_VALUE:
        if raw_value not in (None, ""):
            raise InvalidFilterError(f"{position}: '{operator.value}' does not take a value.")
        value = None
    elif operator is Operator.IN:
        if not isinstance(raw_value, list) or not 1 <= len(raw_value) <= MAX_IN_VALUES:
            raise InvalidFilterError(
                f"{position}: 'in' needs a list of 1 to {MAX_IN_VALUES} values."
            )
        value = tuple(_check_scalar(entry, position) for entry in raw_value)
    else:
        if raw_value is None or isinstance(raw_value, (list, dict)):
            raise InvalidFilterError(f"{position}: '{operator.value}' needs a single value.")
        value = _check_scalar(raw_value, position)
    return Filter(column=column, operator=operator, value=value, table=table)


def value_kind(column_type: str) -> str | None:
    """The kind of value a filter on this column type needs, or None if not filterable."""
    if column_type in _ID_TYPES:
        return "id"
    if column_type in _INTEGER_TYPES:
        return "integer"
    if column_type in _NUMBER_TYPES:
        return "number"
    if column_type == "Boolean":
        return "boolean"
    if column_type == "DateTime":
        return "date"
    if column_type in _TEXT_TYPES:
        return "text"
    return None


def _is_date(value: Scalar) -> bool:
    if not isinstance(value, str):
        return False
    for parser in (date.fromisoformat, datetime.fromisoformat):
        try:
            parser(value.replace("Z", "+00:00") if parser is datetime.fromisoformat else value)
        except ValueError:
            continue
        return True
    return False


def _value_fits(kind: str, value: Scalar) -> bool:
    if kind == "id":
        return isinstance(value, str) and bool(GUID_RE.match(value.lower()))
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "date":
        return _is_date(value)
    return isinstance(value, str)


EXPECTED_VALUE = {
    "id": "a record ID from find_record, not a name",
    "integer": "a whole number (for a choice column, the choice's number)",
    "number": "a number",
    "boolean": "true or false",
    "date": "a date such as 2026-01-31",
    "text": "text",
}


def check_filter_type(flt: Filter, column_type: str) -> None:
    """Raise unless the filter's operator and value suit the column's Dataverse type."""
    kind = value_kind(column_type)
    if kind is None:
        raise InvalidFilterError(f"The column '{flt.column}' cannot be used in a filter.")
    if flt.operator not in _OPERATORS_BY_KIND[kind]:
        allowed = ", ".join(sorted(op.value for op in _OPERATORS_BY_KIND[kind]))
        raise InvalidFilterError(
            f"The operator '{flt.operator.value}' does not suit the column '{flt.column}'. "
            f"Use one of: {allowed}."
        )
    if flt.value is None:
        return
    values = flt.value if isinstance(flt.value, tuple) else (flt.value,)
    if not all(_value_fits(kind, value) for value in values):
        raise InvalidFilterError(
            f"The value for the column '{flt.column}' must be {EXPECTED_VALUE[kind]}."
        )


def _escape_like(text: str) -> str:
    """Make wildcard characters in caller text match literally."""
    return text.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")


def _text(value: Scalar) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str) and GUID_RE.match(value.lower()):
        return value.lower()
    return str(value)


_FETCH_OPERATORS = {
    Operator.EQ: "eq",
    Operator.NE: "ne",
    Operator.GT: "gt",
    Operator.GE: "ge",
    Operator.LT: "lt",
    Operator.LE: "le",
    Operator.IN: "in",
    Operator.NULL: "null",
    Operator.NOT_NULL: "not-null",
    Operator.BEGINS_WITH: "like",
    Operator.CONTAINS: "like",
    Operator.ON: "on",
    Operator.ON_OR_AFTER: "on-or-after",
    Operator.ON_OR_BEFORE: "on-or-before",
}


def condition_parts(flt: Filter) -> tuple[str, str | None, tuple[str, ...]]:
    """Translate a filter to FetchXML: (operator, single value or None, list values)."""
    operator = _FETCH_OPERATORS[flt.operator]
    if flt.value is None:
        return operator, None, ()
    if isinstance(flt.value, tuple):
        return operator, None, tuple(_text(value) for value in flt.value)
    if flt.operator is Operator.BEGINS_WITH:
        return operator, _escape_like(str(flt.value)) + "%", ()
    if flt.operator is Operator.CONTAINS:
        return operator, "%" + _escape_like(str(flt.value)) + "%", ()
    return operator, _text(flt.value), ()

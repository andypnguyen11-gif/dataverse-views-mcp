"""Read a view's stored query (FetchXML) and change it safely.

The query is always handled as a parsed XML tree, never as text. Values are set as XML
attributes and text nodes, so the serializer escapes them and caller input cannot add
elements or attributes. The stored query itself is parsed with a hardened parser that
rejects DTDs and entities.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

# The standard library is used to build and serialize only; parsing uses defusedxml.
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from dataverse_views_mcp.config import LOGICAL_NAME_RE
from dataverse_views_mcp.errors import InvalidFilterError, UnsupportedViewError
from dataverse_views_mcp.filters import Filter, condition_parts

MAX_FETCHXML_LENGTH = 200_000
COUNT_ALIAS = "record_count"

# Operators whose meaning depends on who is running the query. Under a service identity
# they describe the service, not the person asking, so such views are flagged and refused.
USER_RELATIVE_OPERATORS = frozenset(
    {
        "eq-userid",
        "ne-userid",
        "eq-userteams",
        "eq-useroruserteams",
        "eq-useroruserhierarchy",
        "eq-useroruserhierarchyandteams",
        "eq-businessid",
        "ne-businessid",
        "eq-userlanguage",
    }
)

_PAGING_ATTRIBUTES = ("page", "count", "paging-cookie", "returntotalrecordcount")


@dataclass(frozen=True)
class Column:
    name: str
    table: str
    join: str | None
    """None for the main table; otherwise the joined table's alias (or name if no alias)."""
    alias: str | None = None

    @property
    def result_key(self) -> str:
        """The key this column has in a row returned by Dataverse."""
        if self.alias:
            return self.alias
        return self.name if self.join is None else f"{self.join}.{self.name}"


@dataclass(frozen=True)
class Join:
    table: str
    name: str
    """How filters refer to this joined table: its alias, or its table name if it has none."""
    from_column: str
    to_column: str
    kind: str
    parent: str | None


@dataclass(frozen=True)
class Sort:
    column: str
    descending: bool
    join: str | None


@dataclass
class Scope:
    """One table in the view (main or joined) and the columns the view uses from it."""

    table: str
    columns: set[str] = field(default_factory=set)
    all_columns: bool = False
    ambiguous: bool = False


@dataclass(frozen=True)
class ViewDescription:
    table: str
    columns: list[Column]
    filters: list[str]
    sort: list[Sort]
    joins: list[Join]
    user_relative_operators: list[str]
    distinct: bool

    @property
    def depends_on_caller(self) -> bool:
        return bool(self.user_relative_operators)


def parse(fetchxml: str) -> ET.Element:
    """Parse a stored view query and confirm it has the shape this server supports."""
    if len(fetchxml) > MAX_FETCHXML_LENGTH:
        raise UnsupportedViewError("This view's stored query is too large to use.")
    try:
        root: ET.Element = SafeET.fromstring(
            fetchxml, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ET.ParseError, DefusedXmlException, ValueError):
        raise UnsupportedViewError("This view's stored query could not be read.") from None
    if root.tag != "fetch":
        raise UnsupportedViewError("This view's stored query is not in the expected format.")
    entities = root.findall("entity")
    if len(entities) != 1 or not LOGICAL_NAME_RE.match(entities[0].get("name", "")):
        raise UnsupportedViewError("This view's stored query is not in the expected format.")
    if root.get("aggregate", "false").lower() == "true":
        raise UnsupportedViewError("This view already totals its rows, so it cannot be used.")
    if root.get("top") is not None:
        raise UnsupportedViewError(
            "This view is limited to a fixed number of top rows, so it cannot be used."
        )
    return root


def serialize(root: ET.Element) -> str:
    return ET.tostring(root, encoding="unicode")


def main_table(root: ET.Element) -> str:
    return _entity(root).get("name", "")


def _entity(root: ET.Element) -> ET.Element:
    entity = root.find("entity")
    if entity is None:  # pragma: no cover - parse() guarantees one entity
        raise UnsupportedViewError("This view's stored query is not in the expected format.")
    return entity


def _join_name(link: ET.Element) -> str:
    return link.get("alias") or link.get("name") or ""


def _walk_joins(parent: ET.Element, parent_name: str | None) -> list[tuple[ET.Element, str | None]]:
    found: list[tuple[ET.Element, str | None]] = []
    for link in parent.findall("link-entity"):
        found.append((link, parent_name))
        found.extend(_walk_joins(link, _join_name(link)))
    return found


def scopes(root: ET.Element) -> dict[str | None, Scope]:
    """The tables in the view and, for each, the columns the view shows or filters on.

    The key is None for the main table and the join's alias (or name) otherwise. These
    are the columns a caller may filter on.
    """
    entity = _entity(root)
    result: dict[str | None, Scope] = {None: Scope(table=entity.get("name", ""))}
    for link, _ in _walk_joins(entity, None):
        name = _join_name(link)
        if name in result:
            result[name].ambiguous = True
        else:
            result[name] = Scope(table=link.get("name", ""))

    def collect(node: ET.Element, key: str | None) -> None:
        scope = result[key]
        for child in node:
            if child.tag == "attribute" and child.get("name"):
                scope.columns.add(child.get("name", ""))
            elif child.tag == "all-attributes":
                scope.all_columns = True
            elif child.tag == "filter":
                for condition in child.iter("condition"):
                    target = condition.get("entityname") or key
                    column = condition.get("attribute")
                    if column and target in result:
                        result[target].columns.add(column)
            elif child.tag == "link-entity":
                collect(child, _join_name(child))

    collect(entity, None)
    return result


def _describe_condition(condition: ET.Element, join: str | None) -> str:
    target = condition.get("entityname") or join
    column = condition.get("attribute", "?")
    name = f"{target}.{column}" if target else column
    operator = condition.get("operator", "?")
    values = [value.text or "" for value in condition.findall("value")]
    if values:
        return f"{name} {operator} ({', '.join(values)})"
    if condition.get("value") is not None:
        return f"{name} {operator} {condition.get('value')}"
    return f"{name} {operator}"


def _describe_filter(node: ET.Element, join: str | None) -> str:
    joiner = " or " if node.get("type", "and").lower() == "or" else " and "
    parts: list[str] = []
    for child in node:
        if child.tag == "condition":
            parts.append(_describe_condition(child, join))
        elif child.tag == "filter":
            nested = _describe_filter(child, join)
            if nested:
                parts.append(f"({nested})")
    return joiner.join(parts)


def describe(root: ET.Element) -> ViewDescription:
    """Describe a view's query in plain terms."""
    entity = _entity(root)
    table = entity.get("name", "")
    columns: list[Column] = []
    filters: list[str] = []
    sort: list[Sort] = []
    joins: list[Join] = []

    def visit(node: ET.Element, node_table: str, join: str | None) -> None:
        for child in node:
            if child.tag == "attribute" and child.get("name"):
                columns.append(Column(child.get("name", ""), node_table, join, child.get("alias")))
            elif child.tag == "filter":
                text = _describe_filter(child, join)
                if text:
                    filters.append(text)
            elif child.tag == "order" and child.get("attribute"):
                sort.append(
                    Sort(
                        child.get("attribute", ""),
                        child.get("descending", "false").lower() == "true",
                        join,
                    )
                )

    visit(entity, table, None)
    for link, parent_name in _walk_joins(entity, None):
        name = _join_name(link)
        joins.append(
            Join(
                table=link.get("name", ""),
                name=name,
                from_column=link.get("from", ""),
                to_column=link.get("to", ""),
                kind=link.get("link-type", "inner"),
                parent=parent_name,
            )
        )
        visit(link, link.get("name", ""), name)

    relative = sorted(
        {
            condition.get("operator", "").lower()
            for condition in root.iter("condition")
            if condition.get("operator", "").lower() in USER_RELATIVE_OPERATORS
        }
    )
    return ViewDescription(
        table=table,
        columns=columns,
        filters=filters,
        sort=sort,
        joins=joins,
        user_relative_operators=relative,
        distinct=root.get("distinct", "false").lower() == "true",
    )


def apply_filters(root: ET.Element, filters: list[Filter]) -> ET.Element:
    """Return a copy of the query with the filters added, all of which must match.

    The view's own filters are kept and wrapped together with the new conditions in one
    "and" group on the main table. A condition on a joined table names that table, so it
    narrows the rows returned even when the join is an outer join.
    """
    result = copy.deepcopy(root)
    if not filters:
        return result
    known = scopes(result)
    entity = _entity(result)
    existing = entity.findall("filter")
    group = ET.Element("filter", {"type": "and"})
    for old in existing:
        entity.remove(old)
        group.append(old)
    for flt in filters:
        scope = known.get(flt.table)
        if scope is None or scope.ambiguous:
            raise InvalidFilterError(
                f"The view has no joined table named '{flt.table}' that can be filtered."
            )
        operator, value, values = condition_parts(flt)
        attributes = {"attribute": flt.column, "operator": operator}
        if flt.table is not None:
            attributes["entityname"] = flt.table
        if value is not None:
            attributes["value"] = value
        condition = ET.SubElement(group, "condition", attributes)
        for item in values:
            ET.SubElement(condition, "value").text = item
    entity.append(group)
    return result


def _strip(node: ET.Element, tags: set[str]) -> None:
    for child in list(node):
        if child.tag in tags:
            node.remove(child)
        else:
            _strip(child, tags)


def to_count(root: ET.Element, primary_id: str) -> ET.Element:
    """Return a copy of the query that counts distinct records of the main table.

    Counting distinct record IDs means a join that matches several related rows cannot
    inflate the total. Dataverse computes the number; no rows are returned.
    """
    result = copy.deepcopy(root)
    for name in (*_PAGING_ATTRIBUTES, "distinct"):
        result.attrib.pop(name, None)
    result.set("aggregate", "true")
    _strip(result, {"attribute", "all-attributes", "order"})
    counter = ET.Element(
        "attribute",
        {"name": primary_id, "alias": COUNT_ALIAS, "aggregate": "countcolumn", "distinct": "true"},
    )
    _entity(result).insert(0, counter)
    return result


def to_page(
    root: ET.Element, primary_id: str, page: int, page_size: int, cookie: str | None
) -> ET.Element:
    """Return a copy of the query that fetches one page of rows.

    The record ID is added as a final sort so the order is the same on every run, which
    paging needs to return every row exactly once.
    """
    result = copy.deepcopy(root)
    for name in _PAGING_ATTRIBUTES:
        result.attrib.pop(name, None)
    result.set("page", str(page))
    result.set("count", str(page_size))
    if cookie:
        result.set("paging-cookie", cookie)
    entity = _entity(result)
    if not any(child.get("name") == primary_id for child in entity.findall("attribute")):
        entity.insert(0, ET.Element("attribute", {"name": primary_id}))
    if not any(order.get("attribute") == primary_id for order in entity.findall("order")):
        orders = entity.findall("order")
        position = list(entity).index(orders[-1]) + 1 if orders else len(entity)
        entity.insert(position, ET.Element("order", {"attribute": primary_id}))
    return result

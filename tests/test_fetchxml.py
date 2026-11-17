from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from dataverse_views_mcp import fetchxml
from dataverse_views_mcp.errors import InvalidFilterError, UnsupportedViewError
from dataverse_views_mcp.filters import parse_filters
from tests.conftest import RECORD_ID, read_fixture

SIMPLE = read_fixture("view_simple.xml")
JOINED = read_fixture("view_joined.xml")
USER_RELATIVE = read_fixture("view_user_relative.xml")
ALL_FIXTURES = [SIMPLE, JOINED, USER_RELATIVE]


def reparse(root: ET.Element) -> ET.Element:
    """Serialize and parse again, as Dataverse would receive it."""
    return fetchxml.parse(fetchxml.serialize(root))


# --- parse ---------------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_FIXTURES)
def test_parses_every_fixture(source: str) -> None:
    root = fetchxml.parse(source)

    assert root.tag == "fetch"
    assert fetchxml.main_table(root) == "thing"


@pytest.mark.parametrize(
    "source",
    [
        "not xml at all",
        "<fetch><entity name='thing'>",
        "<query><entity name='thing'/></query>",
        "<fetch/>",
        "<fetch><entity name='a'/><entity name='b'/></fetch>",
        "<fetch><entity name='Bad Name'/></fetch>",
        "<fetch aggregate='true'><entity name='thing'/></fetch>",
        "<fetch top='10'><entity name='thing'/></fetch>",
        "<fetch><entity name='thing'/></fetch>" + " " * 200_001,
    ],
)
def test_rejects_queries_it_cannot_run_safely(source: str) -> None:
    with pytest.raises(UnsupportedViewError):
        fetchxml.parse(source)


def test_rejects_entity_expansion_and_external_entities() -> None:
    bomb = (
        '<!DOCTYPE fetch [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        "<fetch><entity name='thing'><attribute name='&b;'/></entity></fetch>"
    )
    external = (
        '<!DOCTYPE fetch [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<fetch><entity name='thing'><attribute name='&xxe;'/></entity></fetch>"
    )
    for source in (bomb, external):
        with pytest.raises(UnsupportedViewError):
            fetchxml.parse(source)


# --- describe ------------------------------------------------------------------------------


def test_describes_a_simple_view() -> None:
    description = fetchxml.describe(fetchxml.parse(SIMPLE))

    assert description.table == "thing"
    assert [c.name for c in description.columns] == ["name", "officeid", "statecode", "createdon"]
    assert all(c.join is None and c.table == "thing" for c in description.columns)
    assert description.filters == ["statecode eq 0"]
    assert [(s.column, s.descending, s.join) for s in description.sort] == [("name", False, None)]
    assert description.joins == []
    assert description.depends_on_caller is False
    assert description.distinct is False


def test_describes_a_joined_view() -> None:
    description = fetchxml.describe(fetchxml.parse(JOINED))

    assert [(c.join, c.name, c.result_key) for c in description.columns] == [
        (None, "name", "name"),
        (None, "createdon", "createdon"),
        ("off", "name", "off.name"),
        ("off", "regionid", "off.regionid"),
        ("nt", "subject", "nt.subject"),
    ]
    assert description.filters == [
        "statecode eq 0 and (quantity gt 5 or isurgent eq 1)",
        "nt.category in (1, 2)",
    ]
    assert [(j.table, j.name, j.from_column, j.to_column, j.kind) for j in description.joins] == [
        ("office", "off", "officeid", "officeid", "outer"),
        ("note", "nt", "thingid", "thingid", "inner"),
    ]
    assert description.sort[0].descending is True


def test_flags_a_view_that_depends_on_who_is_asking() -> None:
    description = fetchxml.describe(fetchxml.parse(USER_RELATIVE))

    assert description.depends_on_caller is True
    assert description.user_relative_operators == ["eq-businessid", "eq-userid"]


@pytest.mark.parametrize("operator", sorted(fetchxml.USER_RELATIVE_OPERATORS))
def test_every_caller_dependent_operator_is_detected(operator: str) -> None:
    source = (
        "<fetch><entity name='thing'><link-entity name='o' from='a' to='b'><filter>"
        f"<condition attribute='x' operator='{operator.upper()}'/>"
        "</filter></link-entity></entity></fetch>"
    )

    assert fetchxml.describe(fetchxml.parse(source)).user_relative_operators == [operator]


def test_describes_nested_joins_aliases_and_unusual_shapes() -> None:
    source = """
    <fetch distinct="true"><entity name="thing">
      <attribute name="name" alias="title"/>
      <filter><condition attribute="x" operator="null"/></filter>
      <filter type="or"/>
      <link-entity name="office" from="officeid" to="officeid">
        <link-entity name="region" from="regionid" to="regionid" alias="reg">
          <attribute name="name"/>
          <order attribute="name"/>
        </link-entity>
      </link-entity>
    </entity></fetch>"""
    description = fetchxml.describe(fetchxml.parse(source))

    assert description.distinct is True
    assert description.columns[0].result_key == "title"
    assert description.filters == ["x null"]
    assert [(j.name, j.parent, j.kind) for j in description.joins] == [
        ("office", None, "inner"),
        ("reg", "office", "inner"),
    ]
    assert [(s.column, s.join) for s in description.sort] == [("name", "reg")]


# --- scopes --------------------------------------------------------------------------------


def test_scopes_list_the_columns_each_table_shows_or_filters_on() -> None:
    known = fetchxml.scopes(fetchxml.parse(JOINED))

    assert set(known) == {None, "off", "nt"}
    assert known[None].table == "thing"
    assert known[None].columns == {"name", "createdon", "statecode", "quantity", "isurgent"}
    assert known["off"].table == "office"
    assert known["off"].columns == {"name", "regionid"}
    assert known["nt"].columns == {"subject", "category"}


def test_scopes_handle_all_columns_cross_table_conditions_and_duplicate_joins() -> None:
    source = """
    <fetch><entity name="thing">
      <all-attributes/>
      <filter><condition entityname="off" attribute="city" operator="eq" value="x"/></filter>
      <link-entity name="office" from="officeid" to="officeid" alias="off"/>
      <link-entity name="note" from="thingid" to="thingid"/>
      <link-entity name="note" from="thingid" to="otherid"/>
    </entity></fetch>"""
    known = fetchxml.scopes(fetchxml.parse(source))

    assert known[None].all_columns is True
    assert known["off"].columns == {"city"}
    assert known["note"].ambiguous is True


# --- apply_filters -------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_FIXTURES)
def test_no_filters_leaves_the_query_unchanged(source: str) -> None:
    root = fetchxml.parse(source)

    assert fetchxml.serialize(fetchxml.apply_filters(root, [])) == fetchxml.serialize(root)


@pytest.mark.parametrize("source", ALL_FIXTURES)
def test_added_filter_is_anded_with_the_views_own_filters(source: str) -> None:
    root = fetchxml.parse(source)
    before = fetchxml.serialize(root)
    filters = parse_filters([{"column": "officeid", "operator": "eq", "value": RECORD_ID}])

    result = reparse(fetchxml.apply_filters(root, filters))

    entity = result.find("entity")
    assert entity is not None
    groups = entity.findall("filter")
    assert len(groups) == 1
    assert groups[0].get("type") == "and"
    added = groups[0].findall("condition")[-1]
    assert added.attrib == {"attribute": "officeid", "operator": "eq", "value": RECORD_ID}
    original_conditions = sum(1 for _ in fetchxml.parse(source).find("entity").iter("condition"))  # type: ignore[union-attr]
    assert sum(1 for _ in entity.iter("condition")) == original_conditions + 1
    assert fetchxml.serialize(root) == before, "the original query must not be changed"


def test_filter_on_a_joined_table_names_that_table() -> None:
    root = fetchxml.parse(JOINED)
    filters = parse_filters(
        [{"column": "regionid", "operator": "in", "value": [RECORD_ID], "table": "off"}]
    )

    result = reparse(fetchxml.apply_filters(root, filters))

    added = result.find("entity/filter").findall("condition")[-1]  # type: ignore[union-attr]
    assert added.attrib == {"attribute": "regionid", "operator": "in", "entityname": "off"}
    assert [value.text for value in added.findall("value")] == [RECORD_ID]
    assert "nt.category in (1, 2)" in fetchxml.describe(result).filters


def test_filter_on_an_unknown_or_ambiguous_joined_table_is_rejected() -> None:
    root = fetchxml.parse(JOINED)
    with pytest.raises(InvalidFilterError, match="no joined table named 'nope'"):
        fetchxml.apply_filters(
            root, parse_filters([{"column": "a", "operator": "null", "table": "nope"}])
        )

    twice = fetchxml.parse(
        "<fetch><entity name='thing'><link-entity name='note' from='a' to='b'/>"
        "<link-entity name='note' from='a' to='c'/></entity></fetch>"
    )
    with pytest.raises(InvalidFilterError):
        fetchxml.apply_filters(
            twice, parse_filters([{"column": "a", "operator": "null", "table": "note"}])
        )


@pytest.mark.parametrize(
    "hostile",
    [
        '"/><condition attribute="secret" operator="not-null"/><x y="',
        "</filter></entity><entity name='systemuser'>",
        "x' or 1=1 --",
        "<![CDATA[x]]>",
        "&lt;fetch&gt; &amp; &#x41;",
    ],
)
def test_hostile_values_stay_values_and_cannot_change_the_query(hostile: str) -> None:
    root = fetchxml.parse(SIMPLE)
    filters = parse_filters(
        [
            {"column": "name", "operator": "eq", "value": hostile},
            {"column": "name", "operator": "in", "value": [hostile]},
        ]
    )

    result = reparse(fetchxml.apply_filters(root, filters))

    assert [e.tag for e in result.iter()].count("entity") == 1
    conditions = list(result.iter("condition"))
    assert len(conditions) == 3
    assert conditions[1].get("value") == hostile
    assert conditions[2].find("value").text == hostile  # type: ignore[union-attr]
    assert {c.get("attribute") for c in conditions} == {"statecode", "name"}


# --- to_count ------------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_FIXTURES)
def test_count_query_counts_distinct_records_and_returns_no_columns(source: str) -> None:
    root = fetchxml.parse(source)
    conditions_before = [c.attrib for c in root.iter("condition")]

    result = fetchxml.to_count(root, "thingid")

    assert result.get("aggregate") == "true"
    assert result.get("distinct") is None
    attributes = list(result.iter("attribute"))
    assert [a.attrib for a in attributes] == [
        {
            "name": "thingid",
            "alias": "record_count",
            "aggregate": "countcolumn",
            "distinct": "true",
        }
    ]
    assert list(result.iter("order")) == []
    assert [c.attrib for c in result.iter("condition")] == conditions_before
    assert len(list(result.iter("link-entity"))) == len(list(root.iter("link-entity")))
    assert root.get("aggregate") is None, "the original query must not be changed"


def test_joined_view_produces_a_distinct_count_that_joins_cannot_inflate() -> None:
    result = fetchxml.to_count(fetchxml.parse(JOINED), "thingid")

    counter = result.find("entity/attribute")
    assert counter is not None
    assert counter.get("distinct") == "true"
    assert counter.get("aggregate") == "countcolumn"
    links = result.findall("entity/link-entity")
    assert [link.get("alias") for link in links] == ["off", "nt"]
    assert all(link.find("attribute") is None for link in links)
    assert links[1].find("filter/condition") is not None


def test_count_query_drops_paging_and_all_columns() -> None:
    source = (
        "<fetch page='3' count='50' paging-cookie='x' distinct='true'>"
        "<entity name='thing'><all-attributes/></entity></fetch>"
    )

    result = fetchxml.to_count(fetchxml.parse(source), "thingid")

    assert result.attrib == {"aggregate": "true"}
    assert result.find("entity/all-attributes") is None


def test_count_query_is_identical_on_every_run() -> None:
    filters = parse_filters([{"column": "officeid", "operator": "eq", "value": RECORD_ID}])
    outputs = {
        fetchxml.serialize(
            fetchxml.to_count(fetchxml.apply_filters(fetchxml.parse(JOINED), filters), "thingid")
        )
        for _ in range(10)
    }

    assert len(outputs) == 1


# --- to_page -------------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_FIXTURES)
def test_page_query_sets_paging_and_a_stable_order(source: str) -> None:
    root = fetchxml.parse(source)

    result = fetchxml.to_page(root, "thingid", page=2, page_size=25, cookie="<cookie page='1'/>")

    assert result.get("page") == "2"
    assert result.get("count") == "25"
    assert result.get("paging-cookie") == "<cookie page='1'/>"
    entity = result.find("entity")
    assert entity is not None
    assert entity.findall("order")[-1].attrib == {"attribute": "thingid"}
    assert "thingid" in [a.get("name") for a in entity.findall("attribute")]
    assert reparse(result).get("paging-cookie") == "<cookie page='1'/>"
    assert root.get("page") is None, "the original query must not be changed"


def test_first_page_has_no_cookie_and_keeps_the_views_sort_first() -> None:
    result = fetchxml.to_page(fetchxml.parse(SIMPLE), "thingid", 1, 50, None)

    assert result.get("paging-cookie") is None
    orders = result.findall("entity/order")
    assert [o.get("attribute") for o in orders] == ["name", "thingid"]


def test_page_query_does_not_duplicate_an_existing_id_column_or_sort() -> None:
    source = (
        "<fetch count='5000' page='9'><entity name='thing'><attribute name='thingid'/>"
        "<order attribute='thingid' descending='true'/></entity></fetch>"
    )

    result = fetchxml.to_page(fetchxml.parse(source), "thingid", 1, 10, None)

    assert len(result.findall("entity/attribute")) == 1
    assert len(result.findall("entity/order")) == 1
    assert result.get("count") == "10"
    assert result.get("page") == "1"

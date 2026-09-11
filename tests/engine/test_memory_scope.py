from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import Text, column, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY

from src.engine.components.store.scope import scope_predicate, tag_predicate
from src.engine.interface import MemoryScope, RecallRequest
from src.engine.scope import (
    TagFilter,
    TagGroup,
    parse_tag_expression,
    request_tag_filter,
)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("any", [True, True, True, True, True]),
        ("all", [True, True, False, False, True]),
        ("any_strict", [False, False, True, True, True]),
        ("all_strict", [False, False, False, False, True]),
        ("exact", [False, False, False, False, True]),
    ],
)
def test_two_tag_truth_table(mode, expected):
    actual = [None, [], ["x"], ["y"], ["y", "x", "x"]]
    assert [TagFilter(("x", "y"), mode).matches(tags) for tags in actual] == expected


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("any", [True, False]),
        ("all", [True, True]),
        ("any_strict", [False, False]),
        ("all_strict", [False, True]),
        ("exact", [True, False]),
    ],
)
def test_empty_group_truth_table_and_request_absence(mode, expected):
    assert [TagFilter((), mode).matches(tags) for tags in ([], ["x"])] == expected
    assert (request_tag_filter((), mode) is None) == (mode != "exact")


def test_nested_filters_cannot_expand_trusted_scope():
    trusted = MemoryScope("a", TagFilter(("user:1",), "all_strict"))
    request = parse_tag_expression(
        {
            "or": [
                {"tags": ["user:2"]},
                {"not": {"tags": ["secret"], "match": "any_strict"}},
            ]
        }
    )
    scope = trusted.narrowed(request)
    assert scope.permits("a", ["user:1"])
    assert not scope.permits("a", ["user:2"])
    assert not scope.permits("a", [])
    assert not scope.permits("b", ["user:1"])


def test_default_and_observation_scope_are_independent_of_session_tags():
    assert RecallRequest("question").query == "question"
    assert MemoryScope().bank_id == "default-team"
    source_tags = ["user:1", "session:s1"]
    scope = MemoryScope(observation_scopes=(("user:1",), ("user:1",)))
    assert scope.observation_scopes == (("user:1",),)
    assert source_tags == ["user:1", "session:s1"]
    with pytest.raises(FrozenInstanceError):
        scope.bank_id = "other"


@pytest.mark.parametrize(
    "value",
    [
        {"tags": ["x"], "match": "invalid"},
        {"tags": "x"},
        {"tags": [""]},
        {"or": []},
        {"and": [], "not": {}},
        {"not": {"tags": [], "surprise": True}},
    ],
)
def test_bad_filters_fail_closed(value):
    with pytest.raises(ValueError):
        parse_tag_expression(value)


def test_sql_is_parameterized_and_bank_constraint_survives_not():
    tags = column("tags", ARRAY(Text))
    bank = column("bank_id", Text)
    hostile_tag = "x'); DROP TABLE documents; --"
    scope = MemoryScope("a", TagGroup("not", (TagFilter((hostile_tag,), "exact"),)))
    compiled = (
        select(bank)
        .where(scope_predicate(bank, tags, scope))
        .compile(dialect=postgresql.dialect())
    )
    assert hostile_tag not in str(compiled)
    assert [hostile_tag] in compiled.params.values()
    assert "bank_id =" in str(compiled)
    assert "coalesce" in str(tag_predicate(tags, TagFilter((), "exact")))

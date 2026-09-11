"""Parameterized PostgreSQL predicates for the shared tag contract."""

from sqlalchemy import and_, false, func, not_, or_, true

from src.engine.scope import MemoryScope, TagExpression, TagFilter


def tag_predicate(column, expression: TagExpression | None):
    if expression is None:
        return true()
    if isinstance(expression, TagFilter):
        # NULL legacy arrays are semantically empty; total booleans are required
        # so NOT(empty/strict) behaves the same in SQL and Python.
        empty = func.coalesce(func.cardinality(column), 0) == 0
        tags = list(expression.tags)
        if expression.match == "exact":
            return (
                empty
                if not tags
                else and_(not_(empty), column.contains(tags), column.contained_by(tags))
            )
        overlap = (
            (column.overlap(tags) if tags else false())
            if expression.match in {"any", "any_strict"}
            else column.contains(tags)
        )
        overlap = func.coalesce(overlap, false())
        if expression.match in {"any", "all"}:
            return or_(empty, overlap)
        return and_(not_(empty), overlap)
    children = [tag_predicate(column, child) for child in expression.filters]
    if expression.operator == "not":
        return not_(children[0])
    return and_(*children) if expression.operator == "and" else or_(*children)


def scope_predicate(bank_column, tag_column, scope: MemoryScope):
    return and_(
        bank_column == scope.bank_id, tag_predicate(tag_column, scope.visibility)
    )

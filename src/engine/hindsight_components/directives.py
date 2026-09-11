"""Trusted, scope-bound reflection directives."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select

from src.engine.components.store.scope import scope_predicate
from src.engine.scope import MemoryScope

from .models import MemoryDirective


@dataclass(frozen=True, slots=True)
class DirectiveDefinition:
    id: str
    name: str
    content: str
    trigger: str | None = None
    priority: int = 0
    is_active: bool = True
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip() or not self.content.strip():
            raise ValueError("directive id, name, and content are required")
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))


@dataclass(frozen=True, slots=True)
class DirectiveView:
    id: str
    name: str
    content: str
    trigger: str | None
    priority: int
    is_active: bool
    tags: tuple[str, ...]


class PostgresDirectiveRepository:
    def __init__(self, session_factory=None, *, scope: MemoryScope | None = None):
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory
        self.scope = scope or MemoryScope()

    def with_scope(self, scope: MemoryScope):
        return PostgresDirectiveRepository(self._session_factory, scope=scope)

    def _visible(self):
        return scope_predicate(
            MemoryDirective.bank_id, MemoryDirective.tags, self.scope
        )

    @staticmethod
    def _view(row: MemoryDirective) -> DirectiveView:
        return DirectiveView(
            id=row.id,
            name=row.name,
            content=row.content,
            trigger=row.trigger,
            priority=row.priority,
            is_active=row.is_active,
            tags=tuple(row.tags),
        )

    async def create(self, definition: DirectiveDefinition) -> DirectiveView:
        if not self.scope.permits(self.scope.bank_id, definition.tags):
            raise PermissionError("directive tags exceed trusted scope")
        row = MemoryDirective(
            id=definition.id,
            bank_id=self.scope.bank_id,
            name=definition.name,
            content=definition.content,
            trigger=definition.trigger,
            priority=definition.priority,
            is_active=definition.is_active,
            tags=list(definition.tags),
        )
        async with self._session_factory() as session, session.begin():
            session.add(row)
        return self._view(row)

    async def get(self, directive_id: str) -> DirectiveView | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(MemoryDirective).where(
                    MemoryDirective.id == directive_id, self._visible()
                )
            )
            return self._view(row) if row else None

    async def list(self) -> list[DirectiveView]:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(MemoryDirective)
                    .where(self._visible())
                    .order_by(MemoryDirective.priority.desc(), MemoryDirective.id)
                )
            )
            return [self._view(row) for row in rows]

    async def matching(self, query: str) -> list[DirectiveView]:
        return [
            row
            for row in await self.list()
            if row.is_active
            and (not row.trigger or row.trigger.casefold() in query.casefold())
        ]

    async def update(
        self, directive_id: str, definition: DirectiveDefinition
    ) -> DirectiveView:
        if definition.id != directive_id:
            raise ValueError("directive id cannot change")
        if not self.scope.permits(self.scope.bank_id, definition.tags):
            raise PermissionError("directive tags exceed trusted scope")
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(MemoryDirective)
                    .where(MemoryDirective.id == directive_id, self._visible())
                    .with_for_update()
                )
                if row is None:
                    raise KeyError(directive_id)
                row.name = definition.name
                row.content = definition.content
                row.trigger = definition.trigger
                row.priority = definition.priority
                row.is_active = definition.is_active
                row.tags = list(definition.tags)
            return self._view(row)

    async def delete(self, directive_id: str) -> bool:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                delete(MemoryDirective).where(
                    MemoryDirective.id == directive_id, self._visible()
                )
            )
            return bool(result.rowcount)

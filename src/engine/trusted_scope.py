"""Resolve server-configured credentials; never accept bank authority from tools."""

from hashlib import sha256
from pydantic import BaseModel, ConfigDict, model_validator

from src.engine.scope import MemoryScope, parse_tag_expression


class ScopeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bank_id: str = "default-team"
    visibility: dict | None = None
    write_tags: tuple[str, ...] = ()
    subject_id: str | None = None
    agent_name: str | None = None
    policy_version: int = 1
    observation_scopes: tuple[tuple[str, ...], ...] = ()

    def scope(self) -> MemoryScope:
        return MemoryScope(
            bank_id=self.bank_id,
            visibility=parse_tag_expression(self.visibility)
            if self.visibility is not None
            else None,
            subject_id=self.subject_id,
            agent_name=self.agent_name,
            policy_version=self.policy_version,
            observation_scopes=self.observation_scopes,
        )

    @model_validator(mode="after")
    def validate_binding(self):
        from src.engine.scope import TagFilter

        TagFilter(self.write_tags)
        if not self.scope().permits(self.bank_id, self.write_tags):
            raise ValueError("write tags must be visible in the configured scope")
        return self


def resolve_binding(
    headers, *, enabled: bool, bindings: dict[str, ScopeBinding]
) -> ScopeBinding:
    token = headers.get("x-tkb-scope-token")
    if token is None:
        return ScopeBinding()
    if not enabled or not token or len(token) > 4096:
        raise PermissionError("scope credential is not authorized")
    binding = bindings.get(sha256(token.encode()).hexdigest())
    if binding is None:
        raise PermissionError("scope credential is not authorized")
    return binding


def bind_service(service, binding: ScopeBinding, *, writes: bool = False):
    if service is None or binding == ScopeBinding():
        return service
    # An older implementation must not silently fall back to shared data.
    if not hasattr(service, "with_scope"):
        raise PermissionError("service does not support isolated scopes")
    return service.with_scope(
        binding.scope(), **({"write_tags": binding.write_tags} if writes else {})
    )

from hashlib import sha256
from types import SimpleNamespace

import pytest

from src.engine.trusted_scope import ScopeBinding, resolve_binding, bind_service


def test_credentials_are_required_for_nondefault_authority():
    binding = ScopeBinding(
        bank_id="A",
        visibility={"tags": ["user:1"], "match": "all_strict"},
        write_tags=("user:1",),
    )
    bindings = {sha256(b"secret-A").hexdigest(): binding}
    assert (
        resolve_binding(
            {"bank_id": "A", "tags": "user:1"}, enabled=True, bindings=bindings
        )
        == ScopeBinding()
    )
    assert (
        resolve_binding(
            {"x-tkb-scope-token": "secret-A"}, enabled=True, bindings=bindings
        )
        == binding
    )
    for token, enabled in (("wrong", True), ("", True), ("secret-A", False)):
        with pytest.raises(PermissionError):
            resolve_binding(
                {"x-tkb-scope-token": token}, enabled=enabled, bindings=bindings
            )
    with pytest.raises(PermissionError, match="does not support"):
        bind_service(SimpleNamespace(), binding)


def test_invalid_server_visibility_and_write_scope_are_rejected():
    with pytest.raises(ValueError):
        ScopeBinding(
            bank_id="A", visibility={"tags": ["private"], "match": "all_strict"}
        )
    with pytest.raises(ValueError):
        ScopeBinding(visibility={"tags": [], "match": "invented"})


def test_mcp_binds_each_request_instead_of_reusing_session_authority(monkeypatch):
    from src.agent.tkb.mcp import server
    from config.schema import AppConfig
    from config import schema
    from config.settings import settings

    config = AppConfig.model_validate(
        {"engine": {"memory": {"enabled": True, "features": {"scope": True}}}}
    )
    monkeypatch.setattr(schema, "load_config", lambda *_: config)
    monkeypatch.setattr(
        settings,
        "memory_scope_bindings",
        {sha256(b"a").hexdigest(): ScopeBinding(bank_id="A")},
    )
    headers = {"x-tkb-scope-token": "a"}
    request = SimpleNamespace(headers=headers)
    monkeypatch.setattr(
        server.mcp,
        "get_context",
        lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)),
    )
    assert server._request_binding().bank_id == "A"
    headers.clear()
    assert server._request_binding().bank_id == "default-team"
    headers["x-tkb-scope-token"] = "forged"
    with pytest.raises(PermissionError):
        server._request_binding()

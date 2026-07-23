from types import SimpleNamespace

import pytest

from src.api import openwebui_server
from src.api.context import RequestPrincipal, get_forwarded_ollama_username


def test_official_openwebui_identity_wins_over_custom_header() -> None:
    headers = {
        "x-tkb-ollama-user": "{{USER_NAME}}",
        "x-openwebui-user-name": "t1ngyx",
    }

    assert get_forwarded_ollama_username(headers) == "t1ngyx"


def test_unexpanded_identity_template_is_ignored() -> None:
    assert get_forwarded_ollama_username(
        {"x-tkb-ollama-user": "{{USER_NAME}}"}
    ) is None


@pytest.mark.asyncio
async def test_openwebui_session_authorization_falls_back_to_forwarded_user(
    monkeypatch,
) -> None:
    expected = RequestPrincipal(
        team_id="engineering",
        subject="t1ngyx",
        roles=("viewer",),
        auth_source="ollama-account",
    )

    async def no_tkb_token(_token: str):
        return None

    async def trusted_user(username: str, requested_team_id: str | None = None):
        assert username == "t1ngyx"
        assert requested_team_id is None
        return expected

    monkeypatch.setattr(openwebui_server, "resolve_token_principal", no_tkb_token)
    monkeypatch.setattr(
        openwebui_server, "resolve_trusted_ollama_principal", trusted_user
    )
    monkeypatch.setattr(openwebui_server, "is_trusted_ollama_source", lambda _host: True)

    request = SimpleNamespace(
        headers={
            "authorization": "Bearer openwebui-session-token",
            "x-openwebui-user-name": "t1ngyx",
        },
        client=SimpleNamespace(host="172.17.0.4"),
    )

    assert await openwebui_server._get_principal(request) == expected

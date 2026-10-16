from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import fastmcp
import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware

from home_brain.auth import GITHUB_SCOPES, allowlist, build_auth
from home_brain.config import Settings
from home_brain.server import create_server

PUBLIC_URL = "http://localhost:8765"


@pytest.fixture
def github_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # build_auth points FastMCP's OAuth storage at DATA_PATH; undo that after each test.
    monkeypatch.setattr(fastmcp.settings, "home", fastmcp.settings.home)
    return dataclasses.replace(
        settings,
        auth_provider="github",
        public_url=PUBLIC_URL,
        github_client_id="Ov23li-test",
        github_client_secret="test-secret",
        allowed_users=frozenset({"allowed-user"}),
    )


def token_for(login: str | None) -> AccessToken:
    return AccessToken(token="t", client_id="c", scopes=GITHUB_SCOPES, claims={"login": login} if login else {})


def ctx(token: AccessToken | None) -> AuthContext:
    return AuthContext(token=token, component=SimpleNamespace(name="read_note"))  # type: ignore[arg-type]


def test_allowlist_check() -> None:
    check = allowlist(frozenset({"allowed-user"}))
    assert check(ctx(token_for("allowed-user")))
    assert check(ctx(token_for("Allowed-User")))  # GitHub logins are case-insensitive
    assert not check(ctx(token_for("someone-else")))
    assert not check(ctx(token_for(None)))
    assert not check(ctx(None))


def test_no_auth_by_default(settings: Settings) -> None:
    assert build_auth(settings) == (None, [])


def test_github_auth_keeps_state_in_data_path(github_settings: Settings) -> None:
    provider, middleware = build_auth(github_settings)
    assert isinstance(provider, GitHubProvider)
    assert [type(m) for m in middleware] == [AuthMiddleware]
    assert fastmcp.settings.home == github_settings.data_path / "fastmcp"


async def test_http_requires_login_but_health_is_public(github_settings: Settings) -> None:
    app = create_server(github_settings).http_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_URL) as http:
        health = await http.get("/health")
        assert (health.status_code, health.text) == (200, "ok")

        denied = await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert denied.status_code == 401
        assert "resource_metadata" in denied.headers["www-authenticate"]

        resource = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert resource["scopes_supported"] == GITHUB_SCOPES
        assert resource["authorization_servers"] == [f"{PUBLIC_URL}/"]

        server = (await http.get("/.well-known/oauth-authorization-server")).json()
        assert server["registration_endpoint"] == f"{PUBLIC_URL}/register"


@pytest.fixture
def signed_in_as(monkeypatch: pytest.MonkeyPatch):
    """Pretend the request carries a verified GitHub token for the given login."""

    def sign_in(login: str) -> None:
        monkeypatch.setattr(
            "fastmcp.server.middleware.authorization.get_access_token", lambda: token_for(login)
        )

    return sign_in


async def test_allowed_user_sees_and_uses_tools(github_settings: Settings, signed_in_as) -> None:
    signed_in_as("allowed-user")
    async with Client(create_server(github_settings)) as client:
        assert len(await client.list_tools()) == 9
        result = await client.call_tool("read_note", {"path": "people/kari.md"})
        assert result.structured_content["path"] == "people/kari.md"


async def test_other_github_users_get_nothing(github_settings: Settings, signed_in_as) -> None:
    signed_in_as("someone-else")
    async with Client(create_server(github_settings)) as client:
        assert await client.list_tools() == []
        with pytest.raises(ToolError, match="not found or not authorized|insufficient permissions"):
            await client.call_tool("read_note", {"path": "people/kari.md"})

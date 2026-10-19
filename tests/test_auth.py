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

from home_brain.auth import GITHUB_SCOPES, Allowlist, AllowlistGitHubProvider, build_auth
from home_brain.config import Settings
from home_brain.server import create_server

PUBLIC_URL = "http://localhost:8765"
ALLOWED_ID = "1234567"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
MCP_HEADERS = {"Authorization": "Bearer from-claude", "Accept": "application/json, text/event-stream"}


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
        allowed_users=frozenset({"allowed-user", ALLOWED_ID}),
    )


def token_for(login: str | None, user_id: str | None = None) -> AccessToken:
    claims = {key: value for key, value in (("sub", user_id), ("login", login)) if value}
    return AccessToken(token="t", client_id="c", scopes=GITHUB_SCOPES, claims=claims)


def ctx(token: AccessToken | None) -> AuthContext:
    return AuthContext(token=token, component=SimpleNamespace(name="read_note"))  # type: ignore[arg-type]


def test_allowlist_check() -> None:
    check = Allowlist(frozenset({"allowed-user"}))
    assert check(ctx(token_for("allowed-user")))
    assert check(ctx(token_for("Allowed-User")))  # GitHub logins are case-insensitive
    assert not check(ctx(token_for("someone-else")))
    assert not check(ctx(token_for(None)))
    assert not check(ctx(None))


def test_allowlist_prefers_user_ids() -> None:
    allowed = Allowlist(frozenset({ALLOWED_ID}))
    assert allowed.allows({"sub": ALLOWED_ID, "login": "renamed"})  # the ID survives a rename
    assert not allowed.allows({"sub": "42", "login": ALLOWED_ID})  # a login that looks like the ID
    assert not allowed.allows({"sub": "42", "login": "allowed-user"})
    assert not allowed.allows({"login": "allowed-user"})
    assert not allowed.allows({})


def test_allowlist_logs_each_user_once(caplog: pytest.LogCaptureFixture) -> None:
    allowed = Allowlist(frozenset({"allowed-user"}))
    for _ in range(3):
        allowed.allows({"sub": "42", "login": "allowed-user"})
        allowed.allows({"sub": "99", "login": "someone-else"})
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "GitHub user 'allowed-user' is allowed by login; put their user ID 42 in ALLOWED_USERS instead",
        "denied GitHub user 'someone-else' (ID 99): not in ALLOWED_USERS",
    ]


def test_logins_in_allowlist_warn_at_startup(github_settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    build_auth(github_settings)
    assert "ALLOWED_USERS has GitHub logins (allowed-user)" in caplog.text
    caplog.clear()
    build_auth(dataclasses.replace(github_settings, allowed_users=frozenset({ALLOWED_ID})))
    assert "ALLOWED_USERS has GitHub logins" not in caplog.text


@pytest.fixture
def github_says(monkeypatch: pytest.MonkeyPatch):
    """Pretend GitHub verified every bearer token as the given account (or rejected it, for None)."""

    def say(login: str | None, user_id: str | None = None) -> None:
        async def verify(self: GitHubProvider, token: str) -> AccessToken | None:
            return token_for(login, user_id) if login or user_id else None

        monkeypatch.setattr(GitHubProvider, "verify_token", verify)

    return say


async def test_tokens_of_other_github_users_are_invalid(github_settings: Settings, github_says) -> None:
    provider, _ = build_auth(github_settings)
    assert isinstance(provider, AllowlistGitHubProvider)
    github_says("someone-else", "42")
    assert await provider.verify_token("t") is None
    github_says(None)
    assert await provider.verify_token("t") is None
    github_says("renamed", ALLOWED_ID)
    access = await provider.verify_token("t")
    assert access is not None and access.claims["sub"] == ALLOWED_ID


async def mcp_handshake(settings: Settings) -> httpx2.Response:
    app = create_server(settings).http_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_URL) as http:
            return await http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)


async def test_handshake_is_refused_to_other_github_users(github_settings: Settings, github_says) -> None:
    # The server instructions hold the vault conventions, so a stranger mustn't even get that far.
    github_says("someone-else", "42")
    response = await mcp_handshake(github_settings)
    assert response.status_code == 401
    assert "invalid_token" in response.headers["www-authenticate"]
    assert "conventions" not in response.text.lower()


async def test_handshake_works_for_allowed_user(github_settings: Settings, github_says) -> None:
    github_says("renamed", ALLOWED_ID)
    response = await mcp_handshake(github_settings)
    assert response.status_code == 200
    assert "Vault conventions" in response.text


def test_no_auth_by_default(settings: Settings) -> None:
    assert build_auth(settings) == (None, [])


def test_github_auth_keeps_state_in_data_path(github_settings: Settings) -> None:
    provider, middleware = build_auth(github_settings)
    assert isinstance(provider, AllowlistGitHubProvider)
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
        assert len(await client.list_tools()) == 10
        result = await client.call_tool("read_note", {"path": "people/kari.md"})
        assert result.structured_content["path"] == "people/kari.md"


async def test_other_github_users_get_nothing(github_settings: Settings, signed_in_as) -> None:
    signed_in_as("someone-else")
    async with Client(create_server(github_settings)) as client:
        assert await client.list_tools() == []
        with pytest.raises(ToolError, match="not found or not authorized|insufficient permissions"):
            await client.call_tool("read_note", {"path": "people/kari.md"})

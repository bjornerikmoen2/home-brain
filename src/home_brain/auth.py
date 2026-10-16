"""Login for the Claude app: GitHub OAuth through FastMCP's OAuth proxy, plus an allowlist.

Any GitHub account can complete the OAuth flow, so the allowlist is what keeps everyone but
the configured users out. It is checked on every tool list and tool call.
"""

from __future__ import annotations

import logging

import fastmcp
from fastmcp.server.auth import AuthContext, AuthProvider
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware, Middleware

from .config import Settings

log = logging.getLogger(__name__)

# Read-only profile access is all we need to learn who the user is
# (FastMCP's default is "user", which also grants write access to the profile).
GITHUB_SCOPES = ["read:user"]

# GitHub is asked "who is this?" on every request otherwise.
TOKEN_CACHE_SECONDS = 300


def build_auth(settings: Settings) -> tuple[AuthProvider | None, list[Middleware]]:
    """The auth provider and middleware for the server, or (None, []) when auth is off."""
    if settings.auth_provider == "none":
        return None, []

    # OAuth state (client registrations, encrypted upstream tokens) must survive restarts.
    fastmcp.settings.home = settings.data_path / "fastmcp"
    provider = GitHubProvider(
        client_id=settings.github_client_id or "",
        client_secret=settings.github_client_secret or "",
        base_url=settings.public_url or "",
        required_scopes=GITHUB_SCOPES,
        cache_ttl_seconds=TOKEN_CACHE_SECONDS,
        jwt_signing_key=settings.jwt_signing_key,
    )
    log.info("GitHub login enabled for %s; allowed users: %s", settings.public_url, ", ".join(sorted(settings.allowed_users)))
    return provider, [AuthMiddleware(auth=allowlist(settings.allowed_users))]


def allowlist(allowed_users: frozenset[str]):
    """An auth check that passes only for the given GitHub logins (case-insensitive)."""
    reported: set[str | None] = set()

    def check(ctx: AuthContext) -> bool:
        claims = (ctx.token.claims or {}) if ctx.token else {}
        login = claims.get("login")
        if isinstance(login, str) and login.lower() in allowed_users:
            return True
        if login not in reported:  # tools/list runs this per tool; log each user once
            reported.add(login)
            log.warning("denied GitHub user %r: not in ALLOWED_USERS", login)
        return False

    return check

"""Login for the Claude app: GitHub OAuth through FastMCP's OAuth proxy, plus an allowlist.

Any GitHub account can complete the OAuth flow, so the allowlist is what keeps everyone but
the configured users out. It is checked twice:

- when a request's token is verified, so anyone else gets 401 on every request, including the
  MCP handshake (its server instructions contain the vault conventions);
- on every tool, resource and prompt, by FastMCP's AuthMiddleware, as a second line.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import fastmcp
from fastmcp.server.auth import AccessToken, AuthContext, AuthProvider
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.middleware import AuthMiddleware, Middleware

from .config import Settings

log = logging.getLogger(__name__)

# Read-only profile access is all we need to learn who the user is
# (FastMCP's default is "user", which also grants write access to the profile).
GITHUB_SCOPES = ["read:user"]

# GitHub is asked "who is this?" on every request otherwise.
TOKEN_CACHE_SECONDS = 300


class Allowlist:
    """The GitHub accounts that may use the server, from ALLOWED_USERS.

    A number is a GitHub user ID, which never changes. Anything else is a login (case-insensitive),
    which its owner can rename; after that, someone else can register the old login, so IDs are safer.
    """

    def __init__(self, users: frozenset[str]) -> None:
        self.ids = frozenset(u for u in users if u.isdigit())
        self.logins = frozenset(u.lower() for u in users if not u.isdigit())
        self._reported: set[tuple[str, str | None]] = set()

    def allows(self, claims: Mapping[str, Any]) -> bool:
        """Whether a verified GitHub identity (the `sub` and `login` claims) is on the list."""
        user_id, login = claims.get("sub"), claims.get("login")
        if isinstance(user_id, str) and user_id in self.ids:
            return True
        if isinstance(login, str) and login.lower() in self.logins:
            self._report(
                "login", login,
                "GitHub user %r is allowed by login; put their user ID %s in ALLOWED_USERS instead",
                login, user_id,
            )
            return True
        self._report("denied", login, "denied GitHub user %r (ID %s): not in ALLOWED_USERS", login, user_id)
        return False

    def __call__(self, ctx: AuthContext) -> bool:
        """The check for FastMCP's AuthMiddleware."""
        return self.allows((ctx.token.claims or {}) if ctx.token else {})

    def _report(self, kind: str, login: str | None, message: str, *args: object) -> None:
        # Every request and every tool in tools/list runs the check; log each user once.
        if (kind, login) not in self._reported:
            self._reported.add((kind, login))
            log.warning(message, *args)


class AllowlistGitHubProvider(GitHubProvider):
    """GitHub login that treats the tokens of everyone not on the allowlist as invalid."""

    def __init__(self, *, allowlist: Allowlist, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowlist = allowlist

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await super().verify_token(token)
        if access is None or not self.allowlist.allows(access.claims or {}):
            return None
        return access


def build_auth(settings: Settings) -> tuple[AuthProvider | None, list[Middleware]]:
    """The auth provider and middleware for the server, or (None, []) when auth is off."""
    if settings.auth_provider == "none":
        return None, []

    # OAuth state (client registrations, encrypted upstream tokens) must survive restarts.
    fastmcp.settings.home = settings.data_path / "fastmcp"
    allowlist = Allowlist(settings.allowed_users)
    provider = AllowlistGitHubProvider(
        allowlist=allowlist,
        client_id=settings.github_client_id or "",
        client_secret=settings.github_client_secret or "",
        base_url=settings.public_url or "",
        required_scopes=GITHUB_SCOPES,
        cache_ttl_seconds=TOKEN_CACHE_SECONDS,
        jwt_signing_key=settings.jwt_signing_key,
    )
    log.info("GitHub login enabled for %s; allowed users: %s", settings.public_url, ", ".join(sorted(settings.allowed_users)))
    if allowlist.logins:
        log.warning(
            "ALLOWED_USERS has GitHub logins (%s): a renamed login can be taken by someone else, "
            "so prefer numeric user IDs (logged at sign-in, or the `id` at https://api.github.com/users/<login>)",
            ", ".join(sorted(allowlist.logins)),
        )
    return provider, [AuthMiddleware(auth=allowlist)]

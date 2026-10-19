"""Runtime settings, read from environment variables."""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Settings that can't work together; the message says what to fix."""


@dataclass(frozen=True)
class Settings:
    vault_path: Path
    """Root folder of the markdown vault."""

    data_path: Path
    """Folder for the search index and login state. Must be a local disk, not NFS (SQLite locking)."""

    host: str = "127.0.0.1"
    port: int = 8000

    vault_git: bool = True
    """Keep the vault in git, with one commit per change."""
    git_author_name: str = "home-brain"
    git_author_email: str = "home-brain@localhost"

    auth_provider: Literal["none", "github"] = "none"
    public_url: str | None = None
    """Public HTTPS address of the server, e.g. https://brain.example.com (needed for OAuth)."""
    github_client_id: str | None = None
    github_client_secret: str | None = field(default=None, repr=False)
    allowed_users: frozenset[str] = frozenset()
    """GitHub user IDs (numbers, preferred) or logins (lowercase) that may use the server."""
    jwt_signing_key: str | None = field(default=None, repr=False)
    """Optional key for the tokens home-brain issues to Claude; defaults to one derived from the client secret."""

    ntfy_url: str | None = None
    """ntfy server home-brain publishes to, e.g. http://ntfy; push notifications are off when unset."""
    ntfy_topic: str = "home-brain"
    ntfy_token: str | None = field(default=None, repr=False)
    """Access token of the ntfy user home-brain publishes as."""
    briefing_time: dt.time = dt.time(19, 0)
    """When the evening briefing about tomorrow is pushed."""

    homeboard_url: str | None = None
    """home-board's API, e.g. http://home-board-api:8080; chores are off when unset."""
    homeboard_api_key: str | None = field(default=None, repr=False)
    """home-board's Integrations:ApiKey."""
    homeboard_write_key: str | None = field(default=None, repr=False)
    """home-board's Integrations:WriteApiKey: lets Claude approve chores and give bonus points."""

    calendar_urls: tuple[str, ...] = field(default=(), repr=False)
    """Secret iCal addresses of calendars (e.g. Google Calendar) whose events join the agenda."""

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        settings = cls(
            vault_path=Path(env.get("VAULT_PATH", "vault")),
            data_path=Path(env.get("DATA_PATH", "data")),
            host=env.get("HOST", "127.0.0.1"),
            port=int(env.get("PORT", "8000")),
            vault_git=env.get("VAULT_GIT", "true").strip().lower() not in ("0", "false", "no", "off"),
            git_author_name=env.get("VAULT_GIT_AUTHOR_NAME", "home-brain"),
            git_author_email=env.get("VAULT_GIT_AUTHOR_EMAIL", "home-brain@localhost"),
            auth_provider=env.get("AUTH_PROVIDER", "none").strip().lower(),  # type: ignore[arg-type]
            public_url=env.get("PUBLIC_URL") or None,
            github_client_id=env.get("GITHUB_CLIENT_ID") or None,
            github_client_secret=env.get("GITHUB_CLIENT_SECRET") or None,
            allowed_users=parse_users(env.get("ALLOWED_USERS", "")),
            jwt_signing_key=env.get("OAUTH_JWT_SIGNING_KEY") or None,
            ntfy_url=env.get("NTFY_URL") or None,
            ntfy_topic=env.get("NTFY_TOPIC") or "home-brain",
            ntfy_token=env.get("NTFY_TOKEN") or None,
            briefing_time=parse_time("BRIEFING_TIME", env.get("BRIEFING_TIME") or "19:00"),
            homeboard_url=env.get("HOMEBOARD_URL") or None,
            homeboard_api_key=env.get("HOMEBOARD_API_KEY") or None,
            homeboard_write_key=env.get("HOMEBOARD_WRITE_KEY") or None,
            calendar_urls=parse_urls(env.get("CALENDAR_URLS", "")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.ntfy_url:
            url = urlparse(self.ntfy_url)
            if url.scheme not in ("https", "http") or not url.netloc:
                raise ConfigError(f"NTFY_URL must be a full URL like http://ntfy, not {self.ntfy_url!r}")
            if not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", self.ntfy_topic):
                raise ConfigError("NTFY_TOPIC may only contain letters, digits, '-' and '_' (at most 64)")
        if self.homeboard_url:
            url = urlparse(self.homeboard_url)
            if url.scheme not in ("https", "http") or not url.netloc:
                raise ConfigError(f"HOMEBOARD_URL must be a full URL like http://home-board-api:8080, not {self.homeboard_url!r}")
            if not self.homeboard_api_key:
                raise ConfigError("HOMEBOARD_URL needs HOMEBOARD_API_KEY (home-board's Integrations__ApiKey)")
        elif self.homeboard_write_key:
            raise ConfigError("HOMEBOARD_WRITE_KEY needs HOMEBOARD_URL and HOMEBOARD_API_KEY")
        for number, address in enumerate(self.calendar_urls, 1):
            url = urlparse(address)
            if url.scheme not in ("https", "http") or not url.netloc:
                # The address is a secret: say which one, not what it is.
                raise ConfigError(f"CALENDAR_URLS: address {number} must be a full https:// iCal address")
        if self.auth_provider not in ("none", "github"):
            raise ConfigError(f"AUTH_PROVIDER must be 'none' or 'github', not {self.auth_provider!r}")
        if self.auth_provider == "none":
            return
        missing = [
            name
            for name, value in (
                ("PUBLIC_URL", self.public_url),
                ("GITHUB_CLIENT_ID", self.github_client_id),
                ("GITHUB_CLIENT_SECRET", self.github_client_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"AUTH_PROVIDER=github needs {', '.join(missing)}")
        if not self.allowed_users:
            raise ConfigError("AUTH_PROVIDER=github needs ALLOWED_USERS (comma-separated GitHub user IDs or logins)")
        url = urlparse(self.public_url or "")
        if url.scheme not in ("https", "http") or not url.netloc:
            raise ConfigError(f"PUBLIC_URL must be a full URL like https://brain.example.com, not {self.public_url!r}")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1"):
            raise ConfigError("PUBLIC_URL must use https (http is only allowed for localhost testing)")


def parse_users(value: str) -> frozenset[str]:
    return frozenset(u.strip().lstrip("@").lower() for u in value.split(",") if u.strip())


def parse_urls(value: str) -> tuple[str, ...]:
    """Addresses separated by commas, spaces or newlines; webcal:// is read as https://."""
    urls = (u.strip() for u in re.split(r"[,\s]+", value) if u.strip())
    return tuple("https://" + u[len("webcal://"):] if u.lower().startswith("webcal://") else u for u in urls)


def parse_time(name: str, value: str) -> dt.time:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ConfigError(f"{name} must be a time like 19:00, not {value!r}")
    return dt.time(int(match.group(1)), int(match.group(2)))

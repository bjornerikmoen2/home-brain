from __future__ import annotations

from pathlib import Path

import pytest

from home_brain.config import ConfigError, Settings, parse_users

GITHUB_ENV = {
    "AUTH_PROVIDER": "github",
    "PUBLIC_URL": "https://brain.example.com",
    "GITHUB_CLIENT_ID": "Ov23li-test",
    "GITHUB_CLIENT_SECRET": "s3cret",
    "ALLOWED_USERS": "SomeUser, @other ",
}


def env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key in (*GITHUB_ENV, "VAULT_PATH", "DATA_PATH", "VAULT_GIT", "OAUTH_JWT_SIGNING_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {})
    settings = Settings.from_env()
    assert settings.vault_path == Path("vault")
    assert settings.vault_git is True
    assert settings.auth_provider == "none"


def test_github_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {**GITHUB_ENV, "VAULT_GIT": "false"})
    settings = Settings.from_env()
    assert settings.auth_provider == "github"
    assert settings.allowed_users == frozenset({"someuser", "other"})
    assert settings.vault_git is False
    assert "s3cret" not in repr(settings)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"GITHUB_CLIENT_SECRET": ""}, "needs GITHUB_CLIENT_SECRET"),
        ({"PUBLIC_URL": "", "GITHUB_CLIENT_ID": ""}, "needs PUBLIC_URL, GITHUB_CLIENT_ID"),
        ({"ALLOWED_USERS": " , "}, "needs ALLOWED_USERS"),
        ({"PUBLIC_URL": "brain.example.com"}, "full URL"),
        ({"PUBLIC_URL": "http://brain.example.com"}, "must use https"),
        ({"AUTH_PROVIDER": "google"}, "must be 'none' or 'github'"),
    ],
)
def test_invalid_auth_settings(monkeypatch: pytest.MonkeyPatch, override: dict[str, str], message: str) -> None:
    env(monkeypatch, {**GITHUB_ENV, **override})
    with pytest.raises(ConfigError, match=message):
        Settings.from_env()


def test_http_localhost_is_allowed_for_testing(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {**GITHUB_ENV, "PUBLIC_URL": "http://localhost:8000"})
    assert Settings.from_env().public_url == "http://localhost:8000"


def test_parse_users() -> None:
    assert parse_users("") == frozenset()
    assert parse_users("A,,@b , c ") == frozenset({"a", "b", "c"})

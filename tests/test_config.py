from __future__ import annotations

import datetime as dt
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
NTFY_ENV = {
    "NTFY_URL": "http://ntfy",
    "NTFY_TOPIC": "family",
    "NTFY_TOKEN": "tk_secret",
    "BRIEFING_TIME": "7:30",
}


def env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key in (*GITHUB_ENV, *NTFY_ENV, "HOMEBOARD_URL", "HOMEBOARD_API_KEY", "HOMEBOARD_WRITE_KEY", "VAULT_PATH", "DATA_PATH", "VAULT_GIT",
                "OAUTH_JWT_SIGNING_KEY", "CALENDAR_URLS", "NOTIFY_LANGUAGE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {})
    settings = Settings.from_env()
    assert settings.vault_path == Path("vault")
    assert settings.vault_git is True
    assert settings.auth_provider == "none"
    assert settings.ntfy_url is None
    assert settings.briefing_time == dt.time(19, 0)


def test_ntfy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, NTFY_ENV)
    settings = Settings.from_env()
    assert (settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token) == ("http://ntfy", "family", "tk_secret")
    assert settings.briefing_time == dt.time(7, 30)
    assert "tk_secret" not in repr(settings)


def test_homeboard_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {"HOMEBOARD_URL": "http://home-board-api:8080", "HOMEBOARD_API_KEY": "s3cret-key"})
    settings = Settings.from_env()
    assert settings.homeboard_url == "http://home-board-api:8080"
    assert settings.homeboard_write_key is None
    assert "s3cret-key" not in repr(settings)
    env(monkeypatch, {"HOMEBOARD_URL": "http://home-board-api:8080", "HOMEBOARD_API_KEY": "k", "HOMEBOARD_WRITE_KEY": "wr1te-key"})
    settings = Settings.from_env()
    assert settings.homeboard_write_key == "wr1te-key"
    assert "wr1te-key" not in repr(settings)


def test_calendar_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {"CALENDAR_URLS": " https://calendar.example.com/a/private-1/basic.ics,\n webcal://example.com/b.ics "})
    settings = Settings.from_env()
    assert settings.calendar_urls == ("https://calendar.example.com/a/private-1/basic.ics", "https://example.com/b.ics")
    assert "private-1" not in repr(settings)
    env(monkeypatch, {"CALENDAR_URLS": "https://example.com/a.ics calendar.example.com/private-2/basic.ics"})
    with pytest.raises(ConfigError, match="address 2 must be a full https:// iCal address") as caught:
        Settings.from_env()
    assert "private-2" not in str(caught.value)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"NTFY_URL": "ntfy"}, "NTFY_URL must be a full URL"),
        ({"NTFY_TOPIC": "family news"}, "NTFY_TOPIC may only contain"),
        ({"BRIEFING_TIME": "25:00"}, "BRIEFING_TIME must be a time"),
        ({"BRIEFING_TIME": "evening"}, "BRIEFING_TIME must be a time"),
        ({"HOMEBOARD_URL": "home-board-api:8080", "HOMEBOARD_API_KEY": "k"}, "HOMEBOARD_URL must be a full URL"),
        ({"HOMEBOARD_URL": "http://home-board-api:8080"}, "needs HOMEBOARD_API_KEY"),
        ({"HOMEBOARD_WRITE_KEY": "k"}, "HOMEBOARD_WRITE_KEY needs HOMEBOARD_URL"),
    ],
)
def test_invalid_ntfy_settings(monkeypatch: pytest.MonkeyPatch, override: dict[str, str], message: str) -> None:
    env(monkeypatch, {**NTFY_ENV, **override})
    with pytest.raises(ConfigError, match=message):
        Settings.from_env()


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


def test_notify_language(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch, {})
    assert Settings.from_env().notify_language == "en"
    env(monkeypatch, {"NOTIFY_LANGUAGE": " NB "})
    assert Settings.from_env().notify_language == "nb"
    env(monkeypatch, {"NOTIFY_LANGUAGE": "de"})
    with pytest.raises(ConfigError, match="NOTIFY_LANGUAGE must be one of en, nb, no, not 'de'"):
        Settings.from_env()


def test_parse_users() -> None:
    assert parse_users("") == frozenset()
    assert parse_users("A,,@b , c ") == frozenset({"a", "b", "c"})

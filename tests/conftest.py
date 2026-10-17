from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import datetime as dt

from home_brain.config import Settings
from home_brain.homeboard import Chore, HomeBoardError
from home_brain.index import Index
from home_brain.notify import Message, NotifyError
from home_brain.vault import Vault

MON = dt.date(2026, 10, 19)
TUE = dt.date(2026, 10, 20)
CHORES = [
    Chore(MON, "Make bed", "verified", "daily", 5, due="08:00", assigned_to="Ola", completed_by="Ola",
          completed_at=dt.datetime(2026, 10, 19, 7, 40)),
    Chore(MON, "Feed the cat", "todo", "daily", 10, due="09:00", assigned_to="Nora"),
    Chore(MON, "Vacuum room", "todo", "during_week", 20, assigned_to="Ola"),
    Chore(MON, "Empty dishwasher", "done", "daily", 10, group="kids", completed_by="Nora",
          completed_at=dt.datetime(2026, 10, 19, 11, 5)),
    Chore(TUE, "Make bed", "todo", "daily", 5, due="08:00", assigned_to="Ola"),
    Chore(TUE, "Vacuum room", "todo", "during_week", 20, assigned_to="Ola"),
    Chore(TUE, "Empty dishwasher", "todo", "daily", 10, group="kids"),
]
"""Chores as home-board would report them for Monday 19 and Tuesday 20 October 2026."""

POINTS = {
    "timezone": "Europe/Oslo",
    "today": "2026-10-19",
    "pointToMoneyRate": 0.5,
    "users": [
        {"name": "Nora", "scoreboardPoints": 10, "pointsSinceLastPayout": 10, "moneyToPay": 5.0, "lastPayoutAt": None,
         "earnedThisWeek": 15, "earnedThisMonth": 15,
         "waitingForApproval": [{"date": "2026-10-19", "title": "Feed the cat", "points": 10,
                                 "completedAt": "2026-10-19T06:00:00Z"}]},
        {"name": "Ola", "scoreboardPoints": 30, "pointsSinceLastPayout": 30, "moneyToPay": 15.0,
         "lastPayoutAt": "2026-10-01T18:00:00Z", "earnedThisWeek": 20, "earnedThisMonth": 30, "waitingForApproval": []},
    ],
}
"""Points as home-board would report them on Monday 19 October 2026."""


class FakeHomeBoard:
    def __init__(self, chores: list[Chore] = CHORES, error: str | None = None) -> None:
        self._chores = chores
        self.error = error
        self.calls: list[tuple[dt.date, dt.date]] = []

    def chores(self, start: dt.date, end: dt.date) -> list[Chore]:
        self.calls.append((start, end))
        if self.error:
            raise HomeBoardError(self.error)
        return [c for c in self._chores if start <= c.date <= end]

    def points(self) -> dict:
        if self.error:
            raise HomeBoardError(self.error)
        return POINTS


class FakeNotifier:
    """Collects the messages instead of pushing them; fails the first `failures` sends."""

    def __init__(self, failures: int = 0) -> None:
        self.sent: list[Message] = []
        self.failures = failures

    def send(self, message: Message) -> None:
        if self.failures:
            self.failures -= 1
            raise NotifyError("ntfy is unreachable")
        self.sent.append(message)


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vault = Vault(tmp_path / "vault")
    vault.seed()
    return vault


@pytest.fixture
def index(vault: Vault, tmp_path: Path) -> Iterator[Index]:
    index = Index(vault, tmp_path / "data" / "index.db", min_refresh_interval=0)
    yield index
    index.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # git is off here to keep the server tests fast; test_gitsync covers versioning.
    settings = Settings(vault_path=tmp_path / "vault", data_path=tmp_path / "data", vault_git=False)
    Vault(settings.vault_path).seed()
    return settings

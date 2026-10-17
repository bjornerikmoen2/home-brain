from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import datetime as dt
from zoneinfo import ZoneInfo

from home_brain.calendars import Calendars
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


CALENDAR_ICS = b"""\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
X-WR-CALNAME:Family
X-WR-TIMEZONE:Europe/Oslo
BEGIN:VEVENT
UID:football@example.com
DTSTART;TZID=Europe/Oslo:20261013T170000
DTEND;TZID=Europe/Oslo:20261013T183000
RRULE:FREQ=WEEKLY;BYDAY=TU
EXDATE;TZID=Europe/Oslo:20261027T170000
SUMMARY:Football practice
LOCATION:Eksempel stadion
END:VEVENT
BEGIN:VEVENT
UID:football@example.com
RECURRENCE-ID;TZID=Europe/Oslo:20261020T170000
DTSTART;TZID=Europe/Oslo:20261020T180000
DTEND;TZID=Europe/Oslo:20261020T193000
SUMMARY:Football practice (late)
END:VEVENT
BEGIN:VEVENT
UID:book-club@example.com
DTSTART;TZID=Europe/Oslo:20260922T190000
DTEND;TZID=Europe/Oslo:20260922T210000
RRULE:FREQ=MONTHLY;BYDAY=4TH
SUMMARY:Book club
END:VEVENT
BEGIN:VEVENT
UID:dentist@example.com
DTSTART:20261021T080000Z
DTEND:20261021T090000Z
SUMMARY:Dentist
DESCRIPTION:<b>Bring</b> the health card<br>Room 2
END:VEVENT
BEGIN:VEVENT
UID:cancelled@example.com
DTSTART;TZID=Europe/Oslo:20261021T120000
DTEND;TZID=Europe/Oslo:20261021T130000
SUMMARY:Lunch
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:cabin@example.com
DTSTART;VALUE=DATE:20261023
DTEND;VALUE=DATE:20261026
SUMMARY:Cabin weekend
END:VEVENT
BEGIN:VEVENT
UID:party@example.com
DTSTART;TZID=Europe/Oslo:20261024T140000
DTEND;TZID=Europe/Oslo:20261024T170000
SUMMARY:Per's birthday party
LOCATION:Eksempelveien 12\\, 0123 Oslo
END:VEVENT
END:VCALENDAR
"""
"""A family calendar as Google Calendar exports it, around Monday 19 October 2026."""
CALENDAR_URL = "https://calendar.example.com/calendar/ical/family/private-s3cret/basic.ics"


class FakeFeeds:
    """Serves calendar feeds by address and counts the downloads; `error` makes every one fail."""

    def __init__(self, feeds: dict[str, bytes] | None = None, error: Exception | None = None) -> None:
        self.feeds = {CALENDAR_URL: CALENDAR_ICS} if feeds is None else feeds
        self.error = error
        self.downloads = 0

    def __call__(self, url: str) -> bytes:
        self.downloads += 1
        if self.error:
            raise self.error
        return self.feeds[url]


def fake_calendars(feeds: FakeFeeds | None = None, urls: list[str] | None = None, **kwargs: object) -> Calendars:
    return Calendars(urls or [CALENDAR_URL], fetch=feeds or FakeFeeds(), tz=ZoneInfo("Europe/Oslo"), **kwargs)

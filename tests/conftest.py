from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import datetime as dt
from zoneinfo import ZoneInfo

from home_brain.calendars import Calendars
from home_brain.config import Settings
from home_brain.homeboard import Chore, HomeBoardError, HomeBoardRefused
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
          completed_at=dt.datetime(2026, 10, 19, 11, 5), completion_id="0b5e3f7a-2c41-4d8e-9a6b-1f2e3d4c5b6a"),
    Chore(TUE, "Make bed", "todo", "daily", 5, due="08:00", assigned_to="Ola"),
    Chore(TUE, "Vacuum room", "todo", "during_week", 20, assigned_to="Ola"),
    Chore(TUE, "Empty dishwasher", "todo", "daily", 10, group="kids"),
]
"""Chores as home-board would report them for Monday 19 and Tuesday 20 October 2026."""

NORA_ID = "6f1c2d3e-4b5a-4c6d-8e7f-9a0b1c2d3e4f"
OLA_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
FEED_CAT_ID = "9c8b7a6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d"

POINTS = {
    "timezone": "Europe/Oslo",
    "today": "2026-10-19",
    "pointToMoneyRate": 0.5,
    "users": [
        {"userId": NORA_ID, "name": "Nora", "scoreboardPoints": 10, "pointsSinceLastPayout": 10, "moneyToPay": 5.0,
         "lastPayoutAt": None, "earnedThisWeek": 15, "earnedThisMonth": 15,
         "waitingForApproval": [{"completionId": FEED_CAT_ID, "date": "2026-10-19", "title": "Feed the cat", "points": 10,
                                 "completedAt": "2026-10-19T06:00:00Z"}]},
        {"userId": OLA_ID, "name": "Ola", "scoreboardPoints": 30, "pointsSinceLastPayout": 30, "moneyToPay": 15.0,
         "lastPayoutAt": "2026-10-01T18:00:00Z", "earnedThisWeek": 20, "earnedThisMonth": 30, "waitingForApproval": []},
    ],
}
"""Points as home-board would report them on Monday 19 October 2026."""


class FakeHomeBoard:
    """home-board's integration API; with `write`, it also takes approvals and bonus points."""

    def __init__(self, chores: list[Chore] = CHORES, error: str | None = None, write: bool = False) -> None:
        self._chores = chores
        self.error = error
        self.can_write = write
        self.calls: list[tuple[dt.date, dt.date]] = []
        self.changes: list[tuple] = []

    def approve(self, completion_id: str, points: int | None = None) -> dict:
        self._check(completion_id)
        self.changes.append(("approve", completion_id, points))
        return {"message": "Chore approved", "title": "Feed the cat", "completedBy": "Nora", "pointsAwarded": points or 10}

    def reject(self, completion_id: str, reason: str | None = None) -> dict:
        self._check(completion_id)
        self.changes.append(("reject", completion_id, reason))
        return {"message": "Chore rejected", "title": "Feed the cat", "completedBy": "Nora", "pointsAwarded": 0}

    def award(self, user_id: str, points: int, description: str) -> dict:
        if self.error:
            raise HomeBoardError(self.error)
        self.changes.append(("award", user_id, points, description))
        name = next(u["name"] for u in POINTS["users"] if u["userId"] == user_id)
        return {"message": "Bonus points awarded", "name": name, "pointsAwarded": points}

    def _check(self, completion_id: str) -> None:
        if self.error:
            raise HomeBoardError(self.error)
        if completion_id != FEED_CAT_ID:
            raise HomeBoardRefused("No done chore with this completion id")
        if any(c[1] == completion_id for c in self.changes):
            raise HomeBoardRefused("'Feed the cat' by Nora was already approved or rejected")

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


def make_pdf(*pages: str) -> bytes:
    """A small PDF with the given text per page (Helvetica, so Norwegian letters work); "" makes a
    page without text, like a scan."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for text in pages:
        shown = b" T* ".join(b"(" + _pdf_string(line) + b") Tj" for line in text.split("\n"))
        stream = b"BT /F1 12 Tf 14 TL 50 800 Td " + shown + b" ET"
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >>"
            b" /Contents %d 0 R >>" % len(objects)
        )
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _pdf_string(text: str) -> bytes:
    return text.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


POLICY_PDF = make_pdf(
    "Home insurance policy 2026\nPolicyholder: Ola Nordmann\nAnnual price: 12 000 kr",
    "Cover: building and contents\nDeductible: 8 000 kr",
    "Exclusions\nSnow damage to the roof is covered when the snow was cleared.\nGlass breakage: deductible 3 000 kr",
)
"""An example insurance policy: three pages of text."""


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

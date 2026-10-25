"""Proactive notifications: reminders when they are due, and the scheduled briefings.

A background thread wakes up every minute and works out what should have gone out by then: the
reminders whose time has come, and today's scheduled messages (the evening briefing about
tomorrow, the week ahead on Sundays, the maintenance check on Mondays). What was sent is recorded
in SQLite under DATA_PATH, not in the vault, so nothing goes out twice. After a restart, anything
missed in the last few hours is sent late instead of being lost; if ntfy is down, the next round
tries again.

Notes marked `example: true` (the starter template) are left out: nobody is there to tell the
placeholders from real facts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from . import briefing, frontmatter
from .agenda import Item, Note, as_moment, people
from .agenda import build as build_agenda
from .calendars import Calendars
from .frontmatter import FrontmatterError
from .homeboard import HomeBoard, HomeBoardError, agenda_items, waiting_summary
from .index import Index
from .notify import Message, Notifier, NotifyError
from .texts import EN, Texts
from .vault import Vault, VaultError

log = logging.getLogger(__name__)

CATCH_UP = dt.timedelta(hours=6)
"""How late a missed reminder or briefing is still sent; older ones are dropped."""
DATE_ONLY_AT = dt.time(8, 0)
"""When a reminder with a date but no time is sent."""
KEEP_SENT = dt.timedelta(days=120)


@dataclass(frozen=True)
class Job:
    name: str
    at: dt.time
    weekday: int | None
    """0 = Monday … 6 = Sunday; None for every day."""
    build: Callable[[list[Note], dt.date], Message | None]


class Scheduler:
    def __init__(
        self,
        vault: Vault,
        index: Index,
        notifier: Notifier,
        db_path: Path,
        briefing_time: dt.time = dt.time(19, 0),
        clock: Callable[[], dt.datetime] = dt.datetime.now,
        homeboard: HomeBoard | None = None,
        calendars: Calendars | None = None,
        texts: Texts = EN,
    ) -> None:
        self._vault = vault
        self._index = index
        self._notifier = notifier
        self._homeboard = homeboard
        self._calendars = calendars
        self._texts = texts
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        with self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, at TEXT NOT NULL, status TEXT NOT NULL)")
        self.jobs = [
            Job("tomorrow", briefing_time, None, self._tomorrow),
            Job("week-ahead", dt.time(18, 0), 6, self._week_ahead),
            Job("maintenance", dt.time(8, 0), 0, self._maintenance),
        ]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._thread.start()
        days = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
        when = ", ".join(f"{j.name} {'daily' if j.weekday is None else days[j.weekday]} at {j.at:%H:%M}" for j in self.jobs)
        log.info("scheduler started: reminders when due; %s", when)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        with self._lock:
            self._db.close()

    def _run(self) -> None:
        self._forget_old(self._clock())
        while True:
            try:
                self.tick()
            except Exception:  # one bad round must not stop the reminders for good
                log.exception("scheduler round failed")
            now = self._clock()
            if self._stop.wait(60.5 - now.second - now.microsecond / 1e6):
                return

    def tick(self, now: dt.datetime | None = None) -> list[str]:
        """Send everything due by `now` that hasn't been sent; returns what was sent."""
        now = now or self._clock()
        notes = [Note(path, title, meta) for path, title, meta in self._index.frontmatter() if meta.get("example") is not True]
        sent = []
        for key, build in self._due(notes, now):
            if self._done(key):
                continue
            message = build()
            if message is None:
                self._record(key, now, "nothing to send")
                continue
            try:
                self._notifier.send(message)
            except NotifyError as exc:
                log.warning("could not send %s, trying again next minute: %s", key, exc)
                continue
            self._record(key, now, "sent")
            log.info("sent %s", key)
            sent.append(key)
        return sent

    def _due(self, notes: list[Note], now: dt.datetime) -> Iterator[tuple[str, Callable[[], Message | None]]]:
        for note in notes:
            for raw in _reminders(note):
                moment = as_moment(raw.get("at"))
                text = str(raw.get("text") or "").strip()
                if moment is None or not text or raw.get("done"):
                    continue
                day, clock = moment
                due = dt.datetime.combine(day, dt.time.fromisoformat(clock) if clock else DATE_ONLY_AT)
                if due <= now < due + CATCH_UP:
                    who = people(raw.get("who")) or people(note.meta.get("who"))
                    yield _reminder_key(note.path, raw, due, text), partial(briefing.reminder, text, who, due, now, self._texts)
        today = now.date()
        for job in self.jobs:
            slot = dt.datetime.combine(today, job.at)
            if (job.weekday is None or today.weekday() == job.weekday) and slot <= now < slot + CATCH_UP:
                yield f"{job.name}:{today.isoformat()}", partial(job.build, notes, today)

    # --- the scheduled messages --------------------------------------------------

    def _tomorrow(self, notes: list[Note], today: dt.date) -> Message:
        day = today + dt.timedelta(days=1)
        extra, warnings = self._events(day, day)
        waiting = []
        if self._homeboard is not None:
            # The rest of the briefing is still worth sending when Home-Board is down.
            try:
                extra += agenda_items(self._homeboard.chores(day, day), today)
            except HomeBoardError as exc:
                log.warning("tomorrow's briefing goes out without chores: %s", exc)
                warnings.append(self._texts.homeboard_unreachable)
            try:
                waiting = waiting_summary(self._homeboard.points(), self._texts)
            except HomeBoardError as exc:
                log.warning("tomorrow's briefing goes out without approvals: %s", exc)
        return briefing.tomorrow(build_agenda(notes, day, day, today, extra=extra), day, waiting, warnings, self._texts)

    def _week_ahead(self, notes: list[Note], today: dt.date) -> Message:
        start = today + dt.timedelta(days=1)
        end = start + dt.timedelta(days=6)
        extra, warnings = self._events(start, end)
        return briefing.week_ahead(build_agenda(notes, start, end, today, extra=extra), start, end, warnings, self._texts)

    def _events(self, start: dt.date, end: dt.date) -> tuple[list[Item], list[str]]:
        """Calendar events (e.g. Google Calendar) and why any are missing."""
        if self._calendars is None:
            return [], []
        return self._calendars.items(start, end, self._texts)

    def _maintenance(self, notes: list[Note], today: dt.date) -> Message | None:
        return briefing.maintenance(notes, today, self._body, self._texts)

    def _body(self, path: str) -> str:
        try:
            return frontmatter.split(self._vault.read(path)[1])[1]
        except (VaultError, FrontmatterError, OSError):
            return ""

    # --- what was sent -----------------------------------------------------------

    def _done(self, key: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM sent WHERE key = ?", (key,)).fetchone() is not None

    def _record(self, key: str, now: dt.datetime, status: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO sent (key, at, status) VALUES (?, ?, ?)",
                (key, now.isoformat(timespec="seconds"), status),
            )

    def _forget_old(self, now: dt.datetime) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sent WHERE at < ?", ((now - KEEP_SENT).isoformat(timespec="seconds"),))


def _reminders(note: Note) -> list[dict[str, Any]]:
    value = note.meta.get("reminders")
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _reminder_key(path: str, raw: dict[str, Any], due: dt.datetime, text: str) -> str:
    """Reminders saved with `remind` have an id; hand-written ones are known by note, time and text."""
    if raw.get("id"):
        return f"reminder:{raw['id']}"
    digest = hashlib.sha1(text.encode()).hexdigest()[:10]
    return f"reminder:{path}:{due:%Y-%m-%dT%H:%M}:{digest}"

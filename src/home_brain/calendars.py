"""Events from shared calendars, such as Google Calendar, read through their iCal (ICS) address.

Google Calendar gives every calendar a secret address in iCal format (the calendar's settings,
"Integrate calendar"). home-brain only reads that feed, so it needs no Google login. Anyone with
the address can read the calendar, so it is kept in .env like a password and never logged.

Events become agenda items: kind `recurring` for events that repeat daily or weekly (so the week
ahead can leave them out, like the vault's routines), `event` for everything else.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import icalendar
import recurring_ical_events

from .agenda import Item
from .config import Settings
from .texts import EN, Texts

log = logging.getLogger(__name__)

SOURCE = "calendar"
CACHE_SECONDS = 300
"""A feed is fetched at most this often; `agenda` is often called a few times in a row."""
MAX_BYTES = 20_000_000
NOTES_CHARS = 300
ROUTINE = ("DAILY", "WEEKLY")

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</p>|</div>|</li>", re.IGNORECASE)

Fetch = Callable[[str], bytes]


class CalendarError(RuntimeError):
    """A calendar feed could not be read. The message never contains the secret address."""


class Calendars:
    def __init__(
        self,
        urls: list[str],
        fetch: Fetch | None = None,
        clock: Callable[[], float] = time.monotonic,
        tz: dt.tzinfo | None = None,
    ) -> None:
        """`tz` is the time zone events are shown in; the server's own (TZ) by default."""
        self._urls = list(urls)
        self._fetch = fetch or _download
        self._clock = clock
        self._tz = tz
        self._cache: dict[str, tuple[float, icalendar.Calendar]] = {}
        self._names: dict[str, str] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> Calendars | None:
        return cls(list(settings.calendar_urls)) if settings.calendar_urls else None

    def items(self, start: dt.date, end: dt.date, t: Texts = EN) -> tuple[list[Item], list[str]]:
        """The events on the days start..end (inclusive) from every calendar, and a warning for
        each calendar that could not be read (in the words of `t`). One broken feed doesn't hide
        the others."""
        found: list[Item] = []
        warnings: list[str] = []
        for number, url in enumerate(self._urls, 1):
            try:
                calendar = self._calendar(url)
                found += events(calendar, start, end, self._names.get(url), self._tz)
            except CalendarError as exc:
                log.warning("%s could not be read: %s", self._names.get(url) or f"calendar {number}", exc)
                name = self._names.get(url) or t.unnamed_calendar.format(number=number)
                warnings.append(t.calendar_unreadable.format(name=name, reason=exc))
        return found, warnings

    def _calendar(self, url: str) -> icalendar.Calendar:
        with self._lock:
            cached = self._cache.get(url)
            if cached and self._clock() - cached[0] < CACHE_SECONDS:
                return cached[1]
            try:
                calendar = icalendar.Calendar.from_ical(self._fetch(url))
            except CalendarError:
                raise
            except Exception as exc:  # icalendar raises ValueError and friends on a broken feed
                raise CalendarError("the feed is not a valid iCal file") from exc
            if name := str(calendar.get("X-WR-CALNAME") or "").strip():
                self._names[url] = name
            self._cache[url] = (self._clock(), calendar)
            return calendar


def events(
    calendar: icalendar.Calendar, start: dt.date, end: dt.date, name: str | None = None, tz: dt.tzinfo | None = None
) -> list[Item]:
    """The calendar's events on the days start..end as agenda items, recurring ones expanded.
    Times are shown in `tz`, the server's local time zone (TZ) by default."""
    repeats = {
        str(event.get("UID")): str(event["RRULE"].get("FREQ", [""])[0]).upper()
        for event in calendar.walk("VEVENT")
        if "RRULE" in event
    }
    try:
        occurrences = recurring_ical_events.of(calendar).between(start, end + dt.timedelta(days=1))
    except Exception as exc:  # a rule the library can't expand
        raise CalendarError(f"the events could not be read ({type(exc).__name__})") from exc
    items = []
    for event in occurrences:
        if str(event.get("STATUS") or "").upper() == "CANCELLED":
            continue
        item = _item(event, start, end, repeats.get(str(event.get("UID"))) in ROUTINE, tz)
        if item:
            if name:
                item.extra["calendar"] = name
            items.append(item)
    return items


def _item(event: Any, start: dt.date, end: dt.date, routine: bool, tz: dt.tzinfo | None) -> Item | None:
    begins = _local(event.get("DTSTART").dt, tz)
    ends = _local(event["DTEND"].dt, tz) if "DTEND" in event else None
    if ends is None and "DURATION" in event:
        ends = begins + event["DURATION"].dt
    timed = isinstance(begins, dt.datetime)
    if ends is not None and isinstance(ends, dt.datetime) != timed:  # DTSTART and DTEND of different types
        ends = dt.datetime.combine(ends, dt.time()) if timed else ends.date()
    if timed:
        first = begins.date()
        # An event ending at midnight belongs to the day before.
        last = (ends - dt.timedelta(microseconds=1)).date() if ends and ends > begins else first
    else:
        first = begins
        # All-day events end the day before DTEND.
        last = ends - dt.timedelta(days=1) if isinstance(ends, dt.date) and ends > begins else first
    if last < start or first > end:
        return None
    day = max(first, start)
    item = Item(
        date=day,
        kind="recurring" if routine else "event",
        title=str(event.get("SUMMARY") or "").strip() or "(no title)",
        source=SOURCE,
        where=_text(event.get("LOCATION")) or None,
        notes=_notes(event.get("DESCRIPTION")),
    )
    if timed and day == first:
        item.time = f"{begins:%H:%M}"
        if ends and last == first and ends > begins:
            item.end_time = f"{ends:%H:%M}"
    if last > day:
        item.end_date = last
    return item


def _local(value: dt.date | dt.datetime, tz: dt.tzinfo | None) -> dt.date | dt.datetime:
    """Aware date-times in `tz`, without tzinfo; dates and floating times as they are."""
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(tz).replace(tzinfo=None)
    return value


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _notes(value: Any) -> str | None:
    """The description as plain text (Google stores HTML), shortened."""
    text = str(value or "")
    if "<" in text:
        text = html.unescape(_TAG.sub("", _BREAK.sub("\n", text)))
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) > NOTES_CHARS:
        text = text[: NOTES_CHARS - 1].rstrip() + "…"
    return text or None


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "home-brain"})
    host = urlparse(url).hostname or "the server"
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        hint = " (was the secret address reset?)" if exc.code in (401, 403, 404) else ""
        raise CalendarError(f"{host} answered HTTP {exc.code}{hint}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise CalendarError(f"{host} is unreachable ({reason})") from None
    if len(data) > MAX_BYTES:
        raise CalendarError(f"the feed is larger than {MAX_BYTES // 1_000_000} MB")
    return data

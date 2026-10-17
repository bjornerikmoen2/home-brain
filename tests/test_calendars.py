from __future__ import annotations

import datetime as dt
import io
import urllib.error

import pytest
from conftest import CALENDAR_URL, FakeFeeds, fake_calendars

from home_brain import briefing, calendars
from home_brain.agenda import Note, build
from home_brain.calendars import CalendarError
from home_brain.index import Index

MON = dt.date(2026, 10, 19)
SUN = dt.date(2026, 10, 25)


def by_day(items: list) -> dict[str, list[str]]:
    days: dict[str, list[str]] = {}
    for item in sorted(items, key=lambda i: (i.date, i.time or "")):
        days.setdefault(item.date.isoformat(), []).append(
            f"{item.time or ''}{'–' + item.end_time if item.end_time else ''} {item.title} [{item.kind}]".strip()
        )
    return days


def test_events_become_agenda_items() -> None:
    items, warnings = fake_calendars().items(MON, SUN)
    assert warnings == []
    assert by_day(items) == {
        "2026-10-20": ["18:00–19:30 Football practice (late) [recurring]"],
        "2026-10-21": ["10:00–11:00 Dentist [event]"],  # 08:00 UTC; the cancelled lunch is left out
        "2026-10-23": ["Cabin weekend [event]"],
        "2026-10-24": ["14:00–17:00 Per's birthday party [event]"],
        "2026-10-22": ["19:00–21:00 Book club [event]"],  # monthly, so not a routine
    }
    cabin = next(i for i in items if i.title == "Cabin weekend")
    assert cabin.end_date == dt.date(2026, 10, 25)  # DTEND is the day after
    dentist = next(i for i in items if i.title == "Dentist")
    assert dentist.notes == "Bring the health card\nRoom 2"
    party = next(i for i in items if i.title == "Per's birthday party")
    assert (party.where, party.source, party.extra) == ("Eksempelveien 12, 0123 Oslo", "calendar", {"calendar": "Family"})


def test_exdates_and_events_that_started_earlier() -> None:
    items, _ = fake_calendars().items(dt.date(2026, 10, 24), dt.date(2026, 11, 3))
    days = by_day(items)
    assert "2026-10-27" not in days  # football is off that week
    assert days["2026-11-03"] == ["17:00–18:30 Football practice [recurring]"]
    # The cabin weekend began on Friday: it shows from the first day asked for, still with its end.
    cabin = next(i for i in items if i.title == "Cabin weekend")
    assert (cabin.date, cabin.end_date) == (dt.date(2026, 10, 24), dt.date(2026, 10, 25))


def test_feeds_are_cached_for_a_few_minutes() -> None:
    feeds, clock = FakeFeeds(), [0.0]
    cals = fake_calendars(feeds, clock=lambda: clock[0])
    cals.items(MON, MON)
    cals.items(MON, SUN)
    assert feeds.downloads == 1
    clock[0] += calendars.CACHE_SECONDS
    cals.items(MON, MON)
    assert feeds.downloads == 2


def test_a_broken_feed_gives_a_warning_and_the_others_still_work() -> None:
    other = "https://calendar.example.com/other/private-t0ps3cret/basic.ics"
    feeds = FakeFeeds({CALENDAR_URL: b"this is not iCal", other: b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"})
    items, warnings = fake_calendars(feeds, urls=[other, CALENDAR_URL]).items(MON, SUN)
    assert items == []
    assert warnings == ["Calendar 'calendar 2' could not be read: the feed is not a valid iCal file"]
    # Once a feed has been read, warnings use its name.
    feeds = FakeFeeds()
    cals = fake_calendars(feeds, clock=lambda: 1000.0 if feeds.error else 0.0)
    cals.items(MON, MON)
    feeds.error = CalendarError("calendar.example.com answered HTTP 404 (was the secret address reset?)")
    _, warnings = cals.items(MON, MON)
    assert warnings == ["Calendar 'Family' could not be read: calendar.example.com answered HTTP 404 (was the secret address reset?)"]


def test_download_errors_never_show_the_secret_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request, timeout):  # noqa: ANN001
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO())

    monkeypatch.setattr(calendars.urllib.request, "urlopen", refuse)
    with pytest.raises(CalendarError) as caught:
        calendars._download(CALENDAR_URL)
    assert str(caught.value) == "calendar.example.com answered HTTP 404 (was the secret address reset?)"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_a_note_and_a_calendar_event_for_the_same_thing_become_one_item(index: Index) -> None:
    notes = [Note(path, title, meta) for path, title, meta in index.frontmatter()]
    day = dt.date(2026, 10, 24)
    items, _ = fake_calendars().items(day, day)
    agenda = build(notes, day, day, MON, extra=items)
    [cabin, party] = sorted(agenda["days"][0]["items"], key=lambda i: i["title"])
    # The note's details (who, where, bring) win; the calendar adds the end time.
    assert (party["source"], party["time"], party["end_time"], party["where"]) == (
        "events/2026-10-24-per-birthday-party.md", "14:00", "17:00", "Eksempelveien 12")
    assert cabin["source"] == "calendar"
    message = briefing.tomorrow(agenda, day)
    assert message.text == (
        "Cabin weekend (until Sun 25 Oct)\n14:00–17:00 Per's birthday party (Ola), Eksempelveien 12\n\nBring:\nOla: present"
    )


def test_briefings_say_what_is_missing() -> None:
    day = dt.date(2026, 10, 20)
    empty = build([], day, day, MON)
    message = briefing.tomorrow(empty, day, warnings=["Calendar 'Family' could not be read: the feed is not a valid iCal file"])
    assert message.text == "Nothing on the agenda.\n\n⚠ Calendar 'Family' could not be read: the feed is not a valid iCal file"
    assert message.priority == "default"
    week = briefing.week_ahead(build([], MON, SUN, MON), MON, SUN, ["Calendar 'Family' could not be read"])
    assert week.text == "Only the usual routine this week.\n\n⚠ Calendar 'Family' could not be read"

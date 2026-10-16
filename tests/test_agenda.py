from __future__ import annotations

import datetime as dt

import pytest

from home_brain.agenda import Note, build, check, needs_from

TUE = dt.date(2026, 10, 20)  # a Tuesday in week 43

WEEKLY = Note("school/ola/_weekly.md", "Ola – weekly", {
    "type": "school-weekly",
    "who": "ola",
    "recurring": [
        {"rrule": "FREQ=WEEKLY;BYDAY=TU", "title": "PE (kroppsøving)", "bring": ["gym clothes", "shower clothes"]},
        {"rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA,SU", "title": "Read 15 minutes"},
        {"rrule": "FREQ=WEEKLY;BYDAY=TH", "time": "18:00", "title": "Scouts (speider)", "where": "Scout hut"},
    ],
    "events": [{"date": "2026-12-21", "end_date": "2027-01-01", "title": "Christmas break", "break": True}],
})
WEEK = Note("school/ola/2026-w43.md", "Ola – week 43", {
    "type": "school-week",
    "who": "ola",
    "events": [
        {"date": "2026-10-21", "title": "Forest trip", "bring": ["packed lunch", "rain gear"]},
        {"date": "2026-10-22", "time": "09:00", "title": "Dentist", "where": "Eksempelby"},
    ],
})
KID = Note("people/kids/ola.md", "Ola", {"type": "person", "name": "Ola", "birthday": "2018-10-23"})
CAR = Note("vehicles/family-car.md", "Family car", {
    "type": "vehicle",
    "due": [
        {"date": "2026-11-01", "title": "Fit winter tires", "warn_days": 14},
        {"date": "2027-03-01", "title": "EU-kontroll", "warn_days": 90},
        {"date": "2026-10-10", "title": "Book service"},
        {"date": "2026-10-01", "title": "Old and done", "done": True},
    ],
})
REMINDERS = Note("reminders.md", "Reminders", {
    "reminders": [{"at": "2026-10-20T18:00", "text": "Buy a present", "who": "me"}],
})
NOTES = [WEEKLY, WEEK, KID, CAR, REMINDERS]


def agenda(start: dt.date, days: int = 1, notes=NOTES, who: str | None = None) -> dict:
    return build(notes, start, start + dt.timedelta(days=days - 1), today=start, who=who)


def titles(result: dict, day: str) -> list[str]:
    for entry in result["days"]:
        if entry["date"] == day:
            return [i["title"] for i in entry["items"]]
    return []


def test_one_day_combines_routine_events_and_reminders() -> None:
    result = agenda(TUE)
    assert [d["date"] for d in result["days"]] == ["2026-10-20"]
    day = result["days"][0]
    assert day["weekday"] == "Tuesday"
    assert [i["title"] for i in day["items"]] == ["PE (kroppsøving)", "Read 15 minutes", "Buy a present"]
    assert day["bring"] == {"ola": ["gym clothes", "shower clothes"]}
    reminder = day["items"][-1]
    assert (reminder["kind"], reminder["time"], reminder["who"]) == ("reminder", "18:00", ["me"])
    assert result["sources"] == ["reminders.md", "school/ola/_weekly.md", "vehicles/family-car.md"]


def test_a_week_with_events_birthdays_and_deadlines() -> None:
    result = agenda(dt.date(2026, 10, 19), days=7)
    assert titles(result, "2026-10-21") == ["Forest trip", "Read 15 minutes"]
    assert titles(result, "2026-10-22") == ["Read 15 minutes", "Dentist", "Scouts (speider)"]  # untimed first, then by time
    assert titles(result, "2026-10-23") == ["Ola's birthday (turns 8)", "Read 15 minutes"]
    wed = next(d for d in result["days"] if d["date"] == "2026-10-21")
    assert wed["bring"] == {"ola": ["packed lunch", "rain gear"]}
    upcoming = {i["title"]: i for i in result["upcoming"]}
    assert set(upcoming) == {"Fit winter tires", "Book service"}  # EU-kontroll is outside its 90-day window
    assert upcoming["Fit winter tires"]["days_left"] == 13
    assert upcoming["Book service"]["overdue"] is True
    assert "Old and done" not in str(result)


def test_due_inside_the_range_is_a_day_item() -> None:
    result = agenda(dt.date(2026, 11, 1))
    assert "Fit winter tires" in titles(result, "2026-11-01")


def test_breaks_skip_routine_items_from_the_same_folder() -> None:
    result = agenda(dt.date(2026, 12, 21), days=3)
    assert titles(result, "2026-12-21") == ["Christmas break"]
    assert result["days"][0]["items"][0]["until"] == "2027-01-01"
    assert [d["date"] for d in result["days"]] == ["2026-12-21"]  # shown once, no routine items after


def test_cancels_skips_matching_routine_items_that_day() -> None:
    cancelled = Note("school/ola/2026-w43b.md", "", {"who": "ola", "events": [
        {"date": "2026-10-20", "title": "PE cancelled, gym is closed", "cancels": "PE"},
    ]})
    assert titles(agenda(TUE, notes=[WEEKLY, cancelled]), "2026-10-20") == ["PE cancelled, gym is closed", "Read 15 minutes"]
    other_kid = Note("school/nora/x.md", "", {"who": "nora", "events": [{"date": "2026-10-20", "title": "No PE", "cancels": "PE"}]})
    assert "PE (kroppsøving)" in titles(agenda(TUE, notes=[WEEKLY, other_kid]), "2026-10-20")
    thursday = Note("school/ola/w.md", "", {"who": "ola", "events": [{"date": "2026-10-22", "title": "No PE", "cancels": "pe"}]})
    assert "Scouts (speider)" in titles(agenda(dt.date(2026, 10, 22), notes=[WEEKLY, thursday]), "2026-10-22")  # whole words only


def test_event_repeating_a_routine_item_is_listed_once() -> None:
    letter = Note("school/ola/2026-w43.md", "", {"who": "ola", "events": [
        {"date": "2026-10-20", "title": "PE (Kroppsøving)!", "notes": "Outdoors this week", "bring": ["warm jacket"]},
    ]})
    day = agenda(TUE, notes=[WEEKLY, letter])["days"][0]
    pe = [i for i in day["items"] if i["title"].lower().startswith("pe")]
    assert len(pe) == 1
    assert (pe[0]["kind"], pe[0]["notes"]) == ("event", "Outdoors this week")
    assert day["bring"] == {"ola": ["warm jacket", "gym clothes", "shower clothes"]}


def test_who_filter() -> None:
    assert titles(agenda(TUE, who="me"), "2026-10-20") == ["Buy a present"]
    assert titles(agenda(TUE, who="@Ola"), "2026-10-20") == ["PE (kroppsøving)", "Read 15 minutes"]
    assert agenda(TUE, who="nobody")["days"] == []


def test_birthday_on_29_february_and_newborns() -> None:
    leap = Note("people/leap.md", "", {"name": "Leap", "birthday": "2016-02-29"})
    assert titles(agenda(dt.date(2027, 2, 28), notes=[leap]), "2027-02-28") == ["Leap's birthday (turns 11)"]
    baby = Note("people/baby.md", "", {"name": "Baby", "birthday": "2026-10-20"})
    assert titles(agenda(TUE, notes=[baby]), "2026-10-20") == ["Baby's birthday"]


def test_rrule_with_interval_uses_from() -> None:
    every_other = Note("x/bins.md", "", {"recurring": [
        {"rrule": "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", "from": "2026-10-05", "title": "Paper bin"},
    ]})
    result = agenda(dt.date(2026, 10, 5), days=29, notes=[every_other])
    assert [d["date"] for d in result["days"]] == ["2026-10-05", "2026-10-19", "2026-11-02"]


def test_until_and_exdates() -> None:
    note = Note("x/swim.md", "", {"recurring": [{
        "rrule": "FREQ=WEEKLY;BYDAY=TU", "title": "Swimming", "until": "2026-11-03", "exdates": ["2026-10-27"],
    }]})
    result = agenda(dt.date(2026, 10, 19), days=28, notes=[note])
    assert [d["date"] for d in result["days"]] == ["2026-10-20", "2026-11-03"]


def test_invalid_items_are_skipped_and_reported() -> None:
    broken = Note("x/broken.md", "", {
        "birthday": "someday",
        "events": [{"date": "2026-10-32", "title": "Bad"}, {"date": "2026-10-20", "title": "Good"}, "not a mapping"],
        "recurring": [{"rrule": "FREQ=HOURLY", "title": "Too often"}, {"rrule": "BYDAY=TU", "title": "No freq"}],
        "due": [{"date": "2026-10-20", "title": "x", "warn_days": "soon"}],
    })
    result = agenda(TUE, notes=[broken])
    assert titles(result, "2026-10-20") == ["Good"]
    problems = "\n".join(result["problems"])
    for expected in ("birthday", "events[0].date", "events[2]", "recurring[0].rrule", "recurring[1].rrule", "due[0].warn_days"):
        assert f"x/broken.md: {expected}" in problems


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("FREQ=WEEKLY;BYDAY=TU", False),
        ("FREQ=WEEKLY", True),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", True),
        ("FREQ=DAILY;COUNT=5", True),
        ("FREQ=MONTHLY;BYMONTHDAY=15", False),
        ("FREQ=MONTHLY", True),
        ("FREQ=YEARLY;BYMONTH=5", True),
        ("FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=17", False),
        ("FREQ=DAILY", False),
    ],
)
def test_needs_from(rule: str, expected: bool) -> None:
    assert needs_from(rule) is expected


def test_check_messages() -> None:
    assert check(WEEKLY.meta) == []
    assert check({"events": [{"date": "2026-10-20", "title": "x", "time": "25:00"}]}) == [
        "events[0].time: '25:00' is not a time (use \"HH:MM\")"
    ]
    assert check({"recurring": [{"rrule": "FREQ=WEEKLY", "title": "x"}]}) == [
        "recurring[0]: this rrule depends on its start date; add `from: YYYY-MM-DD`"
    ]
    assert check({"reminders": [{"at": "next week", "text": "x"}]})[0].startswith("reminders[0].at:")
    assert check({"events": {"date": "2026-10-20"}}) == ["events: must be a list of items"]
    assert check({"due": [{"title": "x", "date": "2026-10-20", "end_date": "2026-10-19"}]}) == ["due[0].end_date: is before date"]

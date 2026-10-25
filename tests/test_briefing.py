from __future__ import annotations

import datetime as dt

import pytest
from conftest import CHORES

from home_brain import briefing, frontmatter
from home_brain.agenda import Note, build
from home_brain.homeboard import agenda_items
from home_brain.index import Index
from home_brain.texts import NB
from home_brain.vault import Vault

MON = dt.date(2026, 10, 19)


@pytest.fixture
def notes(index: Index) -> list[Note]:
    return [Note(path, title, meta) for path, title, meta in index.frontmatter()]


def day_agenda(notes: list[Note], day: dt.date) -> dict:
    return build(notes, day, day, MON)


def test_tomorrow_with_packing_list(notes: list[Note]) -> None:
    day = dt.date(2026, 10, 24)
    message = briefing.tomorrow(day_agenda(notes, day), day)
    assert message.title == "Tomorrow: Saturday 24 October"
    assert message.text == "14:00 Per's birthday party (Ola), Eksempelveien 12\n\nBring:\nOla: present"
    assert message.priority == "default"


def test_tomorrow_with_chores(notes: list[Note]) -> None:
    day = dt.date(2026, 10, 20)
    agenda = build(notes, day, day, MON, extra=agenda_items(CHORES, MON))
    message = briefing.tomorrow(agenda, day)
    assert message.text == (
        "Gym (Ola)\n\nBring:\nOla: gym clothes, indoor shoes\n\n"
        "Chores:\nOla: Make bed (by 08:00)\nKids (shared): Empty dishwasher"
    )


def test_tomorrow_with_chores_waiting_for_approval(notes: list[Note]) -> None:
    day = dt.date(2026, 10, 20)
    message = briefing.tomorrow(build(notes, day, day, MON), day, waiting=["Nora: 1 chore (10 points)"])
    assert message.text.endswith("\n\nWaiting for your approval:\nNora: 1 chore (10 points)")
    quiet = briefing.tomorrow(build([], day, day, MON), day, waiting=["Nora: 1 chore (10 points)"])
    assert (quiet.text, quiet.priority) == ("Nothing on the agenda.\n\nWaiting for your approval:\nNora: 1 chore (10 points)", "default")


def test_chores_alone_and_not_in_the_week_ahead() -> None:
    day = dt.date(2026, 10, 20)
    chores = agenda_items(CHORES, MON)
    assert briefing.tomorrow(build([], day, day, MON, extra=chores), day).text.startswith("Chores:\nOla: Make bed")
    week = briefing.week_ahead(build([], MON, day, MON, extra=chores), MON, day)
    assert week.text == "Only the usual routine this week."


def test_tomorrow_with_nothing_on(notes: list[Note]) -> None:
    day = dt.date(2026, 10, 25)
    message = briefing.tomorrow(day_agenda(notes, day), day)
    assert (message.text, message.priority) == ("Nothing on the agenda.", "low")


def test_week_ahead_leaves_out_routines(notes: list[Note]) -> None:
    end = MON + dt.timedelta(days=6)
    message = briefing.week_ahead(build(notes, MON, end, MON), MON, end)
    assert message.title == "Week ahead: 19 Oct – 25 Oct"
    lines = message.text.splitlines()
    assert lines[0] == "Wed 21 Oct: Forest trip (Ola)"
    assert lines[-1] == "Sat 24 Oct: 14:00 Per's birthday party (Ola), Eksempelveien 12"
    assert "Gym" not in message.text


def test_week_ahead_with_only_routines() -> None:
    gym = Note("school/ola/_weekly.md", "Weekly", {"who": "ola", "recurring": [{"rrule": "FREQ=WEEKLY;BYDAY=TU", "title": "Gym"}]})
    message = briefing.week_ahead(build([gym], MON, MON + dt.timedelta(days=6), MON), MON, MON + dt.timedelta(days=6))
    assert (message.text, message.priority) == ("Only the usual routine this week.", "low")


def test_monday_check_on_the_starter_vault(notes: list[Note], vault: Vault) -> None:
    message = briefing.maintenance(notes, MON, lambda path: frontmatter.split(vault.read(path)[1])[1])
    assert message is not None
    assert message.title == "Monday check"
    assert message.text == (
        "Deadlines\n"
        "• Change ventilation filter: Sun 1 Nov (in 13 days)\n"
        "\n"
        "Birthdays\n"
        "• Kari Nordmann's birthday (turns 38): Mon 2 Nov\n"
        "  Gift ideas: Pottery course (mentioned 2026-09-12); New thermos for hiking\n"
        "\n"
        "Tires\n"
        "• Family car: time to change to winter tires? (last change Fri 10 Apr 2026)"
    )


def test_monday_check_with_nothing_to_report() -> None:
    assert briefing.maintenance([], MON, lambda path: "") is None


def test_monday_check_lists_overdue_deadlines_and_bad_dates() -> None:
    house = Note("house/log.md", "Log", {"due": [{"date": "2026-10-16", "title": "Pay the plumber"}, {"date": "2026-10-19", "title": "Book chimney sweep"}]})
    broken = Note("events/x.md", "X", {"events": [{"date": "2026-10-32", "title": "Typo"}]})
    message = briefing.maintenance([house, broken], MON, lambda path: "")
    assert message is not None
    assert "• Book chimney sweep: Mon 19 Oct (today)\n• Pay the plumber: Fri 16 Oct (3 days overdue)" in message.text
    assert "Dates Claude should fix in the notes\n• events/x.md: events[0].date: '2026-10-32' is not a date" in message.text


@pytest.mark.parametrize(
    ("tires", "changes", "today", "expected"),
    [
        ({"winter": {"tread_mm": 3.5}}, ["2026-10-01"], MON, ["Car: winter tires have 3.5 mm tread (legal minimum 3 mm)"]),
        ({"summer": {"tread_mm": 3.0}}, [], MON, ["Car: summer tires have 3 mm tread (legal minimum 1.6 mm)"]),
        ({"winter": {"tread_mm": 4.5}, "summer": {"tread_mm": 6}}, ["2026-10-01"], MON, []),
        ({"winter": {"bought": "2019-10"}}, ["2026-10-01"], MON, ["Car: winter tires are 7 years old (bought 2019-10)"]),
        ({"winter": {"bought": 2021}}, ["2026-10-01"], MON, []),
        ({"winter": {}}, ["2026-04-10"], dt.date(2026, 11, 16), []),
        ({"winter": {}}, ["2025-10-28"], dt.date(2026, 4, 6), ["Car: time to change to summer tires? (last change Tue 28 Oct 2025)"]),
        ({"winter": {}}, [], dt.date(2026, 4, 6), ["Car: time to change to summer tires?"]),
        ({"summer": {}}, [], MON, []),
        # Studded: allowed from 1 Nov, off after the Sunday after Easter Monday (Easter 2027: 28 Mar).
        ({"winter": {"studded": True}}, [], MON, ["Car: time to change to winter tires? Studs are allowed from 1 Nov."]),
        ({"winter": {"studded": True}}, [], dt.date(2026, 11, 2), ["Car: time to change to winter tires?"]),
        ({"winter": {"studded": True}}, [], dt.date(2027, 3, 22), ["Car: time to change to summer tires? Studs must be off after Sun 4 Apr."]),
        ({"winter": {"studded": True}}, [], dt.date(2027, 4, 12), ["Car: time to change to summer tires?"]),
        ({"winter": {"studded": False}}, [], dt.date(2027, 3, 22), []),
    ],
)
def test_tire_checks(tires: dict, changes: list[str], today: dt.date, expected: list[str]) -> None:
    car = Note("vehicles/car.md", "car", {"name": "Car", "tires": tires, "tire_changes": changes})
    message = briefing.maintenance([car], today, lambda path: "")
    found = [] if message is None else [line.removeprefix("• ") for line in message.text.splitlines()[1:]]
    assert found == expected


def test_reminder_messages() -> None:
    due = dt.datetime(2026, 10, 20, 18, 0)
    on_time = briefing.reminder("Pack gym clothes", ["ola", "me"], due, due)
    assert (on_time.title, on_time.text, on_time.priority) == ("Reminder for Ola", "Pack gym clothes", "high")
    late = briefing.reminder("Buy milk", ["me"], due, dt.datetime(2026, 10, 21, 0, 30))
    assert (late.title, late.text) == ("Reminder", "Buy milk\n(due Tue 20 Oct 18:00)")


def test_section_items() -> None:
    body = (
        "# Kari\n\n## Likes\n- Coffee\n\n## Gaveideer\n- Pottery course, seen in town. Considered for her\n"
        "  birthday but not bought.\n* Thermos\n1. Book\n\nSome text.\n### Old\n- Scarf\n"
    )
    assert briefing.section_items(body, briefing.GIFT_HEADINGS) == [
        "Pottery course, seen in town. Considered for her birthday but not bought.", "Thermos", "Book",
    ]
    assert briefing.section_items(body, ("dislikes",)) == []


def test_line_and_names() -> None:
    item = {"kind": "break", "title": "Autumn break", "until": "2026-10-04", "who": ["ola", "me"]}
    assert briefing.line(item) == "Autumn break (until Sun 4 Oct) (Ola, you)"
    item = {"kind": "due", "title": "EU-kontroll", "time": "08:00", "end_time": "09:00", "who": ["nora-marie"]}
    assert briefing.line(item) == "08:00–09:00 Due: EU-kontroll (Nora Marie)"


# --- in Norwegian (NOTIFY_LANGUAGE=nb) -------------------------------------------


def test_tomorrow_in_norwegian(notes: list[Note]) -> None:
    day = dt.date(2026, 10, 24)
    message = briefing.tomorrow(day_agenda(notes, day), day, t=NB)
    assert message.title == "I morgen: lørdag 24. oktober"
    assert message.text == "14:00 Per's birthday party (Ola), Eksempelveien 12\n\nTa med:\nOla: present"
    day = dt.date(2026, 10, 20)
    message = briefing.tomorrow(build(notes, day, day, MON, extra=agenda_items(CHORES, MON)), day, t=NB)
    assert message.text == (
        "Gym (Ola)\n\nTa med:\nOla: gym clothes, indoor shoes\n\n"
        "Oppgaver:\nOla: Make bed (innen 08:00)\nKids (felles): Empty dishwasher"
    )
    quiet = briefing.tomorrow(build([], day, day, MON), day, waiting=["Nora: 1 oppgave (10 poeng)"], warnings=["Oops."], t=NB)
    assert quiet.text == "Ingenting på planen.\n\nVenter på verifisering:\nNora: 1 oppgave (10 poeng)\n\n⚠ Oops."


def test_week_ahead_in_norwegian(notes: list[Note]) -> None:
    end = MON + dt.timedelta(days=6)
    message = briefing.week_ahead(build(notes, MON, end, MON), MON, end, t=NB)
    assert message.title == "Uka som kommer: 19. okt. – 25. okt."
    assert message.text.splitlines()[0] == "ons. 21. okt.: Forest trip (Ola)"
    quiet = briefing.week_ahead(build([], MON, end, MON), MON, end, t=NB)
    assert quiet.text == "Bare vanlig rutine denne uka."


def test_monday_check_in_norwegian(notes: list[Note], vault: Vault) -> None:
    message = briefing.maintenance(notes, MON, lambda path: frontmatter.split(vault.read(path)[1])[1], t=NB)
    assert message is not None
    assert message.title == "Mandagssjekk"
    assert message.text == (
        "Frister\n"
        "• Change ventilation filter: søn. 1. nov. (om 13 dager)\n"
        "\n"
        "Bursdager\n"
        "• Kari Nordmann fyller 38: man. 2. nov.\n"
        "  Gaveideer: Pottery course (mentioned 2026-09-12); New thermos for hiking\n"
        "\n"
        "Dekk\n"
        "• Family car: på tide å bytte til vinterdekk? (sist byttet fre. 10. apr. 2026)"
    )
    house = Note("house/log.md", "Log", {"due": [{"date": "2026-10-16", "title": "Pay the plumber"}, {"date": "2026-10-19", "title": "Book chimney sweep"}]})
    broken = Note("events/x.md", "X", {"events": [{"date": "2026-10-32", "title": "Typo"}]})
    message = briefing.maintenance([house, broken], MON, lambda path: "", t=NB)
    assert message is not None
    assert "• Book chimney sweep: man. 19. okt. (i dag)\n• Pay the plumber: fre. 16. okt. (3 dager over fristen)" in message.text
    assert "Datoer Claude bør rette i notatene\n• events/x.md: events[0].date:" in message.text


@pytest.mark.parametrize(
    ("tires", "changes", "today", "expected"),
    [
        ({"winter": {"tread_mm": 3.5}}, ["2026-10-01"], MON, "Car: vinterdekkene har 3,5 mm mønsterdybde (lovlig minimum 3 mm)"),
        ({"summer": {"tread_mm": 1.5}}, [], MON, "Car: sommerdekkene har 1,5 mm mønsterdybde (lovlig minimum 1,6 mm)"),
        ({"winter": {"bought": "2019-10"}}, ["2026-10-01"], MON, "Car: vinterdekkene er 7 år gamle (kjøpt 2019-10)"),
        ({"winter": {"studded": True}}, [], MON, "Car: på tide å bytte til vinterdekk? Piggdekk er lov fra 1. november."),
        ({"winter": {"studded": True}}, [], dt.date(2027, 3, 22), "Car: på tide å bytte til sommerdekk? Etter søn. 4. apr. må piggdekkene av."),
    ],
)
def test_tire_checks_in_norwegian(tires: dict, changes: list[str], today: dt.date, expected: str) -> None:
    car = Note("vehicles/car.md", "car", {"name": "Car", "tires": tires, "tire_changes": changes})
    message = briefing.maintenance([car], today, lambda path: "", t=NB)
    assert message is not None and message.text == f"Dekk\n• {expected}"


def test_reminders_and_lines_in_norwegian() -> None:
    due = dt.datetime(2026, 10, 20, 18, 0)
    assert briefing.reminder("Pakk gymtøy", ["ola", "me"], due, due, NB).title == "Påminnelse for Ola"
    late = briefing.reminder("Kjøp melk", ["me"], due, dt.datetime(2026, 10, 21, 0, 30), NB)
    assert (late.title, late.text) == ("Påminnelse", "Kjøp melk\n(skulle kommet tir. 20. okt. 18:00)")
    item = {"kind": "break", "title": "Høstferie", "until": "2026-10-04", "who": ["ola", "me"]}
    assert briefing.line(item, NB) == "Høstferie (til søn. 4. okt.) (Ola, du)"
    item = {"kind": "due", "title": "EU-kontroll", "time": "08:00", "who": ["nora-marie"]}
    assert briefing.line(item, NB) == "08:00 Frist: EU-kontroll (Nora Marie)"
    item = {"kind": "birthday", "title": "Kari's birthday", "name": "Kari", "age": 0, "who": ["kari"]}
    assert (briefing.line(item, NB), briefing.line(item)) == ("Kari har bursdag", "Kari's birthday")

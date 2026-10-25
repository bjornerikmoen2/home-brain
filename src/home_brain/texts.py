"""The words in the push notifications, in English or Norwegian (NOTIFY_LANGUAGE).

Only what goes to the phone is translated. Tool results stay in English: Claude answers in the
user's language anyway. Templates use str.format; a translation may leave out a placeholder
(e.g. the technical reason a calendar failed, which is in the log) but never add one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Texts:
    weekdays: tuple[str, ...]
    """Monday first."""
    weekdays_short: tuple[str, ...]
    months: tuple[str, ...]
    months_short: tuple[str, ...]
    long_date_format: str
    """{weekday} {day} {month}, with the full names."""
    short_date_format: str
    """{weekday} {day} {month}, with the short names."""
    day_month_format: str
    decimal_comma: bool

    # names
    you: str
    everyone: str
    shared: str

    # reminders
    reminder: str
    reminder_for: str
    sent_late: str

    # the evening briefing and the week ahead
    birthday: str
    birthday_age: str
    tomorrow: str
    nothing_planned: str
    bring: str
    chores: str
    waiting_for_approval: str
    waiting_one: str
    waiting_many: str
    week_ahead: str
    usual_routine: str
    due_prefix: str
    reminder_prefix: str
    until: str
    by_time: str
    homeboard_unreachable: str
    unnamed_calendar: str
    calendar_unreadable: str

    # the Monday check
    monday_check: str
    deadlines: str
    birthdays: str
    tires: str
    fix_dates: str
    and_more: str
    today: str
    in_days: str
    overdue: str
    one_day: str
    n_days: str
    gift_ideas: str
    winter_tires: str
    summer_tires: str
    tread: str
    tire_age: str
    last_change: str
    to_winter: str
    to_summer: str
    studs_allowed: str
    studs_off: str

    def long_date(self, day: dt.date) -> str:
        return self.long_date_format.format(weekday=self.weekdays[day.weekday()], day=day.day, month=self.months[day.month - 1])

    def short_date(self, day: dt.date) -> str:
        return self.short_date_format.format(
            weekday=self.weekdays_short[day.weekday()], day=day.day, month=self.months_short[day.month - 1]
        )

    def day_month(self, day: dt.date) -> str:
        return self.day_month_format.format(day=day.day, month=self.months_short[day.month - 1])

    def number(self, value: float) -> str:
        text = f"{value:g}"
        return text.replace(".", ",") if self.decimal_comma else text


EN = Texts(
    weekdays=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    weekdays_short=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    months=(
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
    months_short=("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    long_date_format="{weekday} {day} {month}",
    short_date_format="{weekday} {day} {month}",
    day_month_format="{day} {month}",
    decimal_comma=False,
    you="you",
    everyone="everyone",
    shared="{who} (shared)",
    reminder="Reminder",
    reminder_for="Reminder for {names}",
    sent_late="(due {when})",
    birthday="{name}'s birthday",
    birthday_age="{name}'s birthday (turns {age})",
    tomorrow="Tomorrow: {date}",
    nothing_planned="Nothing on the agenda.",
    bring="Bring:",
    chores="Chores:",
    waiting_for_approval="Waiting for your approval:",
    waiting_one="{name}: 1 chore ({points} points)",
    waiting_many="{name}: {count} chores ({points} points)",
    week_ahead="Week ahead: {start} – {end}",
    usual_routine="Only the usual routine this week.",
    due_prefix="Due: ",
    reminder_prefix="Reminder: ",
    until="(until {date})",
    by_time=" (by {time})",
    homeboard_unreachable="Home-Board could not be reached, so the chores are missing.",
    unnamed_calendar="calendar {number}",
    calendar_unreadable="Calendar {name!r} could not be read: {reason}",
    monday_check="Monday check",
    deadlines="Deadlines",
    birthdays="Birthdays",
    tires="Tires",
    fix_dates="Dates Claude should fix in the notes",
    and_more="…and {count} more",
    today="today",
    in_days="in {days}",
    overdue="{days} overdue",
    one_day="1 day",
    n_days="{count} days",
    gift_ideas="Gift ideas: {ideas}",
    winter_tires="winter tires",
    summer_tires="summer tires",
    tread="{car}: {tires} have {tread} mm tread (legal minimum {legal} mm)",
    tire_age="{car}: {tires} are {years} years old (bought {bought})",
    last_change=" (last change {date})",
    to_winter="{car}: time to change to winter tires?",
    to_summer="{car}: time to change to summer tires?",
    studs_allowed=" Studs are allowed from 1 Nov.",
    studs_off=" Studs must be off after {date}.",
)

NB = Texts(
    weekdays=("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"),
    weekdays_short=("man.", "tir.", "ons.", "tor.", "fre.", "lør.", "søn."),
    months=(
        "januar", "februar", "mars", "april", "mai", "juni",
        "juli", "august", "september", "oktober", "november", "desember",
    ),
    months_short=("jan.", "feb.", "mars", "apr.", "mai", "juni", "juli", "aug.", "sep.", "okt.", "nov.", "des."),
    long_date_format="{weekday} {day}. {month}",
    short_date_format="{weekday} {day}. {month}",
    day_month_format="{day}. {month}",
    decimal_comma=True,
    you="du",
    everyone="alle",
    shared="{who} (felles)",
    reminder="Påminnelse",
    reminder_for="Påminnelse for {names}",
    sent_late="(skulle kommet {when})",
    birthday="{name} har bursdag",
    birthday_age="{name} fyller {age}",
    tomorrow="I morgen: {date}",
    nothing_planned="Ingenting på planen.",
    bring="Ta med:",
    chores="Oppgaver:",
    waiting_for_approval="Venter på verifisering:",
    waiting_one="{name}: 1 oppgave ({points} poeng)",
    waiting_many="{name}: {count} oppgaver ({points} poeng)",
    week_ahead="Uka som kommer: {start} – {end}",
    usual_routine="Bare vanlig rutine denne uka.",
    due_prefix="Frist: ",
    reminder_prefix="Påminnelse: ",
    until="(til {date})",
    by_time=" (innen {time})",
    homeboard_unreachable="Fikk ikke kontakt med Home-Board, så oppgavene mangler.",
    unnamed_calendar="kalender {number}",
    calendar_unreadable="Kalenderen «{name}» kunne ikke leses.",
    monday_check="Mandagssjekk",
    deadlines="Frister",
    birthdays="Bursdager",
    tires="Dekk",
    fix_dates="Datoer Claude bør rette i notatene",
    and_more="…og {count} til",
    today="i dag",
    in_days="om {days}",
    overdue="{days} over fristen",
    one_day="1 dag",
    n_days="{count} dager",
    gift_ideas="Gaveideer: {ideas}",
    winter_tires="vinterdekkene",
    summer_tires="sommerdekkene",
    tread="{car}: {tires} har {tread} mm mønsterdybde (lovlig minimum {legal} mm)",
    tire_age="{car}: {tires} er {years} år gamle (kjøpt {bought})",
    last_change=" (sist byttet {date})",
    to_winter="{car}: på tide å bytte til vinterdekk?",
    to_summer="{car}: på tide å bytte til sommerdekk?",
    studs_allowed=" Piggdekk er lov fra 1. november.",
    # Not "after {date}." at the end: the short date already ends in a full stop.
    studs_off=" Etter {date} må piggdekkene av.",
)

LANGUAGES = {"en": EN, "nb": NB, "no": NB}
"""NOTIFY_LANGUAGE values; `no` is accepted for Norwegian (Bokmål)."""

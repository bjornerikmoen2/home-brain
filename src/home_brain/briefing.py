"""The text of the push notifications: reminders, the evening briefing about tomorrow, the week
ahead and the Monday maintenance check.

Everything dated comes from the agenda, so the pushes agree with what Claude answers. The texts
are fixed templates (no LLM): plain text, since not every ntfy app renders markdown.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

from dateutil.easter import easter

from .agenda import Note, as_date, as_list
from .agenda import build as build_agenda
from .notify import Message

BIRTHDAY_DAYS = 14
"""The Monday check lists birthdays this many days ahead, with gift ideas."""
GIFT_IDEAS = 3
GIFT_HEADINGS = ("gift ideas", "gaveideer")
TIRE_MAX_YEARS = 6
TREAD_MM = {"winter": (3.0, 4.0), "summer": (1.6, 3.0)}
"""Per tire set: Norway's legal minimum tread, and the depth at which to suggest new tires."""

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
_YEAR_MONTH = re.compile(r"^(\d{4})(?:-(\d{1,2}))?")


def reminder(text: str, who: list[str], due: dt.datetime, now: dt.datetime) -> Message:
    others = [p for p in who if p != "me"]
    title = f"Reminder for {names(others)}" if others else "Reminder"
    if now - due >= dt.timedelta(minutes=15):  # sent late, after downtime
        when = f"{due:%H:%M}" if due.date() == now.date() else f"{short_day(due.date())} {due:%H:%M}"
        text = f"{text}\n(due {when})"
    return Message(text, title=title, priority="high", tags=("bell",))


def tomorrow(
    agenda: dict[str, Any], day: dt.date, waiting: list[str] | None = None, warnings: list[str] | None = None
) -> Message:
    """The evening briefing. `waiting` lists the chores done today that a parent hasn't approved
    yet (from Home-Board), so the points can be approved before the day is over. `warnings` say
    what is missing because a calendar or Home-Board could not be read."""
    title = f"Tomorrow: {day:%A} {day.day} {day:%B}"
    entry = next((d for d in agenda["days"] if d["date"] == day.isoformat()), None)
    approve = ["", "Waiting for your approval:", *waiting] if waiting else []
    approve += _warnings(warnings)
    if entry is None:
        # Sent anyway: a quiet evening shows that the briefing still works.
        text = "\n".join(["Nothing on the agenda.", *approve])
        return Message(text, title=title, priority="default" if waiting or warnings else "low", tags=("calendar",))
    lines = [line(item) for item in entry["items"] if item["kind"] != "chore"]
    if entry.get("bring"):
        lines += ["", "Bring:"]
        lines += [f"{capitalize(names([who]))}: {', '.join(things)}" for who, things in entry["bring"].items()]
    if chores := _chores_by_person([i for i in entry["items"] if i["kind"] == "chore"]):
        lines += ["", "Chores:", *chores]
    return Message("\n".join(lines + approve).strip(), title=title, tags=("calendar",))


def week_ahead(agenda: dict[str, Any], start: dt.date, end: dt.date, warnings: list[str] | None = None) -> Message:
    """What's special in the coming week. Weekly routines and chores are left out: the evening
    briefings cover them, and listing every PE lesson would bury the news."""
    title = f"Week ahead: {start.day} {start:%b} – {end.day} {end:%b}"
    lines = []
    for entry in agenda["days"]:
        items = [i for i in entry["items"] if i["kind"] not in ("recurring", "chore")]
        if items:
            lines.append(f"{short_day(as_date(entry['date']))}: " + "; ".join(line(i) for i in items))
    if not lines and not warnings:
        return Message("Only the usual routine this week.", title=title, priority="low", tags=("spiral_calendar",))
    lines = lines or ["Only the usual routine this week."]
    return Message("\n".join(lines + _warnings(warnings)), title=title, tags=("spiral_calendar",))


def maintenance(notes: list[Note], today: dt.date, read_body: Callable[[str], str]) -> Message | None:
    """Deadlines within their warning time, birthdays with gift ideas, tire checks and broken dates.
    None when there is nothing to report."""
    sections = []
    now = build_agenda(notes, today, today, today)
    if deadlines := _deadlines(now):
        sections.append(("Deadlines", deadlines))
    if birthdays := _birthdays(notes, today, read_body):
        sections.append(("Birthdays", birthdays))
    if tires := [found for note in notes for found in _tires(note, today)]:
        sections.append(("Tires", tires))
    if problems := now.get("problems"):
        shown = [*problems[:3], f"…and {len(problems) - 3} more"] if len(problems) > 3 else problems
        sections.append(("Dates Claude should fix in the notes", shown))
    if not sections:
        return None
    text = "\n\n".join(heading + "\n" + "\n".join(f"• {entry}" for entry in entries) for heading, entries in sections)
    return Message(text, title="Monday check", tags=("wrench",))


# --- pieces ----------------------------------------------------------------------


def _deadlines(agenda: dict[str, Any]) -> list[str]:
    due = [i for d in agenda["days"] for i in d["items"] if i["kind"] == "due"] + agenda["upcoming"]
    lines = []
    for item in due:
        left = item["days_left"]
        if left == 0:
            when = "today"
        elif left < 0:
            when = f"{_days(-left)} overdue"
        else:
            when = f"in {_days(left)}"
        lines.append(f"{item['title']}: {short_day(as_date(item['date']))} ({when})")
    return lines


def _birthdays(notes: list[Note], today: dt.date, read_body: Callable[[str], str]) -> list[str]:
    agenda = build_agenda(notes, today, today + dt.timedelta(days=BIRTHDAY_DAYS), today)
    lines = []
    for item in (i for d in agenda["days"] for i in d["items"] if i["kind"] == "birthday"):
        lines.append(f"{item['title']}: {short_day(as_date(item['date']))}")
        ideas = section_items(read_body(item["source"]), GIFT_HEADINGS)[:GIFT_IDEAS]
        if ideas:
            lines[-1] += f"\n  Gift ideas: {'; '.join(ideas)}"
    return lines


def _tires(note: Note, today: dt.date) -> list[str]:
    tires = note.meta.get("tires")
    if not isinstance(tires, dict):
        return []
    car = str(note.meta.get("name") or note.title)
    found = []
    for season, (legal, suggest) in TREAD_MM.items():
        tire = tires.get(season)
        if not isinstance(tire, dict):
            continue
        tread = tire.get("tread_mm")
        if isinstance(tread, int | float) and not isinstance(tread, bool) and tread <= suggest:
            found.append(f"{car}: {season} tires have {tread:g} mm tread (legal minimum {legal:g} mm)")
        bought = _year_month(tire.get("bought"))
        if bought and (years := (today - bought).days // 365) >= TIRE_MAX_YEARS:
            found.append(f"{car}: {season} tires are {years} years old (bought {tire['bought']})")
    if isinstance(winter := tires.get("winter"), dict):
        changes = [d for d in (as_date(v) for v in as_list(note.meta.get("tire_changes"))) if d]
        last = max(changes, default=None)
        since = f" (last change {short_day(last)} {last.year})" if last else ""
        # Studded tires in most of Norway: from 1 November until the Sunday after Easter Monday.
        studded = winter.get("studded") is True
        studs_off = easter(today.year) + dt.timedelta(days=7)
        if _in_season(today, 10, 1, 11, 15) and not (last and last >= dt.date(today.year, 9, 1)):
            studs = " Studs are allowed from 1 Nov." if studded and today < dt.date(today.year, 11, 1) else ""
            found.append(f"{car}: time to change to winter tires?{since}{studs}")
        spring = min(dt.date(today.year, 4, 1), studs_off - dt.timedelta(days=14)) if studded else dt.date(today.year, 4, 1)
        if spring <= today <= dt.date(today.year, 5, 15) and not (last and last >= dt.date(today.year, 3, 1)):
            studs = f" Studs must be off after {short_day(studs_off)}." if studded and today <= studs_off else ""
            found.append(f"{car}: time to change to summer tires?{since}{studs}")
    return found


def _warnings(warnings: list[str] | None) -> list[str]:
    return ["", *(f"⚠ {w}" for w in warnings)] if warnings else []


def _chores_by_person(chores: list[dict[str, Any]]) -> list[str]:
    """One line per person, e.g. `Ola: Make bed (by 08:00), Feed the cat`; shared chores last."""
    lines: dict[str, list[str]] = {}
    for chore in chores:
        label = names(chore["who"]) if chore.get("who") else f"{capitalize(chore.get('shared_by', 'everyone'))} (shared)"
        lines.setdefault(capitalize(label), []).append(chore["title"] + (f" (by {chore['time']})" if chore.get("time") else ""))
    ordered = sorted(lines, key=lambda label: (label.endswith("(shared)"), label))
    return [f"{label}: {', '.join(lines[label])}" for label in ordered]


# --- formatting ------------------------------------------------------------------


def line(item: dict[str, Any]) -> str:
    """One agenda item as text, e.g. `14:00–16:00 Per's birthday party (Ola), Eksempelveien 12`."""
    parts = []
    if item.get("time"):
        parts.append(item["time"] + (f"–{item['end_time']}" if item.get("end_time") else ""))
    prefix = {"due": "Due: ", "reminder": "Reminder: "}.get(item["kind"], "")
    parts.append(prefix + item["title"])
    if item.get("until"):
        parts.append(f"(until {short_day(as_date(item['until']))})")
    if item.get("who") and item["kind"] != "birthday":
        parts.append(f"({names(item['who'])})")
    text = " ".join(parts)
    return f"{text}, {item['where']}" if item.get("where") else text


def names(slugs: list[str]) -> str:
    """Person slugs as names: `ola` → Ola, `me` → you."""
    return ", ".join("you" if s == "me" else s.replace("-", " ").title() for s in slugs)


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def short_day(day: dt.date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def section_items(body: str, headings: tuple[str, ...]) -> list[str]:
    """The list items under the first heading named one of `headings` (any level, any case)."""
    items: list[str] = []
    inside = False
    for text in body.splitlines():
        if match := _HEADING.match(text):
            if inside:
                break
            inside = match.group(1).casefold() in headings
        elif not inside:
            continue
        elif match := _BULLET.match(text):
            items.append(match.group(1))
        elif items and text[:1].isspace() and text.strip():  # a wrapped list item
            items[-1] += " " + text.strip()
    return items


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _year_month(value: Any) -> dt.date | None:
    """`2022-10`, `2022-10-05` or `2022` as a date (the first of the month or year)."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    match = _YEAR_MONTH.match(str(value or "").strip())
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2) or 1), 1)
    except ValueError:
        return None


def _in_season(day: dt.date, from_month: int, from_day: int, to_month: int, to_day: int) -> bool:
    return (from_month, from_day) <= (day.month, day.day) <= (to_month, to_day)

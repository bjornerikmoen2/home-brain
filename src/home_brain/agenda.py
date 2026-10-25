"""The agenda: everything dated in the vault, for a range of days.

It reads these frontmatter keys (documented in the vault's _conventions.md):

  events     one-off items: date, end_date, time, end_time, title, who, where, bring, notes,
             cancels (skip routine items whose title contains this text that day),
             break (true: skip routine items from the same folder for the whole event)
  recurring  routine items: rrule (RFC 5545), title, who, time, end_time, where, bring, notes,
             from, until, exdates
  birthday   on person notes; repeats every year
  due        deadlines: date, title, warn_days (default 14), done
  reminders  one-off reminders: at (date or date-time), text, who, done

`who` falls back to the note's own `who`, so a school note with `who: ola` doesn't have to
repeat it on every item. Invalid items are skipped and reported, never fatal.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from dateutil.rrule import rrule, rrulestr

DEFAULT_WARN_DAYS = 14
OVERDUE_DAYS = 30
"""Deadlines missed within this many days before the range are still reported."""

_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}:\d{2}))?")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_LIST_KEYS = {
    "events": ("date", "title"),
    "recurring": ("rrule", "title"),
    "due": ("date", "title"),
    "reminders": ("at", "text"),
}
_KIND_ORDER = {"break": 0, "birthday": 1, "due": 2, "event": 3, "recurring": 4, "reminder": 5, "chore": 6}


@dataclass(frozen=True)
class Note:
    path: str
    title: str
    meta: dict[str, Any]


@dataclass
class Item:
    date: dt.date
    kind: str
    title: str
    source: str
    time: str | None = None
    end_time: str | None = None
    end_date: dt.date | None = None
    who: list[str] = field(default_factory=list)
    where: str | None = None
    bring: list[str] = field(default_factory=list)
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"date": self.date.isoformat(), "kind": self.kind, "title": self.title}
        for key in ("time", "end_time", "where", "notes"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.end_date:
            out["until"] = self.end_date.isoformat()
        if self.who:
            out["who"] = self.who
        if self.bring:
            out["bring"] = self.bring
        out.update(self.extra)
        out["source"] = self.source
        return out


# --- parsing -------------------------------------------------------------------


def as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and (match := _DATE.match(value.strip())):
        try:
            return dt.date.fromisoformat(match.group(1))
        except ValueError:
            return None
    return None


def as_moment(value: Any) -> tuple[dt.date, str | None] | None:
    """A date with an optional "HH:MM" time, from `2026-10-20` or `2026-10-20T19:00`."""
    day = as_date(value)
    if day is None:
        return None
    match = _DATE.match(str(value).strip())
    clock = as_time(match.group(2)) if match and match.group(2) else None
    return day, clock


def as_time(value: Any) -> str | None:
    if value is None:
        return None
    match = _TIME.match(str(value).strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def people(value: Any) -> list[str]:
    return [p.lstrip("@").lower() for p in as_list(value)]


def parse_rrule(text: str, anchor: dt.date) -> rrule:
    rule = text.strip().removeprefix("RRULE:").removeprefix("rrule:")
    if "FREQ=" not in rule.upper():
        raise ValueError("needs FREQ=..., e.g. FREQ=WEEKLY;BYDAY=TU")
    return rrulestr(rule, dtstart=dt.datetime.combine(anchor, dt.time()))


def needs_from(text: str) -> bool:
    """Rules whose occurrences depend on their start date need an explicit `from`."""
    rule = text.upper()
    freq = (re.search(r"FREQ=(\w+)", rule) or [None, ""])[1]
    interval = re.search(r"INTERVAL=(\d+)", rule)
    if "COUNT=" in rule or (interval and int(interval.group(1)) > 1):
        return True
    if freq == "WEEKLY":
        return "BYDAY=" not in rule
    if freq == "MONTHLY":
        return not any(k in rule for k in ("BYMONTHDAY=", "BYDAY="))
    if freq == "YEARLY":
        on_a_day = "BYMONTH=" in rule and ("BYMONTHDAY=" in rule or "BYDAY=" in rule)
        return not (on_a_day or "BYYEARDAY=" in rule or ("BYWEEKNO=" in rule and "BYDAY=" in rule))
    return False


ALLOWED_FREQ = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}


# --- validation ----------------------------------------------------------------


def check(meta: dict[str, Any]) -> list[str]:
    """Problems with the agenda keys of one note's frontmatter, as readable messages."""
    problems: list[str] = []
    if "birthday" in meta and as_date(meta["birthday"]) is None:
        problems.append(f"birthday: {meta['birthday']!r} is not a date (use YYYY-MM-DD)")
    for key, required in _LIST_KEYS.items():
        if key not in meta or meta[key] is None:
            continue
        items = meta[key]
        if not isinstance(items, list):
            problems.append(f"{key}: must be a list of items")
            continue
        for i, item in enumerate(items):
            at = f"{key}[{i}]"
            if not isinstance(item, dict):
                problems.append(f"{at}: must be a mapping like {{{required[0]}: ..., {required[1]}: ...}}")
                continue
            problems.extend(f"{at}.{name}: missing" for name in required if item.get(name) in (None, ""))
            problems.extend(_check_item(key, at, item))
    return problems


def _check_item(key: str, at: str, item: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name in ("date", "end_date", "from", "until"):
        if item.get(name) not in (None, "") and as_date(item[name]) is None:
            problems.append(f"{at}.{name}: {item[name]!r} is not a date (use YYYY-MM-DD)")
    for name in ("time", "end_time"):
        if item.get(name) not in (None, "") and as_time(item[name]) is None:
            problems.append(f'{at}.{name}: {item[name]!r} is not a time (use "HH:MM")')
    start, end = as_date(item.get("date")), as_date(item.get("end_date"))
    if start and end and end < start:
        problems.append(f"{at}.end_date: is before date")
    if key == "reminders" and item.get("at") not in (None, "") and as_moment(item["at"]) is None:
        problems.append(f'{at}.at: {item["at"]!r} is not a date or date-time (use "YYYY-MM-DDTHH:MM")')
    if key == "due" and "warn_days" in item:
        warn = item["warn_days"]
        if isinstance(warn, bool) or not isinstance(warn, int) or warn < 0:
            problems.append(f"{at}.warn_days: must be a whole number of days")
    if key == "recurring" and item.get("rrule"):
        freq = (re.search(r"FREQ=(\w+)", str(item["rrule"]).upper()) or [None, ""])[1]
        try:
            parse_rrule(str(item["rrule"]), dt.date(2000, 1, 3))
        except (ValueError, TypeError) as exc:
            problems.append(f"{at}.rrule: {exc}")
        else:
            if freq not in ALLOWED_FREQ:
                problems.append(f"{at}.rrule: FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY")
            elif needs_from(str(item["rrule"])) and as_date(item.get("from")) is None:
                problems.append(f"{at}: this rrule depends on its start date; add `from: YYYY-MM-DD`")
        for j, value in enumerate(item.get("exdates") or []):
            if as_date(value) is None:
                problems.append(f"{at}.exdates[{j}]: {value!r} is not a date")
    return problems


# --- building the agenda -------------------------------------------------------


def build(
    notes: Iterable[Note],
    start: dt.date,
    end: dt.date,
    today: dt.date,
    who: str | None = None,
    extra: Iterable[Item] = (),
) -> dict[str, Any]:
    """Everything dated between start and end (inclusive), grouped by day. `extra` adds items
    from outside the vault, such as chores from home-board and calendar events."""
    outside = [i for i in extra if start <= i.date <= end]
    items: list[Item] = []
    upcoming: list[Item] = []
    problems: list[str] = []
    breaks: list[tuple[str, dt.date, dt.date]] = []  # (folder, first, last)
    cancels: list[tuple[dt.date, str, list[str]]] = []  # (day, text, who)
    routine: list[Item] = []

    for note in notes:
        meta = note.meta
        found = check(meta)
        problems.extend(f"{note.path}: {p}" for p in found)
        bad = {p.split(":")[0] for p in found}
        default_who = people(meta.get("who"))
        folder = str(PurePosixPath(note.path).parent)

        if "birthday" not in bad and (born := as_date(meta.get("birthday"))):
            items.extend(_birthdays(note, born, start, end))
        for i, raw in enumerate(_items(meta, "events")):
            if _is_bad(bad, "events", i):
                continue
            first, last = as_date(raw["date"]), as_date(raw.get("end_date")) or as_date(raw["date"])
            if raw.get("break"):
                breaks.append((folder, first, last))
            for text in as_list(raw.get("cancels")):
                for day in _days(max(first, start), min(last, end)):
                    cancels.append((day, text.lower(), people(raw.get("who")) or default_who))
            if last < start or first > end:
                continue
            item = _item(raw, note, max(first, start), "break" if raw.get("break") else "event", default_who)
            if last > first:
                item.end_date = last
            items.append(item)
        for i, raw in enumerate(_items(meta, "recurring")):
            if _is_bad(bad, "recurring", i):
                continue
            for day in _occurrences(raw, start, end):
                routine.append(_item(raw, note, day, "recurring", default_who))
        for i, raw in enumerate(_items(meta, "due")):
            if raw.get("done") or _is_bad(bad, "due", i):
                continue
            item = _item(raw, note, as_date(raw["date"]), "due", default_who)
            item.extra["days_left"] = (item.date - today).days
            warn = raw.get("warn_days", DEFAULT_WARN_DAYS)
            if start <= item.date <= end:
                items.append(item)
            elif item.date > end and item.date - dt.timedelta(days=warn) <= end:
                upcoming.append(item)
            elif start - dt.timedelta(days=OVERDUE_DAYS) <= item.date < start:
                item.extra["overdue"] = True
                upcoming.append(item)
        for i, raw in enumerate(_items(meta, "reminders")):
            if raw.get("done") or _is_bad(bad, "reminders", i):
                continue
            day, clock = as_moment(raw["at"])
            if start <= day <= end:
                item = _item({**raw, "title": raw["text"], "time": clock}, note, day, "reminder", default_who)
                items.append(item)

    for item in routine:
        folder = str(PurePosixPath(item.source).parent)
        if any(f == folder and first <= item.date <= last for f, first, last in breaks):
            continue
        if any(
            day == item.date and _mentions(item.title, text) and _same_people(by, item.who)
            for day, text, by in cancels
        ):
            continue
        # A week note often repeats a routine item as an event ("PE on Tuesday"): keep the event.
        twin = next(
            (e for e in items if e.kind == "event" and e.date == item.date
             and _same_title(e.title, item.title) and _same_people(e.who, item.who)),
            None,
        )
        if twin:
            twin.bring += [b for b in item.bring if b not in twin.bring]
            twin.time = twin.time or item.time
            twin.where = twin.where or item.where
            continue
        items.append(item)

    for item in outside:
        # The same appointment in a calendar and in a note: one item, with the note's details first.
        twin = None if item.kind == "chore" else next(
            (v for v in items if v.source.endswith(".md") and v.date == item.date and _same_title(v.title, item.title)
             and not (v.time and item.time and v.time != item.time)),
            None,
        )
        if twin:
            twin.time = twin.time or item.time
            twin.end_time = twin.end_time or item.end_time
            twin.where = twin.where or item.where
            continue
        items.append(item)

    if who:
        wanted = who.lstrip("@").lower()
        items = [i for i in items if wanted in i.who]
        upcoming = [i for i in upcoming if wanted in i.who]

    return _render(items, upcoming, problems, start, end, today)


def _is_bad(bad: set[str], key: str, index: int) -> bool:
    """Whether a problem was reported for item `index` of `key` (or for the whole list)."""
    me = f"{key}[{index}]"
    return key in bad or any(b == me or b.startswith(f"{me}.") for b in bad)


def _items(meta: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = meta.get(key)
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _item(raw: dict[str, Any], note: Note, day: dt.date, kind: str, default_who: list[str]) -> Item:
    return Item(
        date=day,
        kind=kind,
        title=str(raw.get("title", "")).strip(),
        source=note.path,
        time=as_time(raw.get("time")),
        end_time=as_time(raw.get("end_time")),
        who=people(raw.get("who")) or default_who,
        where=str(raw["where"]).strip() if raw.get("where") else None,
        bring=as_list(raw.get("bring")),
        notes=str(raw["notes"]).strip() if raw.get("notes") else None,
    )


def _birthdays(note: Note, born: dt.date, start: dt.date, end: dt.date) -> list[Item]:
    name = str(note.meta.get("name") or note.title)
    found = []
    for year in range(start.year, end.year + 1):
        try:
            day = born.replace(year=year)
        except ValueError:  # 29 February in a non-leap year
            day = dt.date(year, 2, 28)
        if start <= day <= end:
            age = year - born.year
            title = f"{name}'s birthday" + (f" (turns {age})" if age > 0 else "")
            found.append(Item(date=day, kind="birthday", title=title, source=note.path,
                              who=[PurePosixPath(note.path).stem.lower()], extra={"age": age, "name": name}))
    return found


def _occurrences(raw: dict[str, Any], start: dt.date, end: dt.date) -> list[dt.date]:
    first, last = as_date(raw.get("from")), as_date(raw.get("until"))
    low, high = max(start, first) if first else start, min(end, last) if last else end
    if low > high:
        return []
    # Rules that don't depend on their start date (validated) get a nearby anchor, so
    # expansion doesn't iterate from some date long ago.
    anchor = first or start - dt.timedelta(days=start.weekday() + 7)
    rule = parse_rrule(str(raw["rrule"]), anchor)
    days = [d.date() for d in rule.between(dt.datetime.combine(low, dt.time.min), dt.datetime.combine(high, dt.time.max), inc=True)]
    skip = {as_date(d) for d in raw.get("exdates") or []}
    return [d for d in days if d not in skip]


def _days(first: dt.date, last: dt.date) -> Iterable[dt.date]:
    day = first
    while day <= last:
        yield day
        day += dt.timedelta(days=1)


def _mentions(title: str, text: str) -> bool:
    """Whole-word match, so cancelling "PE" doesn't also cancel "Scouts (speider)"."""
    return re.search(rf"(?<!\w){re.escape(text)}(?!\w)", title, re.IGNORECASE) is not None


def _same_title(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"\W+", " ", s.casefold()).strip()

    return norm(a) == norm(b)


def _same_people(a: list[str], b: list[str]) -> bool:
    return not a or not b or bool(set(a) & set(b))


def _render(items: list[Item], upcoming: list[Item], problems: list[str], start: dt.date, end: dt.date, today: dt.date) -> dict[str, Any]:
    by_day: dict[dt.date, list[Item]] = {}
    for item in items:
        by_day.setdefault(item.date, []).append(item)
    days = []
    for day in sorted(by_day):
        day_items = sorted(by_day[day], key=lambda i: (i.time or "", _KIND_ORDER.get(i.kind, 9), i.title))
        packing: dict[str, list[str]] = {}
        for item in day_items:
            for person in item.who or ["everyone"]:
                for thing in item.bring:
                    if thing not in packing.setdefault(person, []):
                        packing[person].append(thing)
        entry: dict[str, Any] = {"date": day.isoformat(), "weekday": day.strftime("%A"), "items": [i.to_dict() for i in day_items]}
        if packing:
            entry["bring"] = packing
        days.append(entry)
    sources = sorted({i.source for i in [*items, *upcoming] if i.source.endswith(".md")})
    result: dict[str, Any] = {
        "today": today.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "upcoming": [i.to_dict() for i in sorted(upcoming, key=lambda i: i.date)],
        "sources": sources,
    }
    if problems:
        result["problems"] = problems[:20]
    return result

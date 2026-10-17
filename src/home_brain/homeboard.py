"""Chores from home-board, the family's chore and points app.

home-board answers GET /api/integrations/chores with an API key (its Integrations:ApiKey setting)
instead of a user login, so home-brain can only read chores, not change them.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .agenda import Item
from .config import Settings

log = logging.getLogger(__name__)

MAX_DAYS = 62
"""home-board's limit per request."""
DONE = ("done", "verified")
_FRACTION = re.compile(r"(\.\d{6})\d+")


class HomeBoardError(RuntimeError):
    """home-board could not be reached or refused the request."""


@dataclass(frozen=True)
class Chore:
    date: dt.date
    title: str
    status: str
    """todo, done (waiting for a parent to check it), verified or rejected"""
    schedule: str
    """daily, weekly, once, during_week or during_month"""
    points: int = 0
    due: str | None = None
    """Due time, "HH:MM"."""
    assigned_to: str | None = None
    """Display name; None for a chore shared by a group."""
    group: str | None = None
    """"kids" or "parents" for a shared chore."""
    completed_by: str | None = None
    completed_at: dt.datetime | None = None
    """Local time."""

    @property
    def done(self) -> bool:
        return self.status in DONE

    @property
    def who(self) -> list[str]:
        return [slug(self.assigned_to)] if self.assigned_to else []

    def to_dict(self, now: dt.datetime | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"title": self.title, "status": self.status}
        if self.due:
            out["due"] = self.due
        if self.group:
            out["shared_by"] = self.group
        if self.completed_by and (self.group or self.completed_by != self.assigned_to):
            out["completed_by"] = self.completed_by
        if self.completed_at:
            out["completed_at"] = f"{self.completed_at:%H:%M}"
        if now and not self.done and self.status != "rejected":
            if self.date < now.date() or (self.date == now.date() and self.due and self.due < f"{now:%H:%M}"):
                out["late"] = True
        if self.schedule in ("during_week", "during_month"):
            out["any_day"] = "this week" if self.schedule == "during_week" else "this month"
        out["points"] = self.points
        return out


def slug(name: str) -> str:
    """A home-board display name as a vault person slug: "Ola Nordmann" → ola-nordmann."""
    return re.sub(r"\s+", "-", name.strip().casefold())


class HomeBoard:
    def __init__(self, url: str, api_key: str, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> HomeBoard | None:
        if not settings.homeboard_url or not settings.homeboard_api_key:
            return None
        return cls(settings.homeboard_url, settings.homeboard_api_key)

    def chores(self, start: dt.date, end: dt.date) -> list[Chore]:
        found: list[Chore] = []
        first = start
        while first <= end:
            last = min(end, first + dt.timedelta(days=MAX_DAYS - 1))
            found += [_chore(raw) for raw in self._get(first, last)["chores"]]
            first = last + dt.timedelta(days=1)
        return found

    def _get(self, start: dt.date, end: dt.date) -> dict[str, Any]:
        query = urllib.parse.urlencode({"startDate": start.isoformat(), "endDate": end.isoformat()})
        request = urllib.request.Request(f"{self.url}/api/integrations/chores?{query}", headers={"X-Api-Key": self._api_key})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            hint = {401: ": wrong HOMEBOARD_API_KEY", 404: ": home-board has no Integrations__ApiKey set, or is too old"}
            raise HomeBoardError(f"home-board refused the request (HTTP {exc.code}{hint.get(exc.code, '')})") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HomeBoardError(f"home-board is unreachable at {self.url}: {exc}") from exc


def chore_report(chores: list[Chore], now: dt.datetime, who: str | None = None) -> dict[str, Any]:
    """Per day and person: the chores done and still to do, plus the chores shared by the kids."""
    wanted = slug(who.lstrip("@")) if who else None
    names = sorted({c.assigned_to for c in chores if c.assigned_to})
    if wanted and not any(_matches(n, wanted) for n in names):
        return {"now": f"{now:%Y-%m-%dT%H:%M}", "days": [],
                "hint": f"No chores for {who!r} in this period. People with chores: {', '.join(names) or 'none'}."}
    days = []
    for day in sorted({c.date for c in chores}):
        on_day = [c for c in chores if c.date == day]
        people = []
        for name in sorted({c.assigned_to for c in on_day if c.assigned_to}):
            if wanted and not _matches(name, wanted):
                continue
            mine = [c for c in on_day if c.assigned_to == name]
            done = [c for c in mine if c.done]
            people.append({
                "name": name,
                "who": slug(name),
                "summary": f"{len(done)} of {len(mine)} done",
                "done": [c.to_dict(now) for c in done],
                "todo": [c.to_dict(now) for c in mine if not c.done],
            })
        shared = [c.to_dict(now) for c in on_day if c.group and (c.group == "kids" or not wanted)]
        days.append({"date": day.isoformat(), "weekday": f"{day:%A}", "people": people, "shared": shared})
    return {"now": f"{now:%Y-%m-%dT%H:%M}", "days": days}


def agenda_items(chores: list[Chore], today: dt.date) -> list[Item]:
    """Chores as agenda items. Chores that may be done any day of the week or month are only
    listed for today, so they don't repeat on every day of an agenda."""
    items = []
    for chore in chores:
        if chore.schedule in ("during_week", "during_month") and chore.date != today:
            continue
        extra: dict[str, Any] = {"status": chore.status}
        if chore.group:
            extra["shared_by"] = chore.group
        if chore.completed_by and chore.group:
            extra["completed_by"] = chore.completed_by
        items.append(Item(date=chore.date, kind="chore", title=chore.title, source="home-board",
                          time=chore.due, who=chore.who, extra=extra))
    return items


def _matches(name: str, wanted: str) -> bool:
    """`ola` matches "Ola" and "Ola Nordmann"."""
    return slug(name) == wanted or slug(name).split("-")[0] == wanted


def _chore(raw: dict[str, Any]) -> Chore:
    due = raw.get("dueTime")
    return Chore(
        date=dt.date.fromisoformat(raw["date"]),
        title=str(raw.get("title") or "").strip(),
        status=str(raw.get("status") or "todo"),
        schedule=str(raw.get("schedule") or "daily"),
        points=int(raw.get("points") or 0),
        due=due[:5] if due else None,
        assigned_to=raw.get("assignedTo") or None,
        group=raw.get("assignedToGroup") or None,
        completed_by=raw.get("completedBy") or None,
        completed_at=_local(raw.get("completedAt")),
    )


def _local(value: str | None) -> dt.datetime | None:
    """home-board's UTC timestamps (up to 7 decimals, with or without Z) as local time."""
    if not value:
        return None
    try:
        moment = dt.datetime.fromisoformat(_FRACTION.sub(r"\1", value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return moment.astimezone().replace(tzinfo=None)

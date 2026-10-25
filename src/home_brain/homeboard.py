"""Chores and points from home-board, the family's chore and points app.

home-board's integration endpoints take an API key instead of a user login. The read key
(Integrations:ApiKey) only reads chores and points. The optional write key
(Integrations:WriteApiKey) also approves or rejects done chores and gives bonus points.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from .agenda import Item
from .config import Settings
from .texts import EN, Texts

log = logging.getLogger(__name__)

MAX_DAYS = 62
"""home-board's limit per request."""
DONE = ("done", "verified")
_FRACTION = re.compile(r"(\.\d{6})\d+")


class HomeBoardError(RuntimeError):
    """home-board could not be reached or refused the request."""


class HomeBoardRefused(HomeBoardError):
    """home-board said why it refused a change (unknown chore, already approved, ...)."""


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
    completion_id: str | None = None
    """What approving or rejecting the chore takes, once it is done."""

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
        if self.status == "done" and self.completion_id:
            out["id"] = self.completion_id
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
    def __init__(self, url: str, api_key: str, write_key: str | None = None, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self._api_key = api_key
        self._write_key = write_key
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> HomeBoard | None:
        if not settings.homeboard_url or not settings.homeboard_api_key:
            return None
        return cls(settings.homeboard_url, settings.homeboard_api_key, settings.homeboard_write_key)

    @property
    def can_write(self) -> bool:
        return bool(self._write_key)

    def chores(self, start: dt.date, end: dt.date) -> list[Chore]:
        found: list[Chore] = []
        first = start
        while first <= end:
            last = min(end, first + dt.timedelta(days=MAX_DAYS - 1))
            query = {"startDate": first.isoformat(), "endDate": last.isoformat()}
            found += [_chore(raw) for raw in self._get("chores", query)["chores"]]
            first = last + dt.timedelta(days=1)
        return found

    def points(self) -> dict[str, Any]:
        """Each child's points and the done chores waiting for approval, as home-board reports them."""
        return self._get("points")

    def approve(self, completion_id: str, points: int | None = None) -> dict[str, Any]:
        """Approves a done chore: its points (or `points`) go to the child. Returns the chore's
        `title`, `completedBy` and `pointsAwarded`."""
        body = {"pointsAwarded": points} if points is not None else {}
        return self._post(f"completions/{_completion(completion_id)}/approve", body)

    def reject(self, completion_id: str, reason: str | None = None) -> dict[str, Any]:
        """Rejects a done chore that wasn't really done: no points."""
        return self._post(f"completions/{_completion(completion_id)}/reject", {"reason": reason} if reason else {})

    def award(self, user_id: str, points: int, description: str) -> dict[str, Any]:
        """Gives a child bonus points; `user_id` is the child's `userId` from points()."""
        return self._post(f"users/{user_id}/bonus-points", {"points": points, "description": description})

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._write_key:
            raise HomeBoardError("HOMEBOARD_WRITE_KEY is not set, so home-brain can't change anything in home-board")
        request = urllib.request.Request(
            f"{self.url}/api/integrations/{endpoint}",
            data=json.dumps(body).encode(),
            headers={"X-Api-Key": self._write_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if reason := _reason(exc):
                raise HomeBoardRefused(reason) from exc
            hint = {401: ": wrong HOMEBOARD_WRITE_KEY", 404: ": home-board has no Integrations__WriteApiKey set, or is too old"}
            raise HomeBoardError(f"home-board refused the change (HTTP {exc.code}{hint.get(exc.code, '')})") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HomeBoardError(f"home-board is unreachable at {self.url}: {exc}") from exc

    def _get(self, endpoint: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self.url}/api/integrations/{endpoint}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        request = urllib.request.Request(url, headers={"X-Api-Key": self._api_key})
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


def points_report(raw: dict[str, Any], who: str | None = None) -> dict[str, Any]:
    """Per child: scoreboard points, points earned this week and month, the next payout and the
    done chores waiting for a parent's approval."""
    wanted = slug(who.lstrip("@")) if who else None
    children = []
    for user in raw.get("users", []):
        name = str(user.get("name") or "")
        if wanted and not _matches(name, wanted):
            continue
        last_payout = _local(user.get("lastPayoutAt"))
        children.append({
            "name": name,
            "who": slug(name),
            "user_id": user.get("userId"),
            "scoreboard_points": user.get("scoreboardPoints", 0),
            "earned_this_week": user.get("earnedThisWeek", 0),
            "earned_this_month": user.get("earnedThisMonth", 0),
            "since_last_payout": user.get("pointsSinceLastPayout", 0),
            "money_to_pay": round(float(user.get("moneyToPay") or 0), 2),
            "last_payout": f"{last_payout:%Y-%m-%d}" if last_payout else None,
            "waiting_for_approval": [
                {"id": w.get("completionId"), "title": w.get("title"), "points": w.get("points", 0), "date": w.get("date"),
                 "completed_at": f"{c:%H:%M}" if (c := _local(w.get("completedAt"))) else None}
                for w in user.get("waitingForApproval", [])
            ],
        })
    report: dict[str, Any] = {
        "today": raw.get("today"),
        "point_to_money_rate": raw.get("pointToMoneyRate"),
        "children": children,
        "total_money_to_pay": round(sum(c["money_to_pay"] for c in children), 2),
    }
    if wanted and not children:
        known = ", ".join(str(u.get("name")) for u in raw.get("users", [])) or "none"
        report["hint"] = f"No child called {who!r} in Home-Board. Children: {known}."
    return report


def waiting_summary(raw: dict[str, Any], t: Texts = EN) -> list[str]:
    """One line per child with chores waiting for approval, e.g. `Ola: 2 chores (20 points)`."""
    lines = []
    for user in raw.get("users", []):
        waiting = user.get("waitingForApproval") or []
        if waiting:
            points = sum(int(w.get("points") or 0) for w in waiting)
            template = t.waiting_one if len(waiting) == 1 else t.waiting_many
            lines.append(template.format(name=user.get("name"), count=len(waiting), points=points))
    return lines


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


def find_child(raw: dict[str, Any], who: str) -> dict[str, Any]:
    """The child in points() called `who` ("ola", "Ola" or "Ola Nordmann"); an exact match wins."""
    wanted = slug(who.lstrip("@"))
    users = raw.get("users", [])
    found = [u for u in users if slug(str(u.get("name") or "")) == wanted] or [
        u for u in users if _matches(str(u.get("name") or ""), wanted)]
    if len(found) != 1:
        known = ", ".join(str(u.get("name")) for u in users) or "none"
        problem = "Several children match" if found else "No child matches"
        raise HomeBoardRefused(f"{problem} {who!r} in Home-Board. Children: {known}.")
    return found[0]


def _completion(value: str) -> str:
    """A chore's `id` as home-board expects it; anything else is refused before asking."""
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise HomeBoardRefused(f"{value!r} is not a chore id; take the `id` from scoreboard or chores") from None


def _reason(error: urllib.error.HTTPError) -> str | None:
    """home-board's own explanation of a refused request, if it gave one."""
    try:
        message = json.loads(error.read() or b"{}").get("message")
    except (ValueError, AttributeError, OSError):
        return None
    return str(message) if message else None


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
        completion_id=raw.get("completionId") or None,
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

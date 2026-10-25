from __future__ import annotations

import datetime as dt
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import CHORES, FEED_CAT_ID, MON, NORA_ID, OLA_ID, POINTS, TUE

from home_brain.texts import NB

from home_brain.homeboard import (
    HomeBoard,
    HomeBoardError,
    HomeBoardRefused,
    agenda_items,
    chore_report,
    find_child,
    points_report,
    slug,
    waiting_summary,
)

KEY = "test-key"
WRITE_KEY = "test-write-key"


class FakeHomeBoardServer(ThreadingHTTPServer):
    requests: list[dict[str, str]]


class Handler(BaseHTTPRequestHandler):
    server: FakeHomeBoardServer

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.server.requests.append(query)
        if url.path not in ("/api/integrations/chores", "/api/integrations/points"):
            self._send(404, {})
        elif self.headers.get("X-Api-Key") != KEY:
            self._send(401, {})
        elif url.path.endswith("/points"):
            self._send(200, POINTS)
        else:
            self._send(200, {"timezone": "Europe/Oslo", "today": query["startDate"], "chores": [{
                "date": query["startDate"], "title": "Make bed", "points": 5, "schedule": "daily",
                "dueTime": "08:00:00", "assignedTo": "Ola Nordmann", "assignedToGroup": None,
                "status": "verified", "completedBy": "Ola Nordmann", "completedAt": "2026-10-19T05:40:12.1234567Z",
            }]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        self.server.requests.append({"path": self.path, **body})
        if self.headers.get("X-Api-Key") != WRITE_KEY:
            self._send(401, {})
        elif self.path == f"/api/integrations/completions/{FEED_CAT_ID}/approve":
            self._send(200, {"message": "Chore approved", "title": "Feed the cat", "completedBy": "Nora",
                             "pointsAwarded": body.get("pointsAwarded", 10)})
        elif self.path.startswith("/api/integrations/completions/"):
            self._send(409, {"message": "'Make bed' by Ola was already approved or rejected"})
        elif self.path == f"/api/integrations/users/{OLA_ID}/bonus-points":
            self._send(200, {"message": "Bonus points awarded", "name": "Ola", "pointsAwarded": body["points"]})
        else:
            self._send(404, {})

    def _send(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[FakeHomeBoardServer]:
    server = FakeHomeBoardServer(("127.0.0.1", 0), Handler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def url(server: FakeHomeBoardServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/"


def test_reads_chores(server: FakeHomeBoardServer) -> None:
    [chore] = HomeBoard(url(server), KEY).chores(MON, MON)
    assert (chore.date, chore.title, chore.status, chore.due) == (MON, "Make bed", "verified", "08:00")
    assert chore.who == ["ola-nordmann"]
    assert chore.done
    expected = dt.datetime(2026, 10, 19, 5, 40, 12, 123456, tzinfo=dt.UTC).astimezone().replace(tzinfo=None)
    assert chore.completed_at == expected
    assert server.requests == [{"startDate": "2026-10-19", "endDate": "2026-10-19"}]


def test_long_ranges_take_several_requests(server: FakeHomeBoardServer) -> None:
    HomeBoard(url(server), KEY).chores(MON, MON + dt.timedelta(days=69))
    assert server.requests == [
        {"startDate": "2026-10-19", "endDate": "2026-12-19"},
        {"startDate": "2026-12-20", "endDate": "2026-12-27"},
    ]


def test_wrong_key(server: FakeHomeBoardServer) -> None:
    with pytest.raises(HomeBoardError, match=r"\(HTTP 401: wrong HOMEBOARD_API_KEY\)"):
        HomeBoard(url(server), "nope").chores(MON, MON)


def test_unreachable(server: FakeHomeBoardServer) -> None:
    address = url(server)
    server.shutdown()
    server.server_close()
    with pytest.raises(HomeBoardError, match="unreachable"):
        HomeBoard(address, KEY, timeout=3).chores(MON, MON)


NOON = dt.datetime(2026, 10, 19, 12, 0)


def test_report_per_person() -> None:
    report = chore_report(CHORES, NOON)
    monday = report["days"][0]
    assert (monday["date"], monday["weekday"]) == ("2026-10-19", "Monday")
    ola, nora = sorted(monday["people"], key=lambda p: p["name"] != "Ola")
    assert (ola["name"], ola["who"], ola["summary"]) == ("Ola", "ola", "1 of 2 done")
    assert ola["done"] == [{"title": "Make bed", "status": "verified", "due": "08:00", "completed_at": "07:40", "points": 5}]
    assert ola["todo"] == [{"title": "Vacuum room", "status": "todo", "any_day": "this week", "points": 20}]
    assert nora["todo"] == [{"title": "Feed the cat", "status": "todo", "due": "09:00", "late": True, "points": 10}]
    assert monday["shared"] == [{"title": "Empty dishwasher", "status": "done", "shared_by": "kids",
                                 "completed_by": "Nora", "completed_at": "11:05",
                                 "id": "0b5e3f7a-2c41-4d8e-9a6b-1f2e3d4c5b6a", "points": 10}]
    assert report["now"] == "2026-10-19T12:00"


def test_report_for_one_person() -> None:
    report = chore_report([c for c in CHORES if c.date == MON], NOON, who="@Nora")
    [day] = report["days"]
    assert [p["name"] for p in day["people"]] == ["Nora"]
    assert [c["title"] for c in day["shared"]] == ["Empty dishwasher"]


def test_report_for_someone_without_chores() -> None:
    report = chore_report(CHORES, NOON, who="kari")
    assert report["days"] == []
    assert report["hint"] == "No chores for 'kari' in this period. People with chores: Nora, Ola."


def test_agenda_items_list_any_day_chores_only_today() -> None:
    items = agenda_items(CHORES, today=MON)
    assert [(i.date, i.title) for i in items if i.title == "Vacuum room"] == [(MON, "Vacuum room")]
    dishwasher = next(i for i in items if i.title == "Empty dishwasher" and i.date == TUE)
    assert (dishwasher.kind, dishwasher.who, dishwasher.extra) == ("chore", [], {"status": "todo", "shared_by": "kids"})
    assert next(i for i in items if i.title == "Make bed" and i.date == TUE).time == "08:00"


def test_reads_points(server: FakeHomeBoardServer) -> None:
    assert HomeBoard(url(server), KEY).points() == POINTS
    assert server.requests == [{}]


def test_points_report() -> None:
    report = points_report(POINTS)
    assert (report["today"], report["point_to_money_rate"], report["total_money_to_pay"]) == ("2026-10-19", 0.5, 20.0)
    nora, ola = report["children"]
    assert (ola["name"], ola["who"], ola["scoreboard_points"], ola["earned_this_week"], ola["money_to_pay"]) == ("Ola", "ola", 30, 20, 15.0)
    assert ola["last_payout"] == f"{dt.datetime(2026, 10, 1, 18, tzinfo=dt.UTC).astimezone():%Y-%m-%d}"
    completed = f"{dt.datetime(2026, 10, 19, 6, tzinfo=dt.UTC).astimezone():%H:%M}"
    assert nora["waiting_for_approval"] == [
        {"id": FEED_CAT_ID, "title": "Feed the cat", "points": 10, "date": "2026-10-19", "completed_at": completed}]
    assert nora["user_id"] == NORA_ID
    assert nora["last_payout"] is None


def test_points_report_for_one_child() -> None:
    assert [c["name"] for c in points_report(POINTS, who="ola")["children"]] == ["Ola"]
    unknown = points_report(POINTS, who="kari")
    assert unknown["children"] == []
    assert unknown["hint"] == "No child called 'kari' in Home-Board. Children: Nora, Ola."


def test_waiting_summary() -> None:
    assert waiting_summary(POINTS) == ["Nora: 1 chore (10 points)"]
    assert waiting_summary({"users": [{"name": "Ola", "waitingForApproval": [{"points": 5}, {"points": 10}]}]}) == ["Ola: 2 chores (15 points)"]
    assert waiting_summary(POINTS, NB) == ["Nora: 1 oppgave (10 poeng)"]
    assert waiting_summary({"users": [{"name": "Ola", "waitingForApproval": [{"points": 5}, {"points": 10}]}]}, NB) == ["Ola: 2 oppgaver (15 poeng)"]


def test_slug() -> None:
    assert slug(" Ola  Nordmann ") == "ola-nordmann"
    assert slug("Åse") == "åse"


def test_approve_reject_and_award(server: FakeHomeBoardServer) -> None:
    homeboard = HomeBoard(url(server), KEY, WRITE_KEY)
    assert homeboard.can_write
    assert homeboard.approve(FEED_CAT_ID)["pointsAwarded"] == 10
    assert homeboard.approve(f" {FEED_CAT_ID.upper()} ", points=4)["pointsAwarded"] == 4
    assert homeboard.award(OLA_ID, 20, "Helped with dinner")["name"] == "Ola"
    assert server.requests == [
        {"path": f"/api/integrations/completions/{FEED_CAT_ID}/approve"},
        {"path": f"/api/integrations/completions/{FEED_CAT_ID}/approve", "pointsAwarded": 4},
        {"path": f"/api/integrations/users/{OLA_ID}/bonus-points", "points": 20, "description": "Helped with dinner"},
    ]


def test_refusals_say_why(server: FakeHomeBoardServer) -> None:
    homeboard = HomeBoard(url(server), KEY, WRITE_KEY)
    with pytest.raises(HomeBoardRefused, match="'Make bed' by Ola was already approved or rejected"):
        homeboard.reject("1b2c3d4e-0000-4000-8000-000000000000", "Not done")
    # Not an id at all: refused without asking home-board.
    with pytest.raises(HomeBoardRefused, match="'Feed the cat' is not a chore id"):
        homeboard.approve("Feed the cat")
    assert len(server.requests) == 1


def test_writing_needs_the_write_key(server: FakeHomeBoardServer) -> None:
    assert not HomeBoard(url(server), KEY).can_write
    with pytest.raises(HomeBoardError, match="HOMEBOARD_WRITE_KEY is not set"):
        HomeBoard(url(server), KEY).approve(FEED_CAT_ID)
    with pytest.raises(HomeBoardError, match=r"\(HTTP 401: wrong HOMEBOARD_WRITE_KEY\)"):
        HomeBoard(url(server), KEY, KEY).approve(FEED_CAT_ID)


def test_find_child() -> None:
    assert find_child(POINTS, "@Nora")["userId"] == NORA_ID
    raw = {"users": [{"userId": "1", "name": "Ola"}, {"userId": "2", "name": "Ola Nordmann"}, {"userId": "3", "name": "Nora"}]}
    assert find_child(raw, "ola")["userId"] == "1"  # the exact name wins
    assert find_child(raw, "ola-nordmann")["userId"] == "2"
    with pytest.raises(HomeBoardRefused, match="No child matches 'kari' in Home-Board. Children: Ola, Ola Nordmann, Nora."):
        find_child(raw, "kari")


def test_done_chores_have_an_id() -> None:
    [dishwasher] = [c for c in chore_report(CHORES, NOON)["days"][0]["shared"]]
    assert dishwasher["id"] == "0b5e3f7a-2c41-4d8e-9a6b-1f2e3d4c5b6a"
    verified = chore_report(CHORES, NOON, who="ola")["days"][0]["people"][0]["done"][0]
    assert "id" not in verified  # already approved

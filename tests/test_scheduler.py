from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import FakeNotifier

from home_brain.index import Index
from home_brain.scheduler import Scheduler
from home_brain.vault import Vault

REMINDERS = """\
---
type: reminders
reminders:
  - id: r-1
    at: "2026-10-20T18:00"
    text: Pack gym clothes
    who: ola
  - at: 2026-10-21
    text: "Forest trip: rain gear"
  - id: r-3
    at: "2026-10-20T18:00"
    text: Already handled
    done: true
---
"""
DENTIST = """\
---
type: event
who: me
events:
  - {date: 2026-10-21, time: "09:00", title: Dentist}
---
"""


@pytest.fixture
def scheduler_for(vault: Vault, index: Index, tmp_path: Path) -> Iterator[Callable[..., Scheduler]]:
    vault.write("reminders.md", REMINDERS)
    vault.write("events/2026-10-21-dentist.md", DENTIST)
    made: list[Scheduler] = []

    def make(notifier: FakeNotifier, **kwargs: object) -> Scheduler:
        scheduler = Scheduler(vault, index, notifier, tmp_path / "data" / "scheduler.db", **kwargs)
        made.append(scheduler)
        return scheduler

    yield make
    for scheduler in made:
        scheduler.close()


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute)


def test_reminder_is_sent_once_when_due(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier) -> None:
    scheduler = scheduler_for(notifier)
    assert scheduler.tick(at(20, 17, 59)) == []
    assert scheduler.tick(at(20, 18, 0)) == ["reminder:r-1"]
    assert scheduler.tick(at(20, 18, 1)) == []
    assert [(m.title, m.text) for m in notifier.sent] == [("Reminder for Ola", "Pack gym clothes")]
    # A restart remembers what was sent.
    assert scheduler_for(notifier).tick(at(20, 18, 5)) == []


def test_reminder_without_time_is_sent_in_the_morning(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier) -> None:
    scheduler = scheduler_for(notifier)
    assert scheduler.tick(at(21, 7, 59)) == []
    [key] = scheduler.tick(at(21, 8, 0))
    assert key.startswith("reminder:reminders.md:2026-10-21T08:00:")
    assert notifier.sent[0].text == "Forest trip: rain gear"


def test_missed_reminders_are_sent_late_but_not_too_late(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier, tmp_path: Path) -> None:
    # Down from 17:00 to 23:30: the reminder and the 19:00 briefing are sent when it comes back.
    first = scheduler_for(notifier)
    assert first.tick(at(20, 23, 30)) == ["reminder:r-1", "tomorrow:2026-10-20"]
    assert notifier.sent[0].text == "Pack gym clothes\n(due 18:00)"
    # Down until after midnight: both are dropped (and the briefing would be about the wrong day).
    first.close()
    (tmp_path / "data" / "scheduler.db").unlink()
    assert scheduler_for(FakeNotifier()).tick(at(21, 0, 30)) == []


def test_evening_briefing(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier) -> None:
    scheduler = scheduler_for(notifier)
    scheduler.tick(at(20, 18, 59))
    notifier.sent.clear()
    assert scheduler.tick(at(20, 19, 0)) == ["tomorrow:2026-10-20"]
    assert scheduler.tick(at(20, 19, 1)) == []
    [message] = notifier.sent
    assert message.title == "Tomorrow: Wednesday 21 October"
    assert message.text == "Reminder: Forest trip: rain gear\n09:00 Dentist (you)"


def test_briefing_time_and_weekly_jobs(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier) -> None:
    scheduler = scheduler_for(notifier, briefing_time=dt.time(20, 30))
    assert scheduler.tick(at(24, 19, 0)) == []  # Saturday: no week-ahead, briefing not yet
    assert scheduler.tick(at(25, 18, 0)) == ["week-ahead:2026-10-25"]
    assert scheduler.tick(at(25, 20, 30)) == ["tomorrow:2026-10-25"]


def test_example_notes_are_left_out(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier) -> None:
    scheduler = scheduler_for(notifier)
    # The starter vault's event note has a reminder at this time, and its car and house notes
    # would fill the Monday check; both are examples.
    assert scheduler.tick(at(22, 18, 0)) == []
    assert scheduler.tick(at(19, 8, 0)) == []
    assert notifier.sent == []


def test_failed_sends_are_retried(scheduler_for: Callable[..., Scheduler]) -> None:
    flaky = FakeNotifier(failures=1)
    scheduler = scheduler_for(flaky)
    assert scheduler.tick(at(20, 18, 0)) == []
    assert scheduler.tick(at(20, 18, 1)) == ["reminder:r-1"]
    assert len(flaky.sent) == 1


def test_background_thread_catches_up_at_start(scheduler_for: Callable[..., Scheduler], notifier: FakeNotifier) -> None:
    scheduler = scheduler_for(notifier, clock=lambda: at(20, 18, 5))
    scheduler.start()
    deadline = time.monotonic() + 5
    while not notifier.sent and time.monotonic() < deadline:
        time.sleep(0.05)
    scheduler.close()
    assert [m.text for m in notifier.sent] == ["Pack gym clothes"]

"""Push notifications through a self-hosted ntfy server (https://ntfy.sh)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal, Protocol

from .config import Settings

log = logging.getLogger(__name__)

Priority = Literal["min", "low", "default", "high", "urgent"]
_PRIORITIES: dict[str, int] = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}

MAX_BYTES = 4000
"""ntfy turns longer messages into attachments, which this server doesn't enable; cut them instead."""


class NotifyError(RuntimeError):
    """The ntfy server could not be reached or refused the message."""


@dataclass(frozen=True)
class Message:
    text: str
    title: str | None = None
    priority: Priority = "default"
    tags: tuple[str, ...] = ()
    """ntfy tags; names of emojis (e.g. "bell") are shown as icons."""


class Notifier(Protocol):
    def send(self, message: Message) -> None: ...


class Ntfy:
    def __init__(self, url: str, topic: str, token: str | None = None, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.topic = topic
        self._token = token
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> Ntfy | None:
        if not settings.ntfy_url:
            return None
        if not settings.ntfy_token:
            log.warning("NTFY_TOKEN is not set: ntfy will refuse the messages unless the topic is open to anyone")
        return cls(settings.ntfy_url, settings.ntfy_topic, settings.ntfy_token)

    def send(self, message: Message) -> None:
        # JSON publishing, because ntfy's header API can't carry non-ASCII titles like "Påminnelse".
        payload: dict[str, object] = {
            "topic": self.topic,
            "message": _cut(message.text),
            "priority": _PRIORITIES[message.priority],
        }
        if message.title:
            payload["title"] = message.title
        if message.tags:
            payload["tags"] = list(message.tags)
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()[:200]
            raise NotifyError(f"ntfy refused the message (HTTP {exc.code}): {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise NotifyError(f"ntfy is unreachable at {self.url}: {exc}") from exc


def _cut(text: str) -> str:
    data = text.encode()
    if len(data) <= MAX_BYTES:
        return text
    return data[: MAX_BYTES - 3].decode(errors="ignore").rstrip() + "…"

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from home_brain.config import Settings
from home_brain.notify import MAX_BYTES, Message, NotifyError, Ntfy


class FakeNtfyServer(ThreadingHTTPServer):
    status = 200
    received: list[tuple[dict[str, str], dict]]


class Handler(BaseHTTPRequestHandler):
    server: FakeNtfyServer

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append((dict(self.headers), body))
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(b'{"id":"abc"}' if self.server.status == 200 else b'{"error":"forbidden"}')

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def ntfy() -> Iterator[FakeNtfyServer]:
    server = FakeNtfyServer(("127.0.0.1", 0), Handler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def url(server: FakeNtfyServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/"


def test_publishes_json_with_the_token(ntfy: FakeNtfyServer) -> None:
    Ntfy(url(ntfy), "family", "tk_test").send(Message("Pakk gymtøy", title="Påminnelse", priority="high", tags=("bell",)))
    [(headers, body)] = ntfy.received
    assert headers["Authorization"] == "Bearer tk_test"
    assert headers["Content-Type"] == "application/json"
    assert body == {"topic": "family", "message": "Pakk gymtøy", "title": "Påminnelse", "priority": 4, "tags": ["bell"]}


def test_without_token_or_title(ntfy: FakeNtfyServer) -> None:
    Ntfy(url(ntfy), "family").send(Message("Hello"))
    [(headers, body)] = ntfy.received
    assert "Authorization" not in headers
    assert body == {"topic": "family", "message": "Hello", "priority": 3}


def test_refused_message(ntfy: FakeNtfyServer) -> None:
    ntfy.status = 403
    with pytest.raises(NotifyError, match="HTTP 403.*forbidden"):
        Ntfy(url(ntfy), "family", "tk_wrong").send(Message("Hello"))


def test_unreachable_server(ntfy: FakeNtfyServer) -> None:
    address = url(ntfy)
    ntfy.shutdown()
    ntfy.server_close()
    with pytest.raises(NotifyError, match="unreachable"):
        Ntfy(address, "family", timeout=3).send(Message("Hello"))


def test_long_messages_are_cut(ntfy: FakeNtfyServer) -> None:
    Ntfy(url(ntfy), "family").send(Message("ø" * 3000))
    message = ntfy.received[0][1]["message"]
    assert len(message.encode()) <= MAX_BYTES
    assert message.endswith("ø…")


def test_from_settings(tmp_path: Path) -> None:
    settings = Settings(vault_path=tmp_path, data_path=tmp_path)
    assert Ntfy.from_settings(settings) is None
    ntfy = Ntfy.from_settings(Settings(vault_path=tmp_path, data_path=tmp_path, ntfy_url="http://ntfy/", ntfy_topic="t"))
    assert (ntfy.url, ntfy.topic) == ("http://ntfy", "t")

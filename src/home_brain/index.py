"""SQLite FTS5 index over the vault.

The vault is the source of truth; the index is disposable and rebuilt from it.
Changes are detected by comparing (mtime, size) with a directory scan, because
inotify doesn't work on NFS mounts and notes may be edited outside home-brain
(e.g. in Obsidian). A scan runs at most every few seconds before queries, and
immediately after home-brain's own writes.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from . import frontmatter
from .frontmatter import FrontmatterError
from .vault import Vault, VaultError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_TOKEN = re.compile(r"\w+", re.UNICODE)
_H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)

_SCHEMA = """
CREATE TABLE notes (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    folder      TEXT NOT NULL,
    title       TEXT NOT NULL,
    type        TEXT,
    tags        TEXT NOT NULL,
    frontmatter TEXT NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    size        INTEGER NOT NULL
);
CREATE INDEX notes_type ON notes(type);
CREATE VIRTUAL TABLE notes_fts USING fts5(
    path, title, tags, meta, body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

# bm25 column weights: path, title, tags, meta, body
_WEIGHTS = (2.0, 5.0, 3.0, 2.0, 1.0)


class Index:
    def __init__(
        self,
        vault: Vault,
        db_path: Path,
        min_refresh_interval: float = 2.0,
        search_exclude: frozenset[str] = frozenset({"_conventions.md"}),
    ) -> None:
        self._vault = vault
        self._db_path = Path(db_path)
        self._interval = min_refresh_interval
        # Notes that are readable but would only add noise to search results
        # (the conventions file is already in the server instructions).
        self._search_exclude = search_exclude
        self._last_refresh = float("-inf")
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            self._conn.executescript(
                "DROP TABLE IF EXISTS notes_fts; DROP TABLE IF EXISTS notes;"
                + _SCHEMA
                + f"PRAGMA user_version = {SCHEMA_VERSION};"
            )

    def rebuild(self) -> dict[str, int]:
        with self._lock:
            self._conn.executescript("DELETE FROM notes_fts; DELETE FROM notes;")
            return self.refresh(force=True)

    # --- sync with disk ------------------------------------------------------

    def refresh(self, force: bool = False) -> dict[str, int]:
        """Bring the index in line with the vault. Cheap when nothing changed."""
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_refresh < self._interval:
                return {"added": 0, "updated": 0, "removed": 0}
            on_disk = {note.path: (note.mtime_ns, note.size) for note in self._vault.iter_notes()}
            known = {
                row["path"]: (row["id"], row["mtime_ns"], row["size"])
                for row in self._conn.execute("SELECT id, path, mtime_ns, size FROM notes")
            }
            stats = {"added": 0, "updated": 0, "removed": 0}
            with self._conn:
                for path in known.keys() - on_disk.keys():
                    self._delete(known[path][0])
                    stats["removed"] += 1
                for path, signature in on_disk.items():
                    existing = known.get(path)
                    if existing and existing[1:] == signature:
                        continue
                    if self._upsert(path, existing[0] if existing else None):
                        stats["updated" if existing else "added"] += 1
            self._last_refresh = time.monotonic()
            if any(stats.values()):
                log.info("index refreshed: %s", stats)
            return stats

    def _delete(self, note_id: int) -> None:
        self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def _upsert(self, path: str, note_id: int | None) -> bool:
        try:
            rel, text, stat = self._vault.read(path)
        except (VaultError, OSError, UnicodeDecodeError) as exc:
            log.warning("skipping %s: %s", path, exc)
            return False
        try:
            meta, body = frontmatter.parse(text)
            plain = frontmatter.to_plain(meta)
        except FrontmatterError as exc:
            log.warning("%s: %s; indexing it as plain text", rel, exc)
            plain, body = {}, text

        title = _title(rel, plain, body)
        note_type = plain.get("type") if isinstance(plain.get("type"), str) else None
        tags = _tags(plain.get("tags"))
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        row = (
            rel, folder, title, note_type, json.dumps(tags, ensure_ascii=False),
            json.dumps(plain, ensure_ascii=False), stat.st_mtime_ns, stat.st_size,
        )
        if note_id is None:
            cur = self._conn.execute(
                "INSERT INTO notes (path, folder, title, type, tags, frontmatter, mtime_ns, size)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            note_id = cur.lastrowid
        else:
            self._conn.execute(
                "UPDATE notes SET path = ?, folder = ?, title = ?, type = ?, tags = ?, frontmatter = ?,"
                " mtime_ns = ?, size = ? WHERE id = ?",
                (*row, note_id),
            )
            self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        self._conn.execute(
            "INSERT INTO notes_fts (rowid, path, title, tags, meta, body) VALUES (?, ?, ?, ?, ?, ?)",
            (note_id, rel, title, " ".join(tags), "\n".join(_flatten(plain)), body),
        )
        return True

    # --- queries -------------------------------------------------------------

    def search(
        self, query: str, folder: str = "", note_type: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Keyword search, best matches first. Every word is prefix-matched and any word may match."""
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        self.refresh()
        weights = ", ".join(str(w) for w in _WEIGHTS)
        sql = (
            "SELECT n.path, n.title, n.type, n.mtime_ns,"
            " snippet(notes_fts, -1, '**', '**', ' … ', 16) AS snippet,"
            f" bm25(notes_fts, {weights}) AS score"
            " FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid"
            " WHERE notes_fts MATCH ?"
        )
        params: list[Any] = [fts_query]
        sql, params = _filters(sql, params, folder, note_type)
        if self._search_exclude:
            sql += f" AND n.path NOT IN ({', '.join('?' * len(self._search_exclude))})"
            params += sorted(self._search_exclude)
        sql += " ORDER BY score LIMIT ?"
        params.append(max(1, min(limit, 50)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "path": r["path"],
                "title": r["title"],
                "type": r["type"],
                "snippet": r["snippet"],
                "modified": _iso(r["mtime_ns"]),
            }
            for r in rows
        ]

    def list_notes(
        self, folder: str = "", note_type: str | None = None, recursive: bool = True, limit: int = 200
    ) -> dict[str, Any]:
        """Notes under a folder (path order) plus its immediate subfolders."""
        self.refresh()
        sql = "SELECT path, title, type, mtime_ns FROM notes n WHERE 1 = 1"
        sql, params = _filters(sql, [], folder, note_type)
        sql += " ORDER BY path"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        prefix = f"{folder}/" if folder else ""
        subfolders = sorted({r["path"][len(prefix) :].split("/")[0] for r in rows if "/" in r["path"][len(prefix) :]})
        if not recursive:
            rows = [r for r in rows if "/" not in r["path"][len(prefix) :]]
        notes = [
            {"path": r["path"], "title": r["title"], "type": r["type"], "modified": _iso(r["mtime_ns"])}
            for r in rows[:limit]
        ]
        return {"folder": folder, "subfolders": subfolders, "notes": notes, "truncated": len(rows) > limit}

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]


def build_fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query: each word quoted and prefix-matched, OR-ed together.

    Single letters are dropped (they would match almost everything); digits are kept.
    """
    tokens = [t for t in _TOKEN.findall(text) if len(t) > 1 or t.isdigit()]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in dict.fromkeys(tokens))


def _filters(sql: str, params: list[Any], folder: str, note_type: str | None) -> tuple[str, list[Any]]:
    if folder:
        sql += " AND (n.folder = ? OR substr(n.folder, 1, ?) = ?)"
        params += [folder, len(folder) + 1, f"{folder}/"]
    if note_type:
        sql += " AND n.type = ?"
        params.append(note_type)
    return sql, params


def _title(path: str, meta: dict[str, Any], body: str) -> str:
    for key in ("title", "name"):
        if isinstance(meta.get(key), str) and meta[key].strip():
            return meta[key].strip()
    if match := _H1.search(body):
        return match.group(1)
    return PurePosixPath(path).stem


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(t).strip().lstrip("#") for t in value if str(t).strip()]


def _flatten(value: Any, prefix: str = "") -> list[str]:
    """Frontmatter as `key.sub: value` lines, so keys and values are both searchable."""
    if isinstance(value, dict):
        return [line for k, v in value.items() for line in _flatten(v, f"{prefix}{k}.")]
    if isinstance(value, list):
        return [line for item in value for line in _flatten(item, prefix)]
    if isinstance(value, bool):
        value = str(value).lower()
    return [f"{prefix.rstrip('.')}: {value}"]


def _iso(mtime_ns: int) -> str:
    return dt.datetime.fromtimestamp(mtime_ns / 1e9).astimezone().isoformat(timespec="seconds")

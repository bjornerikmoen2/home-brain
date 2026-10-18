"""SQLite FTS5 index over the vault.

The vault is the source of truth; the index is disposable and rebuilt from it.
Changes are detected by comparing (mtime, size) with a directory scan, because
inotify doesn't work on NFS mounts and notes may be edited outside home-brain
(e.g. in Obsidian). A scan runs at most every few seconds before queries, and
immediately after home-brain's own writes.

Attachments (PDFs) are indexed one row per page, so a search hit can name the page.
Their text is extracted once per change and kept here for `read_attachment`.
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

from . import documents, frontmatter
from .frontmatter import FrontmatterError
from .vault import Vault, VaultError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
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
CREATE TABLE files (
    id       INTEGER PRIMARY KEY,
    path     TEXT NOT NULL UNIQUE,
    folder   TEXT NOT NULL,
    pages    INTEGER NOT NULL,
    chars    INTEGER NOT NULL,
    error    TEXT,
    mtime_ns INTEGER NOT NULL,
    size     INTEGER NOT NULL
);
CREATE TABLE file_pages (
    id      INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    page    INTEGER NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX file_pages_file ON file_pages(file_id, page);
CREATE VIRTUAL TABLE pages_fts USING fts5(
    path, title, body,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

# bm25 column weights: path, title, tags, meta, body
_WEIGHTS = (2.0, 5.0, 3.0, 2.0, 1.0)
# and for attachment pages: path, title, body
_PAGE_WEIGHTS = (1.0, 2.0, 1.0)
READ_CHARS = 30_000
"""How much attachment text `read_attachment` returns per call (about 8 000 tokens)."""
_DROP_ALL = (
    "DROP TABLE IF EXISTS notes_fts; DROP TABLE IF EXISTS notes;"
    " DROP TABLE IF EXISTS pages_fts; DROP TABLE IF EXISTS file_pages; DROP TABLE IF EXISTS files;"
)


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
            self._conn.executescript(_DROP_ALL + _SCHEMA + f"PRAGMA user_version = {SCHEMA_VERSION};")

    def rebuild(self) -> dict[str, int]:
        with self._lock:
            self._conn.executescript(
                "DELETE FROM notes_fts; DELETE FROM notes; DELETE FROM pages_fts; DELETE FROM file_pages; DELETE FROM files;"
            )
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
                self._refresh_files(stats)
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

    def _refresh_files(self, stats: dict[str, int]) -> None:
        on_disk = {f.path: f for f in self._vault.iter_attachments()}
        known = {
            row["path"]: (row["id"], row["mtime_ns"], row["size"])
            for row in self._conn.execute("SELECT id, path, mtime_ns, size FROM files")
        }
        for path in known.keys() - on_disk.keys():
            self._delete_file(known[path][0])
            stats["removed"] += 1
        for path, found in on_disk.items():
            existing = known.get(path)
            if existing and existing[1:] == (found.mtime_ns, found.size):
                continue
            if existing:
                self._delete_file(existing[0])
            # A file still being copied in gets indexed as it is now; its next change re-reads it.
            try:
                extracted = documents.extract(self._vault.attachment_path(path)[1])
            except (VaultError, OSError) as exc:
                log.warning("cannot open %s: %s", path, exc)
                extracted = documents.Extracted(0, error="the file could not be opened")
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
            title = PurePosixPath(path).stem
            cur = self._conn.execute(
                "INSERT INTO files (path, folder, pages, chars, error, mtime_ns, size)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (path, folder, extracted.page_count, sum(map(len, extracted.pages)), extracted.error,
                 found.mtime_ns, found.size),
            )
            for number, text in enumerate(extracted.pages, start=1):
                page = self._conn.execute(
                    "INSERT INTO file_pages (file_id, page, text) VALUES (?, ?, ?)", (cur.lastrowid, number, text)
                )
                self._conn.execute(
                    "INSERT INTO pages_fts (rowid, path, title, body) VALUES (?, ?, ?, ?)",
                    (page.lastrowid, path, title, text),
                )
            if extracted.error:
                log.warning("%s: %s", path, extracted.error)
            stats["updated" if existing else "added"] += 1

    def _delete_file(self, file_id: int) -> None:
        self._conn.execute("DELETE FROM pages_fts WHERE rowid IN (SELECT id FROM file_pages WHERE file_id = ?)", (file_id,))
        self._conn.execute("DELETE FROM file_pages WHERE file_id = ?", (file_id,))
        self._conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

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

    def search_attachments(self, query: str, folder: str = "", limit: int = 10) -> list[dict[str, Any]]:
        """Attachments with pages matching the query, best first: the best page of each, plus the
        other matching pages."""
        fts_query = build_fts_query(query)
        if not fts_query:
            return []
        self.refresh()
        weights = ", ".join(str(w) for w in _PAGE_WEIGHTS)
        sql = (
            "SELECT f.path, f.mtime_ns, p.page,"
            " snippet(pages_fts, 2, '**', '**', ' … ', 20) AS snippet,"
            f" bm25(pages_fts, {weights}) AS score"
            " FROM pages_fts JOIN file_pages p ON p.id = pages_fts.rowid JOIN files f ON f.id = p.file_id"
            " WHERE pages_fts MATCH ?"
        )
        sql, params = _filters(sql, [fts_query], folder, None, alias="f")
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY score LIMIT 500", params).fetchall()
        hits: dict[str, dict[str, Any]] = {}
        for r in rows:
            hit = hits.get(r["path"])
            if hit is None and len(hits) < max(1, min(limit, 50)):
                hits[r["path"]] = {
                    "path": r["path"], "page": r["page"], "snippet": r["snippet"], "also_on_pages": [],
                    "modified": _iso(r["mtime_ns"]),
                }
            elif hit is not None and len(hit["also_on_pages"]) < 10:
                hit["also_on_pages"].append(r["page"])
        for hit in hits.values():
            if hit["also_on_pages"]:
                hit["also_on_pages"].sort()
            else:
                del hit["also_on_pages"]
        return list(hits.values())

    def list_notes(
        self, folder: str = "", note_type: str | None = None, recursive: bool = True, limit: int = 200
    ) -> dict[str, Any]:
        """Notes and attachments under a folder (path order) plus its immediate subfolders."""
        self.refresh()
        sql = "SELECT path, title, type, mtime_ns FROM notes n WHERE 1 = 1"
        sql, params = _filters(sql, [], folder, note_type)
        files_sql, files_params = _filters("SELECT path, pages, error, mtime_ns FROM files f WHERE 1 = 1", [], folder, None, alias="f")
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY path", params).fetchall()
            files = [] if note_type else self._conn.execute(files_sql + " ORDER BY path", files_params).fetchall()
        prefix = f"{folder}/" if folder else ""
        subfolders = sorted(
            {r["path"][len(prefix) :].split("/")[0] for r in [*rows, *files] if "/" in r["path"][len(prefix) :]}
        )
        if not recursive:
            rows = [r for r in rows if "/" not in r["path"][len(prefix) :]]
            files = [r for r in files if "/" not in r["path"][len(prefix) :]]
        notes = [
            {"path": r["path"], "title": r["title"], "type": r["type"], "modified": _iso(r["mtime_ns"])}
            for r in rows[:limit]
        ]
        result: dict[str, Any] = {"folder": folder, "subfolders": subfolders, "notes": notes}
        if files:
            result["attachments"] = [
                {"path": r["path"], "pages": r["pages"], "modified": _iso(r["mtime_ns"])}
                | ({"problem": r["error"]} if r["error"] else {})
                for r in files[:limit]
            ]
        result["truncated"] = len(rows) > limit or len(files) > limit
        return result

    def read_attachment(self, path: str, first_page: int = 1, last_page: int | None = None) -> dict[str, Any]:
        """The text of an attachment's pages, up to READ_CHARS; `next_page` says where to go on."""
        self.refresh()
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
            if row is None:  # maybe copied in moments ago
                self.refresh(force=True)
                row = self._conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
            if row is None:
                raise VaultError(f"attachment not found: {path} (use search_notes or list_notes to find it)")
            if not row["chars"]:
                raise VaultError(
                    f"{path} can't be read: {row['error'] or 'it has no text'}. "
                    "Ask the user to attach the PDF in the chat instead."
                )
            if first_page > row["pages"]:
                raise VaultError(f"{path} has only {row['pages']} pages")
            last = min(last_page or row["pages"], row["pages"])
            if last < first_page:
                raise VaultError("last_page is before first_page")
            pages = self._conn.execute(
                "SELECT page, text FROM file_pages WHERE file_id = ? AND page BETWEEN ? AND ? ORDER BY page",
                (row["id"], first_page, last),
            ).fetchall()
        parts: list[str] = []
        used, next_page = 0, None
        for page in pages:
            chunk = f"[Page {page['page']}]\n{page['text']}"
            if parts and used + len(chunk) > READ_CHARS:
                next_page = page["page"]
                break
            if len(chunk) > READ_CHARS:
                chunk = chunk[:READ_CHARS] + " …"
            parts.append(chunk)
            used += len(chunk)
        result: dict[str, Any] = {
            "path": path,
            "pages": row["pages"],
            "first_page": first_page,
            "last_page": next_page - 1 if next_page else last,
            "text": "\n\n".join(parts),
            "modified": _iso(row["mtime_ns"]),
        }
        if next_page:
            result["next_page"] = next_page
        if row["error"]:
            result["problem"] = row["error"]
        return result

    def linked_from(self, path: str) -> dict[str, Any]:
        """Notes whose `attachments` frontmatter lists this attachment, with that list."""
        self.refresh()
        with self._lock:
            rows = self._conn.execute(
                "SELECT n.path, n.frontmatter FROM notes n, json_each(n.frontmatter, '$.attachments') j"
                " WHERE j.value = ? GROUP BY n.path ORDER BY n.path",
                (path,),
            ).fetchall()
        return {r["path"]: json.loads(r["frontmatter"])["attachments"] for r in rows}

    def frontmatter(self, folder: str = "") -> list[tuple[str, str, dict[str, Any]]]:
        """(path, title, frontmatter) of every note under a folder, for the agenda."""
        self.refresh()
        sql, params = _filters("SELECT path, title, frontmatter FROM notes n WHERE 1 = 1", [], folder, None)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY path", params).fetchall()
        return [(r["path"], r["title"], json.loads(r["frontmatter"])) for r in rows]

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


def _filters(
    sql: str, params: list[Any], folder: str, note_type: str | None, alias: str = "n"
) -> tuple[str, list[Any]]:
    if folder:
        sql += f" AND ({alias}.folder = ? OR substr({alias}.folder, 1, ?) = ?)"
        params += [folder, len(folder) + 1, f"{folder}/"]
    if note_type:
        sql += f" AND {alias}.type = ?"
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

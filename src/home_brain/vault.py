"""Safe access to the markdown vault on disk.

Every path coming from a tool call is resolved and confined to the vault root.
Hidden files and folders (.git, .trash, .obsidian) are off limits, only `.md`
notes can be written, and nothing is ever hard-deleted.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from . import frontmatter
from .frontmatter import FrontmatterError

TRASH_DIR = ".trash"
_DRIVE = re.compile(r"^[A-Za-z]:")
_UMASK = os.umask(0)
os.umask(_UMASK)


class VaultError(Exception):
    """A request the vault refuses. The message is safe to show to the model."""


@dataclass(frozen=True)
class NoteFile:
    path: str
    """Vault-relative path with forward slashes, e.g. `vehicles/family-car.md`."""
    mtime_ns: int
    size: int


ChangeHook = Callable[[list[str], str], None]
"""Called with (changed vault paths, description) after each change, while the vault is locked."""


class Vault:
    def __init__(self, root: Path, on_change: ChangeHook | None = None) -> None:
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self._on_change = on_change

    def _changed(self, paths: list[str], description: str) -> None:
        if self._on_change is not None:
            self._on_change(paths, description)

    # --- paths -------------------------------------------------------------

    def note_path(self, path: str) -> tuple[str, Path]:
        """Resolve a note path from a tool call to (relative path, absolute path)."""
        parts = self._clean(path)
        if not parts:
            raise VaultError("note path is empty")
        if not parts[-1].lower().endswith(".md"):
            parts[-1] += ".md"
        return self._resolve(parts)

    def folder_path(self, folder: str | None) -> tuple[str, Path]:
        """Resolve a folder path; empty means the vault root."""
        return self._resolve(self._clean(folder or ""))

    def _clean(self, raw: str) -> list[str]:
        if "\x00" in raw:
            raise VaultError("invalid characters in path")
        text = raw.strip().replace("\\", "/")
        if text.startswith("/") or _DRIVE.match(text):
            raise VaultError("use a path relative to the vault root, like 'vehicles/family-car.md'")
        parts = [p for p in text.split("/") if p not in ("", ".")]
        for part in parts:
            if part == "..":
                raise VaultError("'..' is not allowed in paths")
            if part.startswith("."):
                raise VaultError("hidden files and folders are off limits")
        return parts

    def _resolve(self, parts: list[str]) -> tuple[str, Path]:
        full = self.root.joinpath(*parts).resolve()
        if full != self.root and not full.is_relative_to(self.root):
            raise VaultError("path points outside the vault")
        return "/".join(parts), full

    # --- reading -----------------------------------------------------------

    def iter_notes(self) -> Iterator[NoteFile]:
        """All visible `.md` files in the vault, in path order."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith(".") or not name.lower().endswith(".md"):
                    continue
                full = Path(dirpath, name)
                try:
                    stat = full.stat()
                except FileNotFoundError:
                    continue
                yield NoteFile(full.relative_to(self.root).as_posix(), stat.st_mtime_ns, stat.st_size)

    def read(self, path: str) -> tuple[str, str, os.stat_result]:
        """Return (relative path, text, stat) of a note."""
        rel, full = self.note_path(path)
        if not full.is_file():
            raise VaultError(f"note not found: {rel} (use search_notes or list_notes to find it)")
        return rel, full.read_text(encoding="utf-8"), full.stat()

    # --- writing -----------------------------------------------------------

    def write(self, path: str, content: str, overwrite: bool = False) -> tuple[str, bool]:
        """Create or replace a note. Returns (relative path, created)."""
        rel, full = self.note_path(path)
        content = content.replace("\r\n", "\n")
        if not content.endswith("\n"):
            content += "\n"
        try:
            frontmatter.parse(content)
        except FrontmatterError as exc:
            raise VaultError(str(exc)) from exc
        with self._lock:
            exists = full.exists()
            if exists and not overwrite:
                raise VaultError(
                    f"{rel} already exists; read it and use patch_note, or pass overwrite=true to replace it"
                )
            if exists and not full.is_file():
                raise VaultError(f"{rel} is a folder")
            _atomic_write(full, content)
            self._changed([rel], f"{'Replace' if exists else 'Create'} {rel}")
        return rel, not exists

    def patch(
        self,
        path: str,
        *,
        set_frontmatter: dict[str, Any] | None = None,
        append_to_list: dict[str, Any] | None = None,
        append_text: str | None = None,
        under_heading: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Edit a note in place. Returns (relative path, updated frontmatter)."""
        if not (set_frontmatter or append_to_list or append_text):
            raise VaultError("nothing to change: give set_frontmatter, append_to_list and/or append_text")
        with self._lock:
            rel, text, _ = self.read(path)
            try:
                meta, body = frontmatter.parse(text)
                if set_frontmatter:
                    frontmatter.merge_patch(meta, set_frontmatter)
                for key_path, items in (append_to_list or {}).items():
                    frontmatter.append_to_list(meta, key_path, items)
            except FrontmatterError as exc:
                raise VaultError(f"{rel}: {exc}") from exc
            if append_text:
                body = frontmatter.append_text(body, append_text, under_heading)
            if set_frontmatter or append_to_list:
                new_text = frontmatter.render(meta, body)
            else:  # body-only edit: don't let the YAML dumper reformat the frontmatter
                new_text = frontmatter.replace_body(text, body)
            _atomic_write(self.note_path(rel)[1], new_text)
            self._changed([rel], f"Update {rel}")
        return rel, frontmatter.to_plain(meta)

    def move(self, path: str, new_path: str) -> tuple[str, str]:
        """Rename or refile a note. Never overwrites."""
        with self._lock:
            src_rel, src = self.note_path(path)
            dst_rel, dst = self.note_path(new_path)
            if not src.is_file():
                raise VaultError(f"note not found: {src_rel}")
            if dst.exists():
                raise VaultError(f"{dst_rel} already exists")
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            self._changed([src_rel, dst_rel], f"Move {src_rel} to {dst_rel}")
        return src_rel, dst_rel

    def delete(self, path: str) -> tuple[str, str]:
        """Soft delete: move the note to `.trash/<timestamp>/<path>`."""
        with self._lock:
            rel, full = self.note_path(path)
            if not full.is_file():
                raise VaultError(f"note not found: {rel}")
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            trashed = self.root / TRASH_DIR / stamp / rel
            trashed.parent.mkdir(parents=True, exist_ok=True)
            full.rename(trashed)
            self._changed([rel], f"Delete {rel}")
        return rel, f"{TRASH_DIR}/{stamp}/{rel}"

    # --- starter template --------------------------------------------------

    def seed(self, template: Traversable | None = None) -> list[str]:
        """Copy the starter vault into this vault, skipping files that already exist."""
        template = template or files("home_brain") / "vault_template"
        copied: list[str] = []
        with self._lock:
            for rel, source in _walk_template(template):
                target = self.root / rel
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                copied.append(rel)
            (self.root / "inbox").mkdir(parents=True, exist_ok=True)
        return copied

    def is_empty(self) -> bool:
        return not self.root.exists() or not any(self.root.iterdir())


def _walk_template(node: Traversable, prefix: str = "") -> Iterator[tuple[str, Traversable]]:
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        if child.name.startswith(("__", ".")):
            continue
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            yield from _walk_template(child, f"{rel}/")
        else:
            yield rel, child


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates 0600 files; keep the existing mode (or the umask default) so
    # other users of the share (e.g. Obsidian over SMB) can still read the note.
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o666 & ~_UMASK
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise

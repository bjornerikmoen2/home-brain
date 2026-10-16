"""Git history for the vault: one commit per change, so every edit Claude makes can be undone.

The vault is its own git repository (never the code repo). Each tool write becomes a commit
with a descriptive message; edits made outside home-brain (Obsidian, SMB) are picked up by
the next commit and listed in its message. Pushing to a remote comes later.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)

GITIGNORE = """\
# Managed by home-brain
.trash/
.obsidian/workspace*.json
.DS_Store
*.tmp
"""


class GitError(RuntimeError):
    pass


class VaultGit:
    def __init__(self, root: Path, author_name: str = "home-brain", author_email: str = "home-brain@localhost") -> None:
        self.root = Path(root).resolve()
        self._author = (author_name, author_email)
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def ensure_repo(self) -> None:
        """Initialise the vault as a git repo if needed and commit whatever is there."""
        with self._lock:
            if not (self.root / ".git").exists():
                self.root.mkdir(parents=True, exist_ok=True)
                self._git("init", "-q", "-b", "main")
                log.info("initialised git repository in %s", self.root)
            gitignore = self.root / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(GITIGNORE, encoding="utf-8", newline="\n")
            self._commit_all("Sync edits made outside home-brain", own_paths=())

    def commit(self, paths: Sequence[str], message: str) -> str | None:
        """Commit the vault, describing the change to `paths`. Returns the commit id, or None if nothing changed."""
        with self._lock:
            return self._commit_all(message, own_paths=paths)

    def _commit_all(self, message: str, own_paths: Sequence[str]) -> str | None:
        self._git("add", "-A")
        staged = [p for p in self._git("diff", "--cached", "--name-only", "-z").split("\0") if p]
        if not staged:
            return None
        others = sorted(set(staged) - set(own_paths))
        if own_paths and others:
            message += "\n\nAlso includes edits made outside home-brain:\n" + "\n".join(f"- {p}" for p in others)
        elif not own_paths:
            message += "\n\n" + "\n".join(f"- {p}" for p in staged)
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "--short", "HEAD").strip()

    def log(self, limit: int = 10) -> list[str]:
        """Recent commit subjects, newest first."""
        with self._lock:
            out = self._git("log", f"-{limit}", "--format=%s")
        return [line for line in out.splitlines() if line]

    def _git(self, *args: str) -> str:
        name, email = self._author
        cmd = [
            "git",
            "-C", str(self.root),
            # The vault may live on an NFS share owned by another UID.
            "-c", f"safe.directory={self.root.as_posix()}",
            "-c", "core.autocrlf=false",
            "-c", "core.quotepath=false",
            "-c", f"user.name={name}",
            "-c", f"user.email={email}",
            # A background service can't sign commits interactively.
            "-c", "commit.gpgsign=false",
            *args,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

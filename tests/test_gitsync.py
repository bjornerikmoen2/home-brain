from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest
from fastmcp import Client

from home_brain.config import Settings
from home_brain.gitsync import GitError, VaultGit
from home_brain.server import create_server, open_vault
from home_brain.vault import Vault

pytestmark = pytest.mark.skipif(not VaultGit.available(), reason="git is not installed")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout


@pytest.fixture
def git_settings(settings: Settings) -> Settings:
    return dataclasses.replace(settings, vault_git=True)


def test_ensure_repo_commits_existing_files_once(git_settings: Settings) -> None:
    repo = VaultGit(git_settings.vault_path)
    repo.ensure_repo()
    assert (git_settings.vault_path / ".git").is_dir()
    assert ".trash/" in (git_settings.vault_path / ".gitignore").read_text(encoding="utf-8")
    assert repo.log() == ["Sync edits made outside home-brain"]
    assert "people/kari.md" in git(git_settings.vault_path, "ls-files")
    repo.ensure_repo()
    assert len(repo.log()) == 1  # nothing new to commit


def test_every_change_is_one_commit(git_settings: Settings) -> None:
    vault = open_vault(git_settings)
    vault.write("events/2026-11-01-dentist.md", "# Dentist\n")
    vault.write("events/2026-11-01-dentist.md", "# Dentist at 10\n", overwrite=True)
    vault.patch("people/kari.md", append_text="- Pottery class", under_heading="Gift ideas")
    vault.move("events/2026-11-01-dentist.md", "events/2026-11-02-dentist.md")
    vault.delete("people/kari.md")

    assert VaultGit(git_settings.vault_path).log()[:5] == [
        "Delete people/kari.md",
        "Move events/2026-11-01-dentist.md to events/2026-11-02-dentist.md",
        "Update people/kari.md",
        "Replace events/2026-11-01-dentist.md",
        "Create events/2026-11-01-dentist.md",
    ]
    assert git(git_settings.vault_path, "status", "--porcelain") == ""  # .trash is ignored
    assert "people/kari.md" in git(git_settings.vault_path, "show", "--stat", "HEAD")


def test_outside_edits_are_listed_in_the_next_commit(git_settings: Settings) -> None:
    vault = open_vault(git_settings)
    (vault.root / "people" / "bjørn.md").write_text("# Bjørn\n", encoding="utf-8")  # e.g. Obsidian
    vault.write("inbox/idea.md", "An idea\n")

    message = git(git_settings.vault_path, "log", "-1", "--format=%B")
    assert message.startswith("Create inbox/idea.md\n\nAlso includes edits made outside home-brain:\n")
    assert "- people/bjørn.md" in message


def test_failed_commit_does_not_fail_the_tool(git_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = open_vault(git_settings)

    def broken(self, paths, message):
        raise GitError("disk full")

    monkeypatch.setattr(VaultGit, "commit", broken)
    assert vault.write("inbox/still-saved.md", "x") == ("inbox/still-saved.md", True)
    assert (vault.root / "inbox" / "still-saved.md").is_file()


def test_versioning_off_or_git_missing(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    open_vault(settings).write("inbox/a.md", "a")
    assert not (settings.vault_path / ".git").exists()

    monkeypatch.setattr(VaultGit, "available", staticmethod(lambda: False))
    open_vault(dataclasses.replace(settings, vault_git=True)).write("inbox/b.md", "b")
    assert not (settings.vault_path / ".git").exists()


async def test_tool_writes_through_the_server_are_committed(git_settings: Settings) -> None:
    async with Client(create_server(git_settings)) as client:
        await client.call_tool("write_note", {"path": "vehicles/bike.md", "content": "# Bike\n"})
        await client.call_tool("patch_note", {"path": "vehicles/bike.md", "set_frontmatter": {"type": "vehicle"}})
    assert VaultGit(git_settings.vault_path).log()[:2] == ["Update vehicles/bike.md", "Create vehicles/bike.md"]


def test_git_errors_carry_the_message(tmp_path: Path) -> None:
    repo = VaultGit(tmp_path / "not-a-repo")
    (tmp_path / "not-a-repo").mkdir()
    with pytest.raises(GitError, match="git log failed"):
        repo.log()


def test_seeded_vault_is_ready_for_git(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "v")
    vault.seed()
    VaultGit(vault.root).ensure_repo()
    assert git(vault.root, "status", "--porcelain") == ""

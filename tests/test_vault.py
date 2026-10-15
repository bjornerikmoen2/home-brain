from __future__ import annotations

import os
from pathlib import Path

import pytest

from home_brain.vault import Vault, VaultError


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "people/../../outside.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        ".git/config",
        "people/.secret.md",
        ".trash/old.md",
        "bad\x00name.md",
        "",
        "   ",
    ],
)
def test_rejects_unsafe_paths(vault: Vault, path: str) -> None:
    with pytest.raises(VaultError):
        vault.note_path(path)


def test_normalizes_paths(vault: Vault) -> None:
    assert vault.note_path("vehicles\\family-car")[0] == "vehicles/family-car.md"
    assert vault.note_path("./people//kari.md")[0] == "people/kari.md"
    assert vault.folder_path("")[0] == ""
    assert vault.folder_path("school/ola/")[0] == "school/ola"


def test_symlink_out_of_vault_is_rejected(vault: Vault, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside, vault.root / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted here")
    with pytest.raises(VaultError, match="outside the vault"):
        vault.read("linked/secret.md")


def test_write_creates_folders_and_refuses_overwrite(vault: Vault) -> None:
    rel, created = vault.write("events/2026-11-01-dentist", "---\ntype: event\n---\n\n# Dentist")
    assert (rel, created) == ("events/2026-11-01-dentist.md", True)
    assert (vault.root / rel).read_text(encoding="utf-8") == "---\ntype: event\n---\n\n# Dentist\n"
    with pytest.raises(VaultError, match="already exists"):
        vault.write(rel, "new")
    assert vault.write(rel, "replaced", overwrite=True) == (rel, False)


def test_write_rejects_broken_frontmatter(vault: Vault) -> None:
    with pytest.raises(VaultError, match="invalid YAML"):
        vault.write("inbox/broken.md", "---\ntype: [oops\n---\n")
    assert not (vault.root / "inbox" / "broken.md").exists()


def test_patch_updates_frontmatter_lists_and_log(vault: Vault) -> None:
    rel, meta = vault.patch(
        "vehicles/family-car.md",
        set_frontmatter={"tires": {"winter": {"tread_mm": 4.0, "measured": "2026-10-28"}}},
        append_to_list={"tire_changes": ["2026-10-28"]},
        append_text="- 2026-10-28: Changed to winter tires. Tread 4.0 mm.",
        under_heading="Log",
    )
    assert rel == "vehicles/family-car.md"
    assert meta["tires"]["winter"]["tread_mm"] == 4.0
    assert meta["tires"]["winter"]["measured"] == "2026-10-28"
    assert meta["tires"]["summer"]["tread_mm"] == 6.0
    assert meta["tire_changes"][-1] == "2026-10-28"
    text = (vault.root / rel).read_text(encoding="utf-8")
    assert text.rstrip().endswith("- 2026-10-28: Changed to winter tires. Tread 4.0 mm.")


def test_patch_needs_a_change_and_an_existing_note(vault: Vault) -> None:
    with pytest.raises(VaultError, match="nothing to change"):
        vault.patch("people/kari.md")
    with pytest.raises(VaultError, match="not found"):
        vault.patch("people/nobody.md", append_text="x")


def test_move_never_overwrites(vault: Vault) -> None:
    vault.write("inbox/note.md", "hello")
    assert vault.move("inbox/note.md", "house/note.md") == ("inbox/note.md", "house/note.md")
    vault.write("inbox/note.md", "again")
    with pytest.raises(VaultError, match="already exists"):
        vault.move("inbox/note.md", "house/note.md")


def test_delete_moves_to_trash(vault: Vault) -> None:
    rel, trashed = vault.delete("people/kari.md")
    assert not (vault.root / rel).exists()
    assert (vault.root / trashed).is_file()
    assert trashed.startswith(".trash/")
    assert "people/kari.md" not in {n.path for n in vault.iter_notes()}


def test_seed_never_overwrites(vault: Vault) -> None:
    me = vault.root / "people" / "me.md"
    me.write_text("mine", encoding="utf-8")
    assert vault.seed() == []
    assert me.read_text(encoding="utf-8") == "mine"
    assert (vault.root / "inbox").is_dir()
    assert (vault.root / "_conventions.md").is_file()


def test_iter_notes_skips_hidden_and_non_markdown(vault: Vault) -> None:
    (vault.root / ".obsidian").mkdir()
    (vault.root / ".obsidian" / "x.md").write_text("x", encoding="utf-8")
    (vault.root / "inbox" / "letter.pdf").write_bytes(b"%PDF")
    paths = {n.path for n in vault.iter_notes()}
    assert "_conventions.md" in paths
    assert "school/ola/_weekly.md" in paths
    assert not any(p.startswith(".") or not p.endswith(".md") for p in paths)

from __future__ import annotations

import os
from unittest.mock import ANY

import pytest
from conftest import POLICY_PDF, make_pdf

from home_brain.index import Index, build_fts_query
from home_brain.vault import Vault, VaultError


def paths(results: list[dict]) -> list[str]:
    return [r["path"] for r in results]


def test_indexes_the_starter_vault(vault: Vault, index: Index) -> None:
    index.refresh(force=True)
    assert index.count() == len(list(vault.iter_notes()))


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("forest trip", "school/ola/2026-w43.md"),  # body + frontmatter
        ("235/55R18", "vehicles/family-car.md"),  # frontmatter value
        ("pottery", "people/kari.md"),
        ("Per party", "events/2026-10-24-per-birthday-party.md"),
        ("ventilation filter", "house/house.md"),
    ],
)
def test_search_ranks_the_right_note_first(index: Index, query: str, expected: str) -> None:
    assert paths(index.search(query))[0] == expected


def test_search_prefix_and_diacritics(vault: Vault, index: Index) -> None:
    vault.write("house/garage.md", "# Garasje\n\nDekkskift på bilen gjøres i oktober.\n")
    assert "house/garage.md" in paths(index.search("dekk"))
    assert "house/garage.md" in paths(index.search("pa bilen"))
    assert "house/garage.md" in paths(index.search("gjøres"))


def test_search_skips_conventions_but_list_shows_it(index: Index) -> None:
    assert "_conventions.md" not in paths(index.search("conventions filing rules recipes"))
    assert "_conventions.md" in [n["path"] for n in index.list_notes()["notes"]]


def test_search_filters(index: Index) -> None:
    assert set(paths(index.search("ola", folder="school"))) == {"school/ola/_weekly.md", "school/ola/2026-w43.md"}
    assert paths(index.search("ola", note_type="event")) == ["events/2026-10-24-per-birthday-party.md"]
    assert index.search("ola", folder="vehicles") == []


@pytest.mark.parametrize("query", ['AND OR "', "tire*", "what's (up)?", "NEAR(a b)", "col:val", "-", ""])
def test_search_never_raises_on_fts_syntax(index: Index, query: str) -> None:
    index.search(query)


def test_build_fts_query() -> None:
    assert build_fts_query("Gym for Ola i morgen?") == '"Gym"* OR "for"* OR "Ola"* OR "morgen"*'
    assert build_fts_query("4 b") == '"4"*'
    assert build_fts_query("?!") is None


def test_refresh_picks_up_external_edits(vault: Vault, index: Index) -> None:
    index.refresh(force=True)
    (vault.root / "inbox" / "from-obsidian.md").write_text("# Snow tires quote\n", encoding="utf-8")
    os.remove(vault.root / "people" / "kari.md")
    car = vault.root / "vehicles" / "family-car.md"
    car.write_text(car.read_text(encoding="utf-8") + "\nWindscreen chip repaired.\n", encoding="utf-8")

    assert index.refresh(force=True) == {"added": 1, "updated": 1, "removed": 1}
    assert paths(index.search("windscreen")) == ["vehicles/family-car.md"]
    assert index.search("pottery") == []
    assert index.refresh(force=True) == {"added": 0, "updated": 0, "removed": 0}


def test_broken_frontmatter_is_indexed_as_text(vault: Vault, index: Index) -> None:
    (vault.root / "inbox" / "broken.md").write_text("---\ntype: [oops\n---\nUnique zebra words\n", encoding="utf-8")
    index.refresh(force=True)
    assert paths(index.search("zebra")) == ["inbox/broken.md"]


def test_list_notes(index: Index) -> None:
    root = index.list_notes(recursive=False)
    assert root["subfolders"] == ["events", "house", "people", "school", "vehicles"]
    assert [n["path"] for n in root["notes"]] == ["_conventions.md"]

    school = index.list_notes("school")
    assert school["subfolders"] == ["ola"]
    assert [n["path"] for n in school["notes"]] == ["school/ola/2026-w43.md", "school/ola/_weekly.md"]
    assert school["notes"][1]["title"] == "Ola – weekly school routine"

    people = index.list_notes(note_type="person")
    assert {n["title"] for n in people["notes"]} == {"Ola Nordmann (senior)", "Kari Nordmann", "Ola Nordmann"}


def add_pdf(vault: Vault, path: str, data: bytes = POLICY_PDF) -> None:
    full = vault.root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)


def test_search_finds_pages_of_attachments(vault: Vault, index: Index) -> None:
    add_pdf(vault, "house/home-insurance-2026.pdf")
    add_pdf(vault, "inbox/scan.pdf", make_pdf(""))
    [hit] = index.search_attachments("deductible")
    assert hit["path"] == "house/home-insurance-2026.pdf"
    assert hit["page"] in (2, 3) and hit["also_on_pages"] == [5 - hit["page"]]
    assert "**deductible**" in hit["snippet"].lower()
    assert [h["page"] for h in index.search_attachments("snow roof")] == [3]
    assert index.search_attachments("deductible", folder="vehicles") == []
    # Attachments are listed apart from the notes.
    assert "house/home-insurance-2026.pdf" not in paths(index.search("deductible"))


def test_attachments_in_list_notes(vault: Vault, index: Index) -> None:
    add_pdf(vault, "insurance/policy.pdf")
    add_pdf(vault, "inbox/scan.pdf", make_pdf(""))
    root = index.list_notes(recursive=False)
    assert "insurance" in root["subfolders"]  # a folder with only PDFs
    assert "attachments" not in root
    assert index.list_notes()["attachments"] == [
        {"path": "inbox/scan.pdf", "pages": 1, "modified": ANY, "problem": "the PDF has no text, it is probably a scan"},
        {"path": "insurance/policy.pdf", "pages": 3, "modified": ANY},
    ]
    assert "attachments" not in index.list_notes(note_type="vehicle")


def test_read_attachment_in_parts(vault: Vault, index: Index, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pdf(vault, "house/policy.pdf")
    whole = index.read_attachment("house/policy.pdf")
    assert (whole["pages"], whole["first_page"], whole["last_page"]) == (3, 1, 3)
    assert whole["text"].startswith("[Page 1]\nHome insurance policy 2026\n")
    assert "\n\n[Page 3]\nExclusions\n" in whole["text"]
    assert "next_page" not in whole

    monkeypatch.setattr("home_brain.index.READ_CHARS", 120)
    part = index.read_attachment("house/policy.pdf")
    assert (part["last_page"], part["next_page"]) == (1, 2)
    assert index.read_attachment("house/policy.pdf", 2, 2)["text"].startswith("[Page 2]\nCover")

    with pytest.raises(VaultError, match="has only 3 pages"):
        index.read_attachment("house/policy.pdf", 4)
    with pytest.raises(VaultError, match="attachment not found"):
        index.read_attachment("house/missing.pdf")


def test_unreadable_attachment(vault: Vault, index: Index) -> None:
    add_pdf(vault, "inbox/scan.pdf", make_pdf(""))
    with pytest.raises(VaultError, match="probably a scan. Ask the user to attach the PDF in the chat"):
        index.read_attachment("inbox/scan.pdf")


def test_attachment_changes_are_picked_up(vault: Vault, index: Index) -> None:
    index.refresh(force=True)
    add_pdf(vault, "inbox/a.pdf")
    assert index.refresh(force=True) == {"added": 1, "updated": 0, "removed": 0}
    add_pdf(vault, "inbox/a.pdf", make_pdf("Car insurance\nKasko"))
    assert index.refresh(force=True) == {"added": 0, "updated": 1, "removed": 0}
    assert index.search_attachments("deductible") == []
    assert [h["path"] for h in index.search_attachments("kasko")] == ["inbox/a.pdf"]
    os.remove(vault.root / "inbox" / "a.pdf")
    assert index.refresh(force=True) == {"added": 0, "updated": 0, "removed": 1}
    assert index.search_attachments("kasko") == []


def test_linked_from(vault: Vault, index: Index) -> None:
    vault.patch("house/house.md", set_frontmatter={"attachments": ["house/policy.pdf", "house/manual.pdf"]})
    vault.patch("people/me.md", set_frontmatter={"attachments": "house/policy.pdf"})
    assert index.linked_from("house/policy.pdf") == {
        "house/house.md": ["house/policy.pdf", "house/manual.pdf"],
        "people/me.md": "house/policy.pdf",
    }
    assert index.linked_from("house/other.pdf") == {}


def test_index_survives_schema_change(vault: Vault, tmp_path) -> None:
    db = tmp_path / "data" / "index.db"
    first = Index(vault, db)
    first.refresh(force=True)
    first._conn.execute("PRAGMA user_version = 999")
    first.close()
    second = Index(vault, db)
    assert second.count() == 0  # rebuilt from scratch
    second.refresh(force=True)
    assert second.count() == len(list(vault.iter_notes()))
    second.close()

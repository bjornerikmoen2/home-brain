from __future__ import annotations

import os

import pytest

from home_brain.index import Index, build_fts_query
from home_brain.vault import Vault


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

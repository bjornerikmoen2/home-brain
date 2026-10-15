from __future__ import annotations

import pytest

from home_brain import frontmatter as fm
from home_brain.frontmatter import FrontmatterError

NOTE = """\
---
type: vehicle  # the family car
reg: AB 12345
tires:
  winter: {size: 235/55R18, tread_mm: 4.5}
  summer: {size: 235/55R18, tread_mm: 6.0}
tire_changes: [2025-10-28, 2026-04-10]
---

# Family car

## Log
- 2025-10-28: Changed to winter tires.

## Notes
Parked in the garage.
"""


def test_round_trip_is_lossless() -> None:
    meta, body = fm.parse(NOTE)
    assert fm.render(meta, body) == NOTE


def test_replace_body_keeps_frontmatter_verbatim() -> None:
    text = "---\nwho: ola   # spaced { a: 1 } style kept\nx: { a: 1 }\n---\nold body\n"
    assert fm.replace_body(text, "new body\n") == "---\nwho: ola   # spaced { a: 1 } style kept\nx: { a: 1 }\n---\nnew body\n"
    assert fm.replace_body("no frontmatter\n", "new\n") == "new\n"


def test_yaml_1_2_avoids_norway_and_sexagesimal_traps() -> None:
    meta, _ = fm.parse("---\ncountry: NO\nanswer: yes\ntime: 14:00\nfalse_flag: false\n---\n")
    assert fm.to_plain(meta) == {"country": "NO", "answer": "yes", "time": "14:00", "false_flag": False}


def test_dates_become_iso_strings() -> None:
    meta, _ = fm.parse("---\nbirthday: 2016-05-12\n---\n")
    assert fm.to_plain(meta) == {"birthday": "2016-05-12"}


def test_no_frontmatter_crlf_and_bom() -> None:
    assert fm.parse("# Just text\n") == ({}, "# Just text\n")
    meta, body = fm.parse("﻿---\r\ntype: event\r\n---\r\nBody\r\n")
    assert fm.to_plain(meta) == {"type": "event"}
    assert body == "Body\n"


def test_unterminated_or_invalid_frontmatter_raises() -> None:
    with pytest.raises(FrontmatterError, match="no closing"):
        fm.parse("---\ntype: event\n\n# Body\n")
    with pytest.raises(FrontmatterError, match="invalid YAML"):
        fm.parse("---\ntype: [unclosed\n---\n")
    with pytest.raises(FrontmatterError, match="mapping"):
        fm.parse("---\n- a list\n---\n")


def test_merge_patch_merges_nested_deletes_null_and_keeps_formatting() -> None:
    meta, body = fm.parse(NOTE)
    fm.merge_patch(meta, {"tires": {"winter": {"tread_mm": 4.0}}, "reg": None, "measured": "2026-10-28"})
    out = fm.render(meta, body)
    assert "type: vehicle  # the family car" in out
    assert "winter: {size: 235/55R18, tread_mm: 4.0}" in out
    assert "summer: {size: 235/55R18, tread_mm: 6.0}" in out
    assert "reg:" not in out
    assert "measured: 2026-10-28\n" in out  # a real date, written unquoted


def test_append_to_list_extends_flow_list_and_creates_nested() -> None:
    meta, body = fm.parse(NOTE)
    fm.append_to_list(meta, "tire_changes", ["2026-10-28"])
    fm.append_to_list(meta, "service.history", {"date": "2026-11-02", "what": "Annual service"})
    plain = fm.to_plain(meta)
    assert plain["tire_changes"] == ["2025-10-28", "2026-04-10", "2026-10-28"]
    assert plain["service"] == {"history": [{"date": "2026-11-02", "what": "Annual service"}]}
    assert "tire_changes: [2025-10-28, 2026-04-10, 2026-10-28]" in fm.render(meta, body)


def test_append_to_list_rejects_non_lists() -> None:
    meta, _ = fm.parse(NOTE)
    with pytest.raises(FrontmatterError, match="not a list"):
        fm.append_to_list(meta, "reg", ["x"])
    with pytest.raises(FrontmatterError, match="not a mapping"):
        fm.append_to_list(meta, "reg.sub", ["x"])


def test_append_text_under_existing_heading_continues_list() -> None:
    _, body = fm.parse(NOTE)
    out = fm.append_text(body, "- 2026-10-28: Changed to winter tires.", "Log")
    assert (
        "## Log\n- 2025-10-28: Changed to winter tires.\n- 2026-10-28: Changed to winter tires.\n\n## Notes" in out
    )


def test_append_text_creates_missing_heading_at_end() -> None:
    out = fm.append_text("\n# Kari\n\nSome text.\n", "- Pottery course", "## Gift ideas")
    assert out == "\n# Kari\n\nSome text.\n\n## Gift ideas\n\n- Pottery course\n"


def test_append_text_to_empty_section_and_body_end() -> None:
    out = fm.append_text("# T\n\n## Log\n\n## Next\n", "- first", "log")
    assert out == "# T\n\n## Log\n\n- first\n\n## Next\n"
    assert fm.append_text("Para one.\n", "Para two.") == "Para one.\n\nPara two.\n"
    assert fm.append_text("", "Hello") == "\nHello\n"

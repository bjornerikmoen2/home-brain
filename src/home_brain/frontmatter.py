"""Markdown notes with YAML frontmatter.

ruamel.yaml in round-trip mode keeps comments, key order and flow style of
hand-edited frontmatter when we patch it. It also speaks YAML 1.2, which avoids
the YAML 1.1 traps where `NO` (Norway) becomes False and `14:00` becomes 840.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


class FrontmatterError(ValueError):
    """Frontmatter that can't be parsed or patched; the message is meant for the user/model."""


def _yaml() -> YAML:
    yaml = YAML()  # round-trip mode
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def split(text: str) -> tuple[str | None, str]:
    """Split a note into (raw YAML, body). The YAML part is None when there is no frontmatter."""
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    raise FrontmatterError("frontmatter starts with '---' but has no closing '---' line")


def parse(text: str) -> tuple[CommentedMap, str]:
    """Parse a note into (frontmatter mapping, body)."""
    raw, body = split(text)
    if raw is None or not raw.strip():
        return CommentedMap(), body
    try:
        meta = _yaml().load(raw)
    except YAMLError as exc:
        raise FrontmatterError(f"invalid YAML in frontmatter: {exc}") from exc
    if meta is None:
        return CommentedMap(), body
    if not isinstance(meta, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping (key: value lines)")
    return meta, body


def replace_body(text: str, body: str) -> str:
    """Swap the body of a note while keeping its frontmatter block byte for byte."""
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    _, old_body = split(text)
    return text[: len(text) - len(old_body)] + body


def render(meta: dict, body: str) -> str:
    """Serialize frontmatter and body back into note text."""
    if not meta:
        return body
    buf = io.StringIO()
    _yaml().dump(meta, buf)
    return f"---\n{buf.getvalue()}---\n{body}"


def to_plain(value: Any) -> Any:
    """Convert loaded YAML into JSON-friendly values; dates become ISO strings."""
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, dt.date):  # also covers datetime
        return value.isoformat()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return str(value)


def from_json(value: Any) -> Any:
    """Prepare JSON input for YAML: ISO date strings become dates so they are written unquoted."""
    if isinstance(value, dict):
        return CommentedMap((k, from_json(v)) for k, v in value.items())
    if isinstance(value, list):
        return CommentedSeq(from_json(v) for v in value)
    if isinstance(value, str) and _ISO_DATE.match(value):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return value
    return value


def merge_patch(target: dict, patch: dict) -> None:
    """Apply an RFC 7396 merge patch in place: dicts merge, null removes a key, anything else replaces."""
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_patch(target[key], value)
        else:
            target[key] = from_json(value)


def append_to_list(meta: dict, key_path: str, items: Any) -> None:
    """Append items to the list at a dotted key path (e.g. `tires.winter.measurements`), creating it if needed."""
    keys = [k for k in key_path.split(".") if k]
    if not keys:
        raise FrontmatterError("append_to_list needs a non-empty key")
    node = meta
    for key in keys[:-1]:
        child = node.get(key)
        if child is None:
            child = node[key] = CommentedMap()
        elif not isinstance(child, dict):
            raise FrontmatterError(f"'{key}' in '{key_path}' is not a mapping")
        node = child
    new_items = from_json(items if isinstance(items, list) else [items])
    current = node.get(keys[-1])
    if current is None:
        node[keys[-1]] = new_items
    elif isinstance(current, list):
        current.extend(new_items)
    else:
        raise FrontmatterError(f"'{key_path}' exists but is not a list")


def append_text(body: str, text: str, heading: str | None = None) -> str:
    """Append markdown to the end of the body, or to the end of the section under `heading`.

    A missing heading is created at the end as a level-2 heading (or the level given, e.g. '### Log').
    """
    text = text.replace("\r\n", "\n").strip("\n")
    if not text:
        return body
    if heading is None:
        return _join(body, text)

    match = _HEADING.match(heading.strip())
    level = len(match.group(1)) if match else None
    title = match.group(2) if match else heading.strip()
    lines = body.split("\n")

    start = None
    for i, line in enumerate(lines):
        found = _HEADING.match(line)
        if found and found.group(2).casefold() == title.casefold() and level in (None, len(found.group(1))):
            start, level = i, len(found.group(1))
            break
    if start is None:
        return _join(body, f"{'#' * (level or 2)} {title}\n\n{text}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        found = _HEADING.match(lines[i])
        if found and len(found.group(1)) <= level:
            end = i
            break
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1

    new_lines = text.split("\n")
    out = lines[:insert_at]
    if insert_at == start + 1 or _needs_gap(lines[insert_at - 1], new_lines[0]):
        out.append("")
    out.extend(new_lines)
    rest = lines[insert_at:]
    if rest and rest[0].strip():  # the next heading followed directly
        out.append("")
    out.extend(rest)
    result = "\n".join(out)
    return result if result.endswith("\n") else result + "\n"


def _join(body: str, text: str) -> str:
    stripped = body.rstrip("\n")
    if not stripped.strip():
        return f"\n{text}\n"
    last_line = stripped.split("\n")[-1]
    separator = "\n\n" if _needs_gap(last_line, text.split("\n")[0]) else "\n"
    return f"{stripped}{separator}{text}\n"


def _needs_gap(previous: str, following: str) -> bool:
    """Blank line between blocks, except when continuing a list or a table."""
    if _LIST_ITEM.match(previous) and _LIST_ITEM.match(following):
        return False
    if previous.lstrip().startswith("|") and following.lstrip().startswith("|"):
        return False
    return True

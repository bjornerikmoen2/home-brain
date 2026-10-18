"""Text from documents in the vault (PDFs such as insurance policies), for search and reading.

Claude can't put a file into the vault: the user copies it in over the network share, and
home-brain extracts its text page by page. Scanned PDFs have no text layer and stay unread.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pypdf import PdfReader

log = logging.getLogger(__name__)

SUFFIXES = frozenset({".pdf"})
MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


@dataclass(frozen=True)
class Extracted:
    page_count: int
    pages: list[str] = field(default_factory=list)
    """Text per page, from page 1; may be shorter than `page_count` (see `error`)."""
    error: str | None = None
    """Why some or all text is missing; safe to show the model."""


def is_document(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SUFFIXES


def extract(path: Path) -> Extracted:
    """The text of each page. Never raises for a bad file; the problem is in `error` instead."""
    if path.stat().st_size > MAX_BYTES:
        return Extracted(0, error=f"the file is larger than {MAX_BYTES // 2**20} MB, so it was not read")
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            return Extracted(0, error="the PDF is password-protected")
        count = len(reader.pages)
    except Exception as exc:  # pypdf raises many kinds of errors on damaged files
        log.warning("cannot read %s: %s", path.name, exc)
        return Extracted(0, error="the PDF could not be read (damaged or not a PDF)")

    pages: list[str] = []
    failed: list[int] = []
    for number, page in enumerate(reader.pages[:MAX_PAGES], start=1):
        try:
            pages.append(_tidy(page.extract_text() or ""))
        except Exception as exc:
            log.warning("cannot read page %d of %s: %s", number, path.name, exc)
            pages.append("")
            failed.append(number)
    problems = []
    if failed:
        problems.append(f"the text of page {', '.join(map(str, failed))} could not be read")
    if count > MAX_PAGES:
        problems.append(f"only the first {MAX_PAGES} of {count} pages were read")
    if count and not any(p.strip() for p in pages) and not failed:
        problems.append("the PDF has no text, it is probably a scan")
    return Extracted(count, pages, "; ".join(problems) or None)


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return _BLANK_LINES.sub("\n\n", _TRAILING_SPACE.sub("\n", text)).strip()

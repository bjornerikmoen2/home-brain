from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import POLICY_PDF, make_pdf
from pypdf import PdfReader, PdfWriter

from home_brain import documents
from home_brain.documents import extract, is_document


def write(tmp_path: Path, data: bytes, name: str = "doc.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def encrypted(data: bytes, user_password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_text_per_page(tmp_path: Path) -> None:
    result = extract(write(tmp_path, POLICY_PDF))
    assert result.page_count == 3
    assert result.pages[1] == "Cover: building and contents\nDeductible: 8 000 kr"
    assert result.error is None


def test_norwegian_letters(tmp_path: Path) -> None:
    assert extract(write(tmp_path, make_pdf("Egenandel på glass: 3 000 kr, gjelder også Ærø"))).pages == [
        "Egenandel på glass: 3 000 kr, gjelder også Ærø"
    ]


def test_scan_without_text(tmp_path: Path) -> None:
    result = extract(write(tmp_path, make_pdf("", "")))
    assert (result.page_count, result.error) == (2, "the PDF has no text, it is probably a scan")


def test_not_a_pdf(tmp_path: Path) -> None:
    result = extract(write(tmp_path, b"<html>not a pdf</html>"))
    assert result.pages == []
    assert result.error == "the PDF could not be read (damaged or not a PDF)"


def test_password_protected(tmp_path: Path) -> None:
    assert extract(write(tmp_path, encrypted(POLICY_PDF, "secret"))).error == "the PDF is password-protected"


def test_encrypted_without_a_password_to_open_it(tmp_path: Path) -> None:
    # Banks and insurers often lock editing with an owner password only; it still opens.
    assert extract(write(tmp_path, encrypted(POLICY_PDF, ""))).pages[0].startswith("Home insurance policy 2026")


def test_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents, "MAX_PAGES", 2)
    result = extract(write(tmp_path, POLICY_PDF))
    assert (result.page_count, len(result.pages)) == (3, 2)
    assert result.error == "only the first 2 of 3 pages were read"

    monkeypatch.setattr(documents, "MAX_BYTES", 100)
    too_big = extract(write(tmp_path, POLICY_PDF))
    assert (too_big.pages, too_big.error) == ([], f"the file is larger than {100 // 2**20} MB, so it was not read")


def test_is_document() -> None:
    assert is_document("insurance/Policy.PDF")
    assert not is_document("insurance/policy.md")
    assert not is_document("pdf")

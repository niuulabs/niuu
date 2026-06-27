from __future__ import annotations

from ravn.momentum.models import SourceSpan
from ravn.momentum.source import SourceDocument


def test_source_document_verifies_valid_line_span() -> None:
    doc = SourceDocument("alpha\nbeta insight\ngamma")

    result = doc.verify(SourceSpan(excerpt="beta insight", line_start=2, line_end=2))

    assert result.status == "verified"
    assert result.line_start == 2
    assert result.line_end == 2


def test_source_document_marks_invalid_line_span_unverified() -> None:
    doc = SourceDocument("alpha\nbeta insight")

    result = doc.verify(SourceSpan(excerpt="beta insight", line_start=9, line_end=9))

    assert result.status == "unverified"
    assert "outside" in result.reason


def test_source_document_verifies_excerpt_only_match() -> None:
    doc = SourceDocument("alpha\nbeta insight continues\non the next line")

    result = doc.verify(SourceSpan(excerpt="beta insight continues on the next line"))

    assert result.status == "verified"
    assert result.line_start == 2
    assert result.line_end == 3


def test_source_document_marks_ungrounded_excerpt_unverified() -> None:
    doc = SourceDocument("alpha\nbeta")

    result = doc.verify(SourceSpan(excerpt="not in the document"))

    assert result.status == "unverified"
    assert "not found" in result.reason


def test_source_document_marks_blank_excerpt_unverified() -> None:
    doc = SourceDocument("alpha\nbeta")

    result = doc.verify(SourceSpan(excerpt=" ", line_start=1, line_end=1))

    assert result.status == "unverified"
    assert "blank" in result.reason

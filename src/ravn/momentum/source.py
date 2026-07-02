"""Source document indexing and provenance checks."""

from __future__ import annotations

from dataclasses import dataclass

from ravn.momentum.models import SourceSpan


@dataclass(frozen=True)
class SourceVerification:
    status: str
    reason: str
    excerpt: str
    line_start: int | None
    line_end: int | None


class SourceDocument:
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.splitlines()

    def resolve(self, start: int | None, end: int | None) -> str | None:
        if start is None or end is None or start < 1 or end < start or end > len(self.lines):
            return None
        return "\n".join(self.lines[start - 1 : end])

    def verify(self, span: SourceSpan) -> SourceVerification:
        excerpt = span.excerpt.strip()
        if not excerpt:
            return SourceVerification(
                status="unverified",
                reason="excerpt is blank",
                excerpt=excerpt,
                line_start=span.line_start,
                line_end=span.line_end,
            )
        if span.line_start is not None or span.line_end is not None:
            cited = self.resolve(span.line_start, span.line_end)
            if cited is None:
                return SourceVerification(
                    status="unverified",
                    reason="line range is outside the source document",
                    excerpt=excerpt,
                    line_start=span.line_start,
                    line_end=span.line_end,
                )
            if _contains(cited, excerpt):
                return SourceVerification(
                    status="verified",
                    reason="excerpt appears in cited line range",
                    excerpt=excerpt,
                    line_start=span.line_start,
                    line_end=span.line_end,
                )
            return SourceVerification(
                status="unverified",
                reason="excerpt does not appear in cited line range",
                excerpt=excerpt,
                line_start=span.line_start,
                line_end=span.line_end,
            )

        located = self._find_excerpt(excerpt)
        if located is None:
            return SourceVerification(
                status="unverified",
                reason="excerpt was not found in the source document",
                excerpt=excerpt,
                line_start=None,
                line_end=None,
            )
        return SourceVerification(
            status="verified",
            reason="excerpt found in source document",
            excerpt=excerpt,
            line_start=located[0],
            line_end=located[1],
        )

    def _find_excerpt(self, excerpt: str) -> tuple[int, int] | None:
        full = "\n".join(self.lines)
        index = full.find(excerpt)
        if index >= 0:
            start = full[:index].count("\n") + 1
            return start, start + excerpt.count("\n")
        for size in range(1, len(self.lines) + 1):
            for start in range(1, len(self.lines) - size + 2):
                end = start + size - 1
                if _contains("\n".join(self.lines[start - 1 : end]), excerpt):
                    return start, end
        return None


def _contains(haystack: str, needle: str) -> bool:
    return _compact(needle) in _compact(haystack)


def _compact(text: str) -> str:
    return " ".join(text.split()).casefold()

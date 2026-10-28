"""Lessons recorded by reflection, read back for matching, curation, and injection.

A *lesson* is a learning candidate (``learning-candidates/``) or a promoted
learning (``learnings/``) as a Mímir page. This module parses the pieces of a
page that matter for deciding whether two lessons say the same thing, and keeps
an embedding index over them so a paraphrased rediscovery is recognised by
meaning instead of by the words it happens to reuse.

It is a leaf of the reflection package: page writers import it, never the
reverse.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from niuu.ports.mimir import MimirPort
    from ravn.ports.embedding import EmbeddingPort

CANDIDATE_CATEGORY = "learning-candidates"
LEARNING_CATEGORY = "learnings"
LESSON_CATEGORIES = (CANDIDATE_CATEGORY, LEARNING_CATEGORY)

#: Statuses of a lesson that still speaks. Everything else (merged, superseded,
#: pruned) is kept for audit but never matched, curated, or injected.
ACTIVE_STATUSES = frozenset({"candidate", "promoted"})

_TITLE_PREFIXES = ("Learning candidate:", "Learning:")


@dataclass(frozen=True)
class Lesson:
    """One recorded lesson, parsed from its Mímir page."""

    path: str
    category: str
    title: str
    claim: str
    status: str
    evidence_count: int
    session_ids: frozenset[str]
    verified: bool
    last_observed: datetime
    procedure: str
    content: str

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_learning(self) -> bool:
        return self.category == LEARNING_CATEGORY

    @property
    def text(self) -> str:
        """The text whose meaning identifies the lesson."""
        return f"{self.title}. {self.claim}".strip()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def strength(self) -> tuple[bool, bool, int, datetime]:
        """Evidence strength: verified, then promoted, then observations, then recency."""
        return (self.verified, self.is_learning, self.evidence_count, self.last_observed)


def parse_lesson(path: str, content: str, *, updated_at: datetime) -> Lesson:
    """Parse a candidate or learning page into a :class:`Lesson`."""
    category = path.split("/", 1)[0]
    front = frontmatter_values(content)
    default_status = "promoted" if category == LEARNING_CATEGORY else "candidate"
    session_ids = frozenset(
        match.group(1).strip()
        for match in re.finditer(r"^\s{4}session_id:\s*(.+?)\s*$", content, flags=re.MULTILINE)
        if match.group(1).strip()
    )
    count_text = front.get("evidence_count", "")
    evidence_count = int(count_text) if count_text.isdigit() else max(1, len(session_ids))
    return Lesson(
        path=path,
        category=category,
        title=_lesson_title(front.get("title", ""), path),
        claim=_lesson_claim(content),
        status=front.get("status", default_status) or default_status,
        evidence_count=evidence_count,
        session_ids=session_ids,
        verified=front.get("verified", "").casefold() == "true",
        last_observed=_last_observed(content, front, updated_at),
        procedure=body_section(content, "Procedure"),
        content=content,
    )


def frontmatter_values(content: str) -> dict[str, str]:
    """Return the top-level ``key: value`` scalars of a page's YAML frontmatter."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    values: dict[str, str] = {}
    for line in content[3:end].splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
        if match is None:
            continue
        values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def body_section(content: str, name: str) -> str:
    """Return the text under ``## name`` up to the next ``## `` heading."""
    match = re.search(
        rf"^## {re.escape(name)}\s*$\n+(.*?)(?=\n## |\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _lesson_title(raw: str, path: str) -> str:
    title = raw.strip()
    for prefix in _TITLE_PREFIXES:
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
            break
    return title or path.rsplit("/", 1)[-1].removesuffix(".md").replace("-", " ")


def _lesson_claim(content: str) -> str:
    for name in ("What was learned", "Summary", "Procedure"):
        text = body_section(content, name)
        if text:
            return " ".join(text.split())
    return ""


def _last_observed(content: str, front: dict[str, str], updated_at: datetime) -> datetime:
    stamps = [
        match.group(1).strip()
        for match in re.finditer(r"^\s{4}date:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    ]
    if front.get("last_confirmed"):
        stamps.append(front["last_confirmed"])
    parsed = [value for value in (_parse_stamp(stamp) for stamp in stamps) if value is not None]
    if not parsed:
        return updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
    return max(parsed)


def _parse_stamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().strip('"').replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors; 0.0 when either is the zero vector."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0.0:
        return 0.0
    return dot / norm


class LessonIndex:
    """Recorded lessons plus an embedding cache, for meaning-based comparison.

    Pages are re-read only when Mímir reports them changed, and a lesson is
    re-embedded only when its title or claim changed, so consulting the index on
    every reflection costs one listing plus whatever actually moved.
    """

    def __init__(self, embedding: EmbeddingPort, *, batch_size: int) -> None:
        if batch_size < 1:
            raise ValueError("LessonIndex batch_size must be at least 1")
        self._embedding = embedding
        self._batch_size = batch_size
        self._pages: dict[str, tuple[datetime, Lesson]] = {}
        self._vectors: dict[str, list[float]] = {}

    async def load(
        self,
        mimir: MimirPort,
        categories: tuple[str, ...] = LESSON_CATEGORIES,
    ) -> list[Lesson]:
        """Return every lesson page in *categories*, reading only changed pages."""
        lessons: list[Lesson] = []
        listed: set[str] = set()
        for category in categories:
            for meta in await mimir.list_pages(category=category):
                listed.add(meta.path)
                cached = self._pages.get(meta.path)
                if cached is not None and cached[0] == meta.updated_at:
                    lessons.append(cached[1])
                    continue
                content = await mimir.read_page(meta.path)
                lesson = parse_lesson(meta.path, content, updated_at=meta.updated_at)
                self._pages[meta.path] = (meta.updated_at, lesson)
                lessons.append(lesson)
        for path in [path for path in self._pages if path.split("/", 1)[0] in categories]:
            if path not in listed:
                del self._pages[path]
        return lessons

    async def rank(self, text: str, lessons: list[Lesson]) -> list[tuple[Lesson, float]]:
        """Return *lessons* paired with their similarity to *text*, most similar first."""
        if not lessons:
            return []
        query = await self._embedding.embed(text)
        vectors = await self._vectors_for(lessons)
        scored = [(lesson, cosine(query, vectors[lesson.fingerprint])) for lesson in lessons]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    async def max_similarity(self, text: str, others: list[str]) -> float:
        """Highest cosine similarity between *text* and any of *others*."""
        return max(await self.similarities(text, others), default=0.0)

    async def similarities(self, text: str, others: list[str]) -> list[float]:
        """Cosine similarity of *text* to each of *others*, in order."""
        if not others:
            return []
        vectors = await self.embed_texts([text, *others])
        return [cosine(vectors[0], vector) for vector in vectors[1:]]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed *texts* in batches, in order, with the index's embedding port."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            chunk = texts[start : start + self._batch_size]
            embedded = await self._embedding.embed_batch(chunk)
            if len(embedded) != len(chunk):
                raise RuntimeError(
                    f"embedding adapter returned {len(embedded)} vectors for "
                    f"{len(chunk)} texts; refusing to compare on a misaligned batch"
                )
            vectors.extend(embedded)
        return vectors

    async def _vectors_for(self, lessons: list[Lesson]) -> dict[str, list[float]]:
        missing: dict[str, str] = {}
        for lesson in lessons:
            if lesson.fingerprint not in self._vectors:
                missing[lesson.fingerprint] = lesson.text
        if missing:
            fingerprints = list(missing)
            for start in range(0, len(fingerprints), self._batch_size):
                chunk = fingerprints[start : start + self._batch_size]
                vectors = await self._embedding.embed_batch([missing[key] for key in chunk])
                if len(vectors) != len(chunk):
                    raise RuntimeError(
                        f"embedding adapter returned {len(vectors)} vectors for "
                        f"{len(chunk)} lessons; refusing to compare lessons on a "
                        "misaligned index"
                    )
                self._vectors.update(zip(chunk, vectors, strict=True))
        return {lesson.fingerprint: self._vectors[lesson.fingerprint] for lesson in lessons}

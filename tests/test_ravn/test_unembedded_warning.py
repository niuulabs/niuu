"""The silent failure gets a voice.

Hybrid search degrades to keyword-only for any document without a vector, and does so perfectly
quietly — you still get results, they are simply the lexical ones. 96 of 137 documents accumulated
that way over seven weeks and nothing ever said so.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ravn.adapters.memory.sqlite import SqliteMemoryAdapter


class _Embedding:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    @property
    def dimension(self) -> int:
        return 2


async def _seed_unembedded(path: Path) -> None:
    """Index a document with no vector — the shape backfill exists to repair."""
    mem = SqliteMemoryAdapter(path=str(path))  # no embedding port: nothing gets a vector
    await mem.initialize()
    await mem._search.index("doc-1", "a memory worth keeping", {})


class TestUnembeddedWarning:
    async def test_it_says_how_many_are_invisible(self, tmp_path: Path, caplog) -> None:
        db = tmp_path / "memory.db"
        await _seed_unembedded(db)

        with caplog.at_level(logging.WARNING):
            mem = SqliteMemoryAdapter(path=str(db), embedding_port=_Embedding())
            await mem.initialize()

        assert any("no embedding" in r.getMessage() for r in caplog.records)

    async def test_a_healthy_corpus_says_nothing(self, tmp_path: Path, caplog) -> None:
        db = tmp_path / "clean.db"

        with caplog.at_level(logging.WARNING):
            mem = SqliteMemoryAdapter(path=str(db), embedding_port=_Embedding())
            await mem.initialize()

        assert not any("no embedding" in (r.getMessage()) for r in caplog.records)

    async def test_no_embedding_configured_is_not_a_warning(self, tmp_path: Path, caplog) -> None:
        """Lexical-only is a choice when nothing is configured — only a surprise when it is."""
        db = tmp_path / "lexical.db"
        await _seed_unembedded(db)

        with caplog.at_level(logging.WARNING):
            mem = SqliteMemoryAdapter(path=str(db))
            await mem.initialize()

        assert not any("no embedding" in (r.getMessage()) for r in caplog.records)

"""A test-only ``MimirPort`` over a directory of markdown pages.

Ting's research and spec tests write the pages a run produced under ``<root>/wiki/`` and
read them back through the campaign's memory well. Directory wells no longer exist in the
product, so those tests name this class as the well's ``adapter`` (with ``root`` as its
kwarg). Each construction reads the directory, so every resolution of the well sees what is
on disk at that moment. It is a test double: nothing here is shipped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from niuu.domain.mimir import MimirPageMeta
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter


class DirectoryMimirAdapter(InMemoryMimirAdapter):
    """The pages under ``<root>/wiki`` as an in-memory store; writes go back to disk."""

    def __init__(self, root: str) -> None:
        super().__init__()
        self._wiki = Path(root) / "wiki"
        if not self._wiki.is_dir():
            return
        for file in sorted(path for path in self._wiki.rglob("*") if path.is_file()):
            modified = datetime.fromtimestamp(file.stat().st_mtime, tz=UTC)
            relative = file.relative_to(self._wiki).as_posix()
            self.pages[relative] = (file.read_text(encoding="utf-8"), modified)

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        await super().upsert_page(path, content, mimir, meta)
        target = self._wiki / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

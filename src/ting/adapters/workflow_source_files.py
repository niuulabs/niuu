"""Bounded project-local source reader for workflow authoring."""

from pathlib import Path

from ting.ports.workflow_authoring import WorkflowSourceReader


class FilesystemWorkflowSourceReader(WorkflowSourceReader):
    def __init__(self, root: str, *, max_source_bytes: int = 2_097_152) -> None:
        self._root = Path(root).resolve(strict=True)
        self._max_source_bytes = max_source_bytes
        if not self._root.is_dir() or max_source_bytes <= 0:
            raise ValueError("Workflow source root must be a directory and size limit positive")

    def read(self, path: str) -> str:
        source = (self._root / path).resolve(strict=True)
        if not source.is_relative_to(self._root):
            raise ValueError(f"Workflow source escapes project directory: {path}")
        with source.open("rb") as stream:
            content = stream.read(self._max_source_bytes + 1)
        if len(content) > self._max_source_bytes:
            raise ValueError(f"Workflow source exceeds size limit: {path}")
        return content.decode("utf-8")

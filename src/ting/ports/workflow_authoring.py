"""Source access for offline, reproducible workflow builds."""

from typing import Protocol


class WorkflowSourceReader(Protocol):
    def read(self, path: str) -> str:
        """Read one normalized project-relative YAML source, or raise."""

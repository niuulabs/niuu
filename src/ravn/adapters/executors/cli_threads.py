"""Keeps a persona's native CLI thread across the steps of one workflow run.

A workflow returns to the same node when a review asks for changes. Without its
thread the persona re-reads the repository, the rules and its own tools before
it can act on the feedback, and that orientation is most of a revision's cost.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol


class WorkflowStep(Protocol):
    workflow_node_id: str
    persona: str | None


def workflow_thread_key(task: WorkflowStep | None) -> str:
    """The thread a task continues, or "" when it is not a workflow step.

    Scoped to the node, not just the persona: a persona's thread holds what it
    did for one piece of the run, and a different node is a different job.

    The root correlation is deliberately not part of the key. Every gate decision
    starts a new one, so a revision a person asked for would lose its thread. The
    store is per session instead, which is one workflow run.
    """
    if task is None or not task.workflow_node_id:
        return ""
    return f"{task.persona or ''}/{task.workflow_node_id}"


class CliThreadStore:
    """Thread ids by workflow step, kept beside the persona's other durable state."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._claimed: set[str] = set()

    def claim(self, key: str) -> str | None:
        """Take *key* for one step and return the thread to resume ("" for none yet).

        Returns None while another step holds it: a thread has one writer, so a
        concurrent step of the same node runs on a thread of its own.
        """
        if key in self._claimed:
            return None
        self._claimed.add(key)
        return self._threads().get(key, "")

    def release(self, key: str, thread_id: str) -> None:
        """Give *key* back, remembering the thread the step ran on."""
        self._claimed.discard(key)
        if not thread_id:
            return
        threads = self._threads()
        threads[key] = thread_id
        self._path.parent.mkdir(parents=True, exist_ok=True)
        pending = self._path.with_suffix(".tmp")
        pending.write_text(json.dumps(threads, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(pending, self._path)

    def _threads(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

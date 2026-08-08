"""Checkpoint port — interface for persisting and loading task checkpoints."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.checkpoint import Checkpoint


class CheckpointPort(ABC):
    """Persist and load task checkpoints.

    Two storage categories share the same port:

    * **Crash-recovery checkpoints** (NIU-504) — one per task, overwritten after
      every tool call.  The task_id is the primary key.  Loaded via
      :meth:`load`.

    * **Named snapshots** (NIU-537) — multiple per task, each with a unique
      ``checkpoint_id`` of the form ``ckpt_{task_id}_{seq}``.  Managed via
      :meth:`save_snapshot`, :meth:`list_for_task`, :meth:`load_snapshot`, and
      :meth:`delete_snapshot`.

    Implementations must be safe to call concurrently from async code.
    All methods are async so adapters can use non-blocking I/O.
    """

    # ------------------------------------------------------------------
    # NIU-504: crash-recovery checkpoint (one per task, keyed by task_id)
    # ------------------------------------------------------------------

    @abstractmethod
    async def save(self, checkpoint: Checkpoint) -> None:
        """Persist *checkpoint*, overwriting any previous checkpoint for the same task_id."""

    @abstractmethod
    async def load(self, task_id: str) -> Checkpoint | None:
        """Return the crash-recovery checkpoint for *task_id*, or None."""

    @abstractmethod
    async def delete(self, task_id: str) -> None:
        """Delete the crash-recovery checkpoint for *task_id*.  No-op if absent."""

    @abstractmethod
    async def list_task_ids(self) -> list[str]:
        """Return the task IDs of all stored crash-recovery checkpoints, newest first."""

    # ------------------------------------------------------------------
    # NIU-537: named snapshots (multiple per task, keyed by checkpoint_id)
    # ------------------------------------------------------------------

    @abstractmethod
    async def save_snapshot(self, checkpoint: Checkpoint) -> str:
        """Persist a named snapshot.

        Assigns the next sequence number for the task, sets ``checkpoint.checkpoint_id``
        to ``ckpt_{task_id}_{seq}``, and persists the checkpoint.

        Returns the assigned ``checkpoint_id``.
        """

    @abstractmethod
    async def list_for_task(self, task_id: str) -> list[Checkpoint]:
        """Return all named snapshots for *task_id*, newest first."""

    @abstractmethod
    async def load_snapshot(self, checkpoint_id: str) -> Checkpoint | None:
        """Return the snapshot with the given *checkpoint_id*, or None."""

    @abstractmethod
    async def delete_snapshot(self, checkpoint_id: str) -> None:
        """Delete the snapshot with the given *checkpoint_id*.  No-op if absent."""

    async def close(self) -> None:
        """Release any resources the backend holds. Idempotent.

        The disk adapter holds nothing; the postgres one lazily opens a pool
        and, until this existed, had no way to give it back.
        """
        return None

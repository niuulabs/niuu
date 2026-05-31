"""Disk-based checkpoint adapter — Pi / local mode.

Crash-recovery checkpoints (NIU-504):
    One gzip-compressed JSON file per task_id:
        ``{dir}/{task_id}.json.gz``

Named snapshots (NIU-537):
    One gzip-compressed JSON file per snapshot, under a per-task subdirectory:
        ``{dir}/{task_id}/{checkpoint_id}.json.gz``
    An ``index.json`` inside the task directory records sequence → checkpoint_id
    mapping and enables fast listing without reading every snapshot file.

All files are written atomically (temp + rename) and set to mode 0600.
Old snapshots are pruned when ``max_snapshots_per_task`` is exceeded.
"""

from __future__ import annotations
from contextlib import suppress

import gzip
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ravn.domain.checkpoint import Checkpoint, InterruptReason
from ravn.ports.checkpoint import CheckpointPort

logger = logging.getLogger(__name__)

_DEFAULT_CHECKPOINT_DIR = Path.home() / ".ravn" / "checkpoints"
_DEFAULT_MAX_SNAPSHOTS = 20
_FILE_MODE = 0o600


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _to_dict(checkpoint: Checkpoint) -> dict:
    return {
        "task_id": checkpoint.task_id,
        "user_input": checkpoint.user_input,
        "messages": checkpoint.messages,
        "todos": checkpoint.todos,
        "iteration_budget_consumed": checkpoint.iteration_budget_consumed,
        "iteration_budget_total": checkpoint.iteration_budget_total,
        "last_tool_call": checkpoint.last_tool_call,
        "last_tool_result": checkpoint.last_tool_result,
        "partial_response": checkpoint.partial_response,
        "interrupted_by": checkpoint.interrupted_by,
        "created_at": checkpoint.created_at.isoformat(),
        # NIU-537 snapshot fields
        "checkpoint_id": checkpoint.checkpoint_id,
        "seq": checkpoint.seq,
        "label": checkpoint.label,
        "tags": checkpoint.tags,
        "memory_context": checkpoint.memory_context,
    }


def _from_dict(data: dict) -> Checkpoint:
    interrupted_by_raw = data.get("interrupted_by")
    interrupted_by = InterruptReason(interrupted_by_raw) if interrupted_by_raw else None
    created_at_raw = data.get("created_at", "")
    created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.now(UTC)
    return Checkpoint(
        task_id=data["task_id"],
        user_input=data.get("user_input", ""),
        messages=data.get("messages", []),
        todos=data.get("todos", []),
        iteration_budget_consumed=data.get("iteration_budget_consumed", 0),
        iteration_budget_total=data.get("iteration_budget_total", 0),
        last_tool_call=data.get("last_tool_call"),
        last_tool_result=data.get("last_tool_result"),
        partial_response=data.get("partial_response", ""),
        interrupted_by=interrupted_by,
        created_at=created_at,
        # NIU-537 snapshot fields
        checkpoint_id=data.get("checkpoint_id", ""),
        seq=data.get("seq", 0),
        label=data.get("label", ""),
        tags=data.get("tags", []),
        memory_context=data.get("memory_context", ""),
    )


def _write_gz(path: Path, data: dict) -> None:
    """Atomically write *data* as gzip-compressed JSON to *path* at mode 0600."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(gzip.compress(json.dumps(data, indent=2).encode()))
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def _read_gz(path: Path) -> dict | None:
    """Read and decompress a gzip JSON file, returning None on any error."""
    try:
        return json.loads(gzip.decompress(path.read_bytes()))
    except Exception as exc:
        logger.warning("Failed to read checkpoint %s: %s", path, exc)
        return None


def _sanitise(task_id: str) -> str:
    """Sanitise *task_id* to a safe filename component."""
    return task_id.replace("/", "_").replace("..", "__")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class DiskCheckpointAdapter(CheckpointPort):
    """Stores checkpoints as gzip-compressed JSON files (0600) in a local directory.

    Parameters
    ----------
    checkpoint_dir:
        Root directory for checkpoint storage.  Defaults to
        ``~/.ravn/checkpoints/``.
    max_snapshots_per_task:
        Maximum named snapshots retained per task.  Oldest are pruned when
        this limit is exceeded.  Defaults to 20.
    """

    def __init__(
        self,
        checkpoint_dir: Path | str | None = None,
        max_snapshots_per_task: int = _DEFAULT_MAX_SNAPSHOTS,
    ) -> None:
        self._dir = Path(checkpoint_dir) if checkpoint_dir else _DEFAULT_CHECKPOINT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_snapshots = max_snapshots_per_task

    # ------------------------------------------------------------------
    # Internal path helpers
    # ------------------------------------------------------------------

    def _crash_path(self, task_id: str) -> Path:
        return self._dir / f"{_sanitise(task_id)}.json.gz"

    def _task_dir(self, task_id: str) -> Path:
        return self._dir / _sanitise(task_id)

    def _snapshot_path(self, task_id: str, checkpoint_id: str) -> Path:
        return self._task_dir(task_id) / f"{_sanitise(checkpoint_id)}.json.gz"

    def _index_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "index.json"

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _read_index(self, task_id: str) -> list[dict]:
        """Return the snapshot index for *task_id*, newest first.

        Each entry: ``{"checkpoint_id": str, "seq": int, "label": str,
        "created_at": str}``.
        """
        path = self._index_path(task_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except Exception:
            return []

    def _write_index(self, task_id: str, entries: list[dict]) -> None:
        path = self._index_path(task_id)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(entries, fh)
            os.replace(tmp, path)
        except Exception:
            with suppress(OSError):
                os.unlink(tmp)
            raise

    def _next_seq(self, task_id: str) -> int:
        entries = self._read_index(task_id)
        if not entries:
            return 1
        return max(e["seq"] for e in entries) + 1

    # ------------------------------------------------------------------
    # NIU-504: crash-recovery checkpoint
    # ------------------------------------------------------------------

    async def save(self, checkpoint: Checkpoint) -> None:
        path = self._crash_path(checkpoint.task_id)
        _write_gz(path, _to_dict(checkpoint))
        logger.debug("Crash checkpoint saved: %s → %s", checkpoint.task_id, path)

    async def load(self, task_id: str) -> Checkpoint | None:
        path = self._crash_path(task_id)
        if not path.exists():
            return None
        data = _read_gz(path)
        if data is None:
            return None
        return _from_dict(data)

    async def delete(self, task_id: str) -> None:
        path = self._crash_path(task_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to delete checkpoint %r: %s", task_id, exc)

    async def list_task_ids(self) -> list[str]:
        files = sorted(self._dir.glob("*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [f.stem.replace(".json", "") for f in files]

    # ------------------------------------------------------------------
    # NIU-537: named snapshots
    # ------------------------------------------------------------------

    async def save_snapshot(self, checkpoint: Checkpoint) -> str:
        task_dir = self._task_dir(checkpoint.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)

        seq = self._next_seq(checkpoint.task_id)
        checkpoint_id = Checkpoint.make_snapshot_id(checkpoint.task_id, seq)
        checkpoint.checkpoint_id = checkpoint_id
        checkpoint.seq = seq

        path = self._snapshot_path(checkpoint.task_id, checkpoint_id)
        _write_gz(path, _to_dict(checkpoint))

        # Update index (prepend newest entry)
        entries = self._read_index(checkpoint.task_id)
        entries.insert(
            0,
            {
                "checkpoint_id": checkpoint_id,
                "seq": seq,
                "label": checkpoint.label,
                "created_at": checkpoint.created_at.isoformat(),
            },
        )
        self._write_index(checkpoint.task_id, entries)

        # Prune oldest snapshots if limit exceeded
        await self._prune_snapshots(checkpoint.task_id, entries)

        logger.debug("Snapshot saved: %s → %s", checkpoint_id, path)
        return checkpoint_id

    async def list_for_task(self, task_id: str) -> list[Checkpoint]:
        entries = self._read_index(task_id)
        results: list[Checkpoint] = []
        for entry in entries:
            cid = entry["checkpoint_id"]
            path = self._snapshot_path(task_id, cid)
            if not path.exists():
                continue
            data = _read_gz(path)
            if data is not None:
                results.append(_from_dict(data))
        return results

    async def load_snapshot(self, checkpoint_id: str) -> Checkpoint | None:
        # checkpoint_id is ckpt_{task_id}_{seq} — extract task_id
        task_id = _task_id_from_checkpoint_id(checkpoint_id)
        if task_id is None:
            return None
        path = self._snapshot_path(task_id, checkpoint_id)
        if not path.exists():
            return None
        data = _read_gz(path)
        if data is None:
            return None
        return _from_dict(data)

    async def delete_snapshot(self, checkpoint_id: str) -> None:
        task_id = _task_id_from_checkpoint_id(checkpoint_id)
        if task_id is None:
            return
        path = self._snapshot_path(task_id, checkpoint_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to delete snapshot %r: %s", checkpoint_id, exc)
            return

        # Remove from index
        entries = self._read_index(task_id)
        entries = [e for e in entries if e["checkpoint_id"] != checkpoint_id]
        try:
            self._write_index(task_id, entries)
        except Exception as exc:
            logger.warning("Failed to update index after deleting %r: %s", checkpoint_id, exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _prune_snapshots(self, task_id: str, entries: list[dict]) -> None:
        """Delete oldest snapshots if ``entries`` exceeds ``_max_snapshots``."""
        if len(entries) <= self._max_snapshots:
            return
        to_remove = entries[self._max_snapshots :]
        surviving = entries[: self._max_snapshots]
        for entry in to_remove:
            cid = entry["checkpoint_id"]
            path = self._snapshot_path(task_id, cid)
            try:
                path.unlink(missing_ok=True)
                logger.debug("Pruned old snapshot: %s", cid)
            except OSError as exc:
                logger.warning("Failed to prune snapshot %r: %s", cid, exc)
        self._write_index(task_id, surviving)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_id_from_checkpoint_id(checkpoint_id: str) -> str | None:
    """Extract task_id from ``ckpt_{task_id}_{seq}``."""
    if not checkpoint_id.startswith("ckpt_"):
        return None
    # Find the last underscore — everything between "ckpt_" and it is the task_id
    tail = checkpoint_id[len("ckpt_") :]
    last_underscore = tail.rfind("_")
    if last_underscore < 0:
        return None
    return tail[:last_underscore]

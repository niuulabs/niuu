"""Codex session provider — scans ~/.codex/sessions for rollout files.

Codex stores one JSONL rollout per session under
``~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>.jsonl``.
The first line is a ``session_meta`` record with the thread id and
working directory, so sessions can be discovered and later resumed via
the ``thread/resume`` app-server RPC.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from volundr.domain.models import ExternalSessionRecord
from volundr.domain.ports import ExternalSessionProvider

logger = logging.getLogger(__name__)

DEFAULT_SESSIONS_DIR = "~/.codex/sessions"
DEFAULT_LIVE_THRESHOLD_SECONDS = 120
DEFAULT_HEAD_LINES = 50
DEFAULT_MAX_SESSIONS = 200
TITLE_MAX_CHARS = 120


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_title(text: str) -> str:
    title = " ".join(text.split())
    if len(title) > TITLE_MAX_CHARS:
        return title[: TITLE_MAX_CHARS - 1] + "…"
    return title


class CodexSessionProvider(ExternalSessionProvider):
    """Discovers Codex sessions on the local host."""

    def __init__(
        self,
        *,
        sessions_dir: str = DEFAULT_SESSIONS_DIR,
        live_threshold_seconds: int = DEFAULT_LIVE_THRESHOLD_SECONDS,
        head_lines: int = DEFAULT_HEAD_LINES,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        **_extra: object,
    ):
        self._sessions_dir = Path(str(sessions_dir)).expanduser()
        self._live_threshold_seconds = int(live_threshold_seconds)
        self._head_lines = int(head_lines)
        self._max_sessions = int(max_sessions)

    @property
    def name(self) -> str:
        return "codex"

    @property
    def harness(self) -> str:
        return "codex"

    async def list_sessions(self) -> list[ExternalSessionRecord]:
        return await asyncio.to_thread(self._scan)

    async def get_session(self, external_id: str) -> ExternalSessionRecord | None:
        return await asyncio.to_thread(self._find_one, external_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan(self) -> list[ExternalSessionRecord]:
        candidates = self._rollout_files()
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        records: list[ExternalSessionRecord] = []
        for path in candidates[: self._max_sessions]:
            record = self._parse_rollout_file(path)
            if record is not None:
                records.append(record)
        return records

    def _find_one(self, external_id: str) -> ExternalSessionRecord | None:
        if not external_id:
            return None
        for path in self._rollout_files():
            if path.stem.endswith(external_id):
                record = self._parse_rollout_file(path)
                if record is not None and record.external_id == external_id:
                    return record
        return None

    def _rollout_files(self) -> list[Path]:
        if not self._sessions_dir.is_dir():
            return []
        return [path for path in self._sessions_dir.rglob("rollout-*.jsonl") if path.is_file()]

    def _parse_rollout_file(self, path: Path) -> ExternalSessionRecord | None:
        external_id = ""
        cwd = ""
        title = ""
        model = ""
        created_at: datetime | None = None

        try:
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= self._head_lines:
                        break
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue

                    record_type = record.get("type")
                    if record_type == "session_meta":
                        external_id = str(payload.get("id") or "")
                        cwd = str(payload.get("cwd") or "")
                        created_at = _parse_timestamp(payload.get("timestamp"))
                        continue
                    if record_type == "turn_context" and not model:
                        raw_model = payload.get("model")
                        if isinstance(raw_model, str):
                            model = raw_model
                        continue
                    if (
                        record_type == "event_msg"
                        and not title
                        and payload.get("type") == "user_message"
                        and isinstance(payload.get("message"), str)
                    ):
                        title = _clean_title(payload["message"])

                    if external_id and cwd and title and model:
                        break
        except OSError:
            logger.warning("Failed to read Codex rollout file %s", path, exc_info=True)
            return None

        if not external_id:
            logger.warning("Codex rollout file %s has no session_meta record", path)
            return None

        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        age_seconds = (datetime.now(UTC) - updated_at).total_seconds()

        return ExternalSessionRecord(
            provider=self.name,
            harness=self.harness,
            external_id=external_id,
            workspace_path=cwd,
            title=title,
            model=model,
            created_at=created_at,
            updated_at=updated_at,
            live=age_seconds <= self._live_threshold_seconds,
            workspace_exists=bool(cwd) and Path(cwd).is_dir(),
        )

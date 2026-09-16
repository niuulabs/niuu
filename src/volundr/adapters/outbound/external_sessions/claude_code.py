"""Claude Code session provider — scans ~/.claude/projects for sessions.

Claude Code stores one JSONL transcript per session under
``~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl``. Each record
carries the session id, working directory, and timestamps, so sessions
can be discovered and later resumed with ``claude --resume <id>``.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from volundr.domain.models import ExternalSessionRecord, SessionLogEntry
from volundr.domain.ports import ExternalSessionProvider

from .transcript_common import (
    DEFAULT_MAX_TRANSCRIPT_BYTES,
    NativeTranscriptBuilder,
    is_injected_text,
    read_native_jsonl,
)

logger = logging.getLogger(__name__)

DEFAULT_PROJECTS_DIR = "~/.claude/projects"
DEFAULT_LIVE_THRESHOLD_SECONDS = 120
DEFAULT_HEAD_LINES = 50
DEFAULT_MAX_SESSIONS = 200
TITLE_MAX_CHARS = 120
_TERMINAL_STOP_REASONS = frozenset({"end_turn", "stop_sequence", "max_tokens", "refusal"})


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _message_text(message: object) -> str:
    """Extract plain text from a Claude message payload."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return ""


def _clean_title(text: str) -> str:
    title = " ".join(text.split())
    if len(title) > TITLE_MAX_CHARS:
        return title[: TITLE_MAX_CHARS - 1] + "…"
    return title


def _is_sidechain(record: dict) -> bool:
    return bool(
        record.get("isSidechain")
        or record.get("is_sidechain")
        or record.get("parent_tool_use_id")
        or record.get("agentId")
        or record.get("agent_id")
    )


def _transcript_content(content: object, *, role: str, line: int) -> list[dict]:
    """Project only public conversation blocks, never native context or reasoning."""
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if role == "user" and is_injected_text(text):
                continue
            blocks.append({"type": "text", "text": text})
            continue
        if kind == "tool_use" and role == "assistant":
            tool_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
            if not isinstance(tool_id, str) or not tool_id or not isinstance(name, str) or not name:
                raise ValueError(f"Invalid Claude tool call on transcript line {line}")
            if not isinstance(arguments, dict):
                raise ValueError(f"Invalid Claude tool input on transcript line {line}")
            blocks.append({"type": "tool_use", "id": tool_id, "name": name, "input": arguments})
            continue
        if kind != "tool_result" or role != "user":
            continue
        tool_id = block.get("tool_use_id")
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError(f"Invalid Claude tool result on transcript line {line}")
        output = block.get("content", "")
        if isinstance(output, list):
            output = "\n".join(
                item["text"]
                for item in output
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            )
        if not isinstance(output, str):
            raise ValueError(f"Invalid Claude tool output on transcript line {line}")
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": output,
                "is_error": block.get("is_error") is True,
            }
        )
    return blocks


def _transcript_messages(record: dict, *, line: int) -> list[dict]:
    kind = record.get("type")
    if kind not in {"user", "assistant"} or record.get("isMeta"):
        return []
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role", kind) != kind:
        return []
    content = _transcript_content(message.get("content"), role=kind, line=line)
    if not content:
        return []
    projected: dict = {"role": kind, "content": content}
    for key in ("id", "model", "stop_reason"):
        if isinstance(message.get(key), str):
            projected[key] = message[key]
    record_uuid = record.get("uuid")
    record_uuid = record_uuid if isinstance(record_uuid, str) and record_uuid else None
    if kind == "assistant":
        payload = {"type": kind, "message": projected}
        if record_uuid:
            payload["uuid"] = record_uuid
        return [payload]
    # Human input is a string on Forge's wire; list content denotes tool results.
    # Keep mixed native block order while grouping adjacent blocks of the same kind.
    groups: list[list[dict]] = []
    for block in content:
        if groups and groups[-1][-1]["type"] == block["type"]:
            groups[-1].append(block)
            continue
        groups.append([block])
    payloads: list[dict] = []
    human_index = 0
    for group in groups:
        if group[0]["type"] != "text":
            payloads.append({"type": kind, "message": {"role": kind, "content": group}})
            continue
        text = "\n".join(block["text"] for block in group)
        identity = record_uuid or f"line:{line}"
        stable_id = (
            record_uuid
            if record_uuid and human_index == 0
            else str(
                uuid5(
                    NAMESPACE_URL,
                    f"claude-code:{record.get('sessionId')}:{identity}:user:{human_index}",
                )
            )
        )
        payloads.append(
            {"type": kind, "uuid": stable_id, "message": {"role": kind, "content": text}}
        )
        human_index += 1
    return payloads


class ClaudeCodeSessionProvider(ExternalSessionProvider):
    """Discovers Claude Code sessions on the local host."""

    def __init__(
        self,
        *,
        projects_dir: str = DEFAULT_PROJECTS_DIR,
        live_threshold_seconds: int = DEFAULT_LIVE_THRESHOLD_SECONDS,
        head_lines: int = DEFAULT_HEAD_LINES,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_transcript_bytes: int = DEFAULT_MAX_TRANSCRIPT_BYTES,
        **_extra: object,
    ):
        self._projects_dir = Path(str(projects_dir)).expanduser()
        self._live_threshold_seconds = int(live_threshold_seconds)
        self._head_lines = int(head_lines)
        self._max_sessions = int(max_sessions)
        self._max_transcript_bytes = int(max_transcript_bytes)
        if self._max_transcript_bytes <= 0:
            raise ValueError("max_transcript_bytes must be positive")

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def harness(self) -> str:
        return "claude"

    async def list_sessions(self) -> list[ExternalSessionRecord]:
        return await asyncio.to_thread(self._scan)

    async def get_session(self, external_id: str) -> ExternalSessionRecord | None:
        return await asyncio.to_thread(self._find_one, external_id)

    async def read_transcript(self, external_id: str, session_id: UUID) -> list[SessionLogEntry]:
        """Read native history without starting Claude or modifying its session."""
        return await asyncio.to_thread(self._read_transcript, external_id, session_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_transcript(self, external_id: str, session_id: UUID) -> list[SessionLogEntry]:
        native_id = str(UUID(external_id))
        candidates = [path for path in self._session_files() if str(UUID(path.stem)) == native_id]
        if not candidates:
            raise FileNotFoundError(f"Claude transcript {native_id} was not found")
        if len(candidates) != 1:
            raise ValueError(f"Multiple Claude transcripts match {native_id}")
        source = read_native_jsonl(candidates[0], self._projects_dir, self._max_transcript_bytes)
        builder = NativeTranscriptBuilder(self.name, native_id, session_id, source)
        identified = False
        seen: dict[str, str] = {}
        for line, record in source.records:
            if _is_sidechain(record):
                continue
            record_id = record.get("sessionId")
            if record_id is not None:
                try:
                    matches = isinstance(record_id, str) and str(UUID(record_id)) == native_id
                except ValueError:
                    matches = False
                if not matches:
                    raise ValueError(
                        f"Foreign or invalid Claude session ID on transcript line {line}"
                    )
                identified = True
            payloads = _transcript_messages(record, line=line)
            if not payloads:
                continue
            if record_id is None:
                raise ValueError(f"Missing Claude session ID on transcript line {line}")
            record_uuid = record.get("uuid")
            record_uuid = record_uuid if isinstance(record_uuid, str) and record_uuid else None
            if record_uuid:
                fingerprint = json.dumps(payloads, sort_keys=True, ensure_ascii=False)
                previous = seen.get(record_uuid)
                if previous is not None:
                    if previous != fingerprint:
                        raise ValueError(
                            f"Conflicting Claude message UUID on transcript line {line}"
                        )
                    continue
                seen[record_uuid] = fingerprint
            for payload in payloads:
                builder.emit(
                    payload,
                    record.get("timestamp"),
                    line=line,
                    source_type=str(record["type"]),
                    native_id=record_uuid,
                )
                stop_reason = payload["message"].get("stop_reason")
                if payload["type"] != "assistant" or stop_reason not in _TERMINAL_STOP_REASONS:
                    continue
                builder.emit(
                    {"type": "result", "result": "", "stop_reason": stop_reason},
                    record.get("timestamp"),
                    line=line,
                    source_type="assistant.stop_reason",
                    native_id=record_uuid,
                )
        if not identified:
            raise ValueError("Claude transcript has no matching native session identity")
        return builder.finish()

    def _scan(self) -> list[ExternalSessionRecord]:
        candidates = self._session_files()
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        records: list[ExternalSessionRecord] = []
        for path in candidates[: self._max_sessions]:
            record = self._parse_session_file(path)
            if record is not None:
                records.append(record)
        return records

    def _find_one(self, external_id: str) -> ExternalSessionRecord | None:
        try:
            UUID(external_id)
        except ValueError:
            return None
        for path in self._session_files():
            if path.stem == external_id:
                return self._parse_session_file(path)
        return None

    def _session_files(self) -> list[Path]:
        if not self._projects_dir.is_dir():
            return []
        files: list[Path] = []
        for project_dir in self._projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for path in project_dir.glob("*.jsonl"):
                try:
                    UUID(path.stem)
                except ValueError:
                    continue
                files.append(path)
        return files

    def _parse_session_file(self, path: Path) -> ExternalSessionRecord | None:
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
                    if record.get("isSidechain"):
                        continue
                    if not cwd and isinstance(record.get("cwd"), str):
                        cwd = record["cwd"]
                    if created_at is None:
                        created_at = _parse_timestamp(record.get("timestamp"))
                    if not title and record.get("type") == "user":
                        title = _clean_title(_message_text(record.get("message")))
                    if not model and record.get("type") == "assistant":
                        message = record.get("message")
                        if isinstance(message, dict) and isinstance(message.get("model"), str):
                            model = message["model"]
                    if cwd and title and model and created_at is not None:
                        break
        except OSError:
            logger.warning("Failed to read Claude session file %s", path, exc_info=True)
            return None

        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        age_seconds = (datetime.now(UTC) - updated_at).total_seconds()

        return ExternalSessionRecord(
            provider=self.name,
            harness=self.harness,
            external_id=path.stem,
            workspace_path=cwd,
            title=title,
            model=model,
            created_at=created_at,
            updated_at=updated_at,
            live=age_seconds <= self._live_threshold_seconds,
            workspace_exists=bool(cwd) and Path(cwd).is_dir(),
        )

"""Project native Codex rollout records onto the shared replay wire contract."""

import json
from collections import Counter, defaultdict
from uuid import NAMESPACE_URL, UUID, uuid5

from volundr.domain.models import SessionLogEntry

from .transcript_common import NativeTranscript, NativeTranscriptBuilder, is_injected_text

_PRIVATE_FIELDS = frozenset(
    {
        "encrypted_content",
        "raw_content",
        "reasoning",
        "thinking",
        "signature",
        "internal_chat_message_metadata_passthrough",
    }
)
_TEXT_TYPES = frozenset({"text", "Text", "input_text", "output_text"})
_MESSAGE_PRIORITY = {"modern": 0, "event": 1, "response": 2}


def _public_value(value: object) -> object:
    if isinstance(value, dict):
        return {k: _public_value(v) for k, v in value.items() if k not in _PRIVATE_FIELDS}
    if isinstance(value, list):
        return [_public_value(v) for v in value]
    return value


def _text(content: object, *, human: bool = False) -> str:
    if isinstance(content, str):
        return "" if human and is_injected_text(content) else content
    if not isinstance(content, list):
        return ""
    values = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _TEXT_TYPES:
            continue
        value = block.get("text")
        if isinstance(value, str) and not (human and is_injected_text(value)):
            values.append(value)
    return "\n".join(values)


def _message(record: dict) -> tuple[str, str, str, str | None, str | None] | None:
    payload = record.get("payload", {})
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    item = payload
    family = ""
    role = ""
    if record.get("type") == "event_msg" and kind == "item_completed":
        item = payload.get("item", {})
        if not isinstance(item, dict):
            return None
        role = {"UserMessage": "user", "AgentMessage": "assistant"}.get(item.get("type"), "")
        family = "modern"
    elif record.get("type") == "event_msg" and kind in {"user_message", "agent_message"}:
        role = "user" if kind == "user_message" else "assistant"
        family = "event"
    elif record.get("type") == "response_item" and kind == "message":
        role = payload.get("role", "")
        family = "response"
    if role not in {"user", "assistant"} or item.get("phase") == "analysis":
        return None
    if item.get("channel") == "analysis" or item.get("isMeta"):
        return None
    if family == "modern" and role == "assistant" and item.get("delivery") == "async":
        # request_user_input_async materializes its question card as an
        # AgentMessage whose ID is the function call ID. Its content is control
        # context, not a public final answer. The actual call/result records
        # below retain that interaction without inventing assistant prose.
        return None
    content = item.get("message") if family == "event" else item.get("content")
    text = _text(content, human=role == "user")
    if not text:
        return None
    native_id = item.get("id")
    phase = item.get("phase") or item.get("channel")
    phase = phase if phase in ("commentary", "final_answer") else None
    return role, text, family, native_id if isinstance(native_id, str) else None, phase


def _records_with_turn(source: NativeTranscript, external_id: str) -> list[tuple[int, dict, str]]:
    turn_id = ""
    result = []
    for line, record in source.records:
        payload = record.get("payload")
        if isinstance(payload, dict):
            thread_id = payload.get("thread_id") or payload.get("threadId")
            if thread_id and thread_id != external_id:
                # Native worker events can be multiplexed into a parent log.
                # Their messages and terminal events cannot own the parent span.
                continue
            if record.get("type") == "turn_context" or payload.get("type") == "task_started":
                turn_id = str(payload.get("turn_id") or turn_id)
        result.append((line, record, turn_id))
    return result


def parse_codex_transcript(
    source: NativeTranscript, external_id: str, session_id: UUID
) -> list[SessionLogEntry]:
    identities = []
    for _, record in source.records:
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("Codex transcript has invalid session metadata")
        identities.extend(payload[key] for key in ("id", "session_id") if payload.get(key))
    if not identities or any(identity != external_id for identity in identities):
        raise ValueError("Codex transcript identity does not match the requested native session")

    builder = NativeTranscriptBuilder("codex", external_id, session_id, source)
    records = _records_with_turn(source, external_id)
    counts: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for _, record, turn in records:
        message = _message(record)
        if message:
            role, text, family, _, _ = message
            counts[(turn, role, text)][family] += 1
    occurrences: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    message_ids: dict[tuple[str, str], str] = {}
    calls: set[str] = set()
    outputs: set[str] = set()
    completed_turns: set[str] = set()
    last_assistant: dict[str, str] = {}

    def emit(frame: dict, line: int, record: dict, native_id: str | None = None) -> None:
        payload = record.get("payload", {})
        subtype = payload.get("type", "") if isinstance(payload, dict) else ""
        if subtype == "item_completed" and isinstance(payload.get("item"), dict):
            subtype = f"{subtype}.{payload['item'].get('type', 'unknown')}"
        builder.emit(
            frame,
            record.get("timestamp"),
            line=line,
            source_type=f"{record.get('type')}.{subtype}",
            native_id=native_id,
        )

    def tool_use(identifier: str, name: str, args: object, line: int, record: dict) -> None:
        if identifier in calls:
            return
        calls.add(identifier)
        emit(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": identifier,
                            "name": name,
                            "input": _public_value(args),
                        }
                    ],
                },
            },
            line,
            record,
            identifier,
        )

    def tool_result(identifier: str, output: object, failed: bool, line: int, record: dict) -> None:
        if identifier in outputs:
            return
        outputs.add(identifier)
        emit(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": identifier,
                            "content": _public_value(output),
                            "is_error": failed,
                        }
                    ],
                },
            },
            line,
            record,
            identifier,
        )

    for line, record, turn in records:
        message = _message(record)
        if message:
            role, text, family, native_id, phase = message
            key = (turn, role, text)
            occurrences[key][family] += 1
            mirrored = max(
                (
                    count
                    for other, count in counts[key].items()
                    if _MESSAGE_PRIORITY[other] < _MESSAGE_PRIORITY[family]
                ),
                default=0,
            )
            if occurrences[key][family] <= mirrored:
                continue
            if native_id:
                identity = (role, native_id)
                previous = message_ids.get(identity)
                if previous is not None and previous != text:
                    raise ValueError(f"Conflicting Codex message identity at line {line}")
                if previous is not None:
                    continue
                message_ids[identity] = text
            content: object = text
            if role == "assistant":
                identifier = native_id or str(
                    uuid5(NAMESPACE_URL, f"codex:{external_id}:{turn}:text-line:{line}")
                )
                block = {
                    "type": "text",
                    "id": identifier,
                    "text": text,
                    "complete": True,
                    "id_source": "native" if native_id else "synthetic",
                    "thread_id": external_id,
                }
                if phase:
                    block["phase"] = phase
                if turn:
                    block["turn_id"] = turn
                content = [block]
            emit(
                {"type": role, "role": role, "message": {"role": role, "content": content}},
                line,
                record,
                native_id,
            )
            if role == "assistant":
                last_assistant[turn] = text
            continue

        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if record.get("type") == "response_item" and kind in {
            "function_call",
            "custom_tool_call",
            "function_call_output",
            "custom_tool_call_output",
        }:
            identifier = str(payload.get("call_id") or payload.get("id") or f"native-line-{line}")
            if kind.endswith("_output"):
                tool_result(identifier, payload.get("output", ""), False, line, record)
                continue
            args = payload.get("arguments", payload.get("input", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"input": args}
            tool_use(identifier, str(payload.get("name") or "Tool"), args, line, record)
            continue

        if record.get("type") != "event_msg":
            continue
        if kind == "task_complete":
            completion_id = str(payload.get("turn_id") or turn or f"line:{line}")
            if completion_id in completed_turns:
                continue
            completed_turns.add(completion_id)
            final = payload.get("last_agent_message")
            text = final if isinstance(final, str) and final != last_assistant.get(turn) else ""
            if text:
                # Some rollouts retain the final public message only on task_complete.
                # A result string alone is a fallback for an otherwise empty turn;
                # it cannot repair a turn which already contains commentary.
                block = {
                    "type": "text",
                    "id": str(uuid5(NAMESPACE_URL, f"codex:{external_id}:{completion_id}:final")),
                    "id_source": "synthetic",
                    "thread_id": external_id,
                    "text": text,
                    "phase": "final_answer",
                    "complete": True,
                }
                if turn:
                    block["turn_id"] = turn
                emit(
                    {"type": "assistant", "message": {"role": "assistant", "content": [block]}},
                    line,
                    record,
                )
            emit(
                {"type": "result", "result": text, "stop_reason": "end_turn", "is_error": False},
                line,
                record,
                completion_id,
            )
            continue
        if kind == "turn_aborted":
            emit({"type": "result", "result": "", "stop_reason": "cancelled"}, line, record)
            continue
        if kind != "item_completed":
            continue
        item = payload.get("item", {})
        if not isinstance(item, dict):
            raise ValueError(f"Invalid Codex completed item at line {line}")
        item_type = item.get("type")
        identifier = str(item.get("id") or f"native-line-{line}")
        failed = item.get("status") in {"failed", "declined", "interrupted"}
        if item_type == "CommandExecution":
            tool_use(
                identifier,
                "Bash",
                {"command": item.get("command"), "cwd": item.get("cwd")},
                line,
                record,
            )
            output = item.get("aggregated_output")
            if not isinstance(output, str):
                output = str(item.get("stdout") or "") + str(item.get("stderr") or "")
            tool_result(identifier, output, failed or bool(item.get("exit_code")), line, record)
        elif item_type == "FileChange":
            tool_use(identifier, "Edit", {"changes": item.get("changes", {})}, line, record)
            output = str(item.get("stdout") or "") + str(item.get("stderr") or "")
            tool_result(identifier, output, failed, line, record)
        elif item_type in {"McpToolCall", "DynamicToolCall", "WebSearch"}:
            name = str(item.get("tool") or ("WebSearch" if item_type == "WebSearch" else item_type))
            args = item.get("arguments", {"query": item.get("query", "")})
            tool_use(identifier, name, args, line, record)
            output = item.get("result", item.get("output", item.get("action", "")))
            tool_result(identifier, output, failed, line, record)
    return builder.finish()

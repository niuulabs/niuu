"""Attachments index — every document and image a session handed over, in one pass.

LEX-1259: the app's Show Documents / Show Images lists only knew the turns it had
loaded, and a big session loads a window of recent turns out of thousands. A session
has thousands of messages but only tens of attachments, so this module scans the
rebuilt turns ONCE and returns just the attachments, ordered by turn then block.

Carriers:
  * ``present_file`` — the Forge ``present-file`` command's self-describing tool_use
    (``input = {file_id, name, mime, size, caption?}``; the part id IS the file_id).
  * ``send_user_file`` — the native Claude ``SendUserFile`` tool_use/tool_result pair;
    the result's small JSON lists one ``attachments`` entry per delivered file.
  * ``read`` — an image tool_result (Claude ``Read`` of an image, or any tool whose
    result is an image block). Shallow placeholders carry ``is_image``/``mime_type``/
    ``img_w``/``img_h`` hints; full results are recognised from their envelope's first
    and last bytes. Image payloads are NEVER decoded here.

Pure: no I/O, no ``skuld`` imports — the same boundary as ``transcript_rebuild``.

``AttachmentsFold`` drives ``rebuild_turns`` incrementally so a session of a million
frames is folded page by page in bounded memory, and a refresh re-reads only the frames
after the last settled turn. It relies on the rebuild's own rule: once an assistant
``conversation.turn`` row exists, raw frames BEFORE it are never reduced (the SDK rows
are authoritative), so everything up to that row is final.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from niuu.domain.conversation_timeline import timeline
from niuu.domain.transcript_rebuild import rebuild_turns

if TYPE_CHECKING:
    from volundr.domain.models import SessionLogEntry

KIND_DOCUMENT = "document"
KIND_IMAGE = "image"
KINDS = frozenset({KIND_DOCUMENT, KIND_IMAGE})

CARRIER_PRESENT_FILE = "present_file"
CARRIER_SEND_USER_FILE = "send_user_file"
CARRIER_READ = "read"

_PRESENT_FILE_TOOL = "present_file"
_SEND_USER_FILE_TOOL = "senduserfile"

# Only these extensions classify an elided Read with no image hint as an image. Vector and
# text-encoded formats (svg) come back from Read as text, so they are never assumed.
_RASTER_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

# A SendUserFile result is a few hundred bytes of metadata. Anything larger is not that
# shape, and decoding it would break the "never decode payloads" budget.
_SEND_USER_FILE_RESULT_MAX_CHARS = 64 * 1024

# How far into a full image envelope we look for its mime/dimension keys. The keys sit
# either before or after the base64 run, never inside it.
_ENVELOPE_SCAN_CHARS = 1024

# Top-level ``"type": "image"`` opens the envelope, or closes it when the writer sorted
# its keys (``{"file": {...}, "type": "image"}``, the shape stored on Thor). The nested
# ``"type": "image/png"`` never matches because the quote must close right after ``image``.
_ENVELOPE_HEAD_RE = re.compile(r'^\s*\{\s*"type"\s*:\s*"image"')
_ENVELOPE_TAIL_RE = re.compile(r'"type"\s*:\s*"image"\s*\}\s*$')
_ENVELOPE_MIME_RE = re.compile(r'"type"\s*:\s*"(image/[A-Za-z0-9.+-]+)"')
_ENVELOPE_WIDTH_RE = re.compile(r'"displayWidth"\s*:\s*(\d+)')
_ENVELOPE_HEIGHT_RE = re.compile(r'"displayHeight"\s*:\s*(\d+)')
_DATA_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,")
_IMAGE_BLOCK_TYPES = frozenset({"image", "input_image", "output_image", "image_url", "Image"})


TURN_ROW_KIND = "conversation.turn"
HISTORY_IMPORT_KIND = "history_import"
FOLD_EXCLUDED_KINDS = frozenset({"terminal_frame", "terminal_snapshot", "claude_hook"})
"""Frames that never carry a tool block: tmux pane paints and hook notifications.

They are ~95% of a tmux session's log (1.36M of 1.50M frames on Thor's largest), so the
fold skips them at the query. Only a pane-scraped text turn depends on them, and a text
turn holds no attachment."""


@dataclass
class AttachmentsFold:
    """Incremental attachments index over one session's durable log.

    ``resume_after`` is the seq of the last settled row: a refresh reads the rows after
    it, settles everything up to the newest assistant ``conversation.turn`` (or history
    import marker), and folds the remainder as the live tail without committing it.
    """

    session_id: str
    max_pending: int
    settled: list[dict[str, Any]] = field(default_factory=list)
    settled_turns: int = 0
    resume_after: int = 0
    truncated: bool = False
    _keys: set[tuple[str, str]] = field(default_factory=set)

    def settle(self, pending: list[SessionLogEntry]) -> list[SessionLogEntry]:
        """Settle ``pending`` up to its last boundary row; return the unsettled rest."""
        boundary = _last_boundary(pending)
        if boundary < 0:
            if len(pending) > self.max_pending:
                # A log with no settling rows (a pure raw-frame session) keeps its
                # newest frames; older raw work is dropped and the index says so.
                self.truncated = True
                return pending[-self.max_pending :]
            return pending
        head, rest = pending[: boundary + 1], pending[boundary + 1 :]
        marker = head[-1]
        if marker.kind != HISTORY_IMPORT_KIND:
            # Raw frames before an assistant turn row are covered by the turn rows.
            head = [row for row in head if row.kind in (TURN_ROW_KIND, HISTORY_IMPORT_KIND)]
        turns = rebuild_turns(head).turns
        for item in build_attachments_index(turns, self.session_id):
            self._add(self.settled, self._keys, item, self.settled_turns)
        self.settled_turns += len(turns)
        self.resume_after = marker.seq
        return rest

    def items(self, pending: list[SessionLogEntry]) -> tuple[list[dict[str, Any]], int]:
        """``(all items, total turns)``: the settled index plus the folded live tail."""
        turns = rebuild_turns(pending).turns if pending else []
        items = list(self.settled)
        keys = set(self._keys)
        for item in build_attachments_index(turns, self.session_id):
            self._add(items, keys, item, self.settled_turns)
        return items, self.settled_turns + len(turns)

    @staticmethod
    def _add(
        items: list[dict[str, Any]], keys: set[tuple[str, str]], item: dict[str, Any], offset: int
    ) -> None:
        key = (item["tool_use_id"], str(item.get("file_id") or item["name"]))
        if key in keys:
            return
        keys.add(key)
        items.append({**item, "turn_seq": item["turn_seq"] + offset})


def _last_boundary(rows: list[SessionLogEntry]) -> int:
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row.kind == HISTORY_IMPORT_KIND:
            return index
        if row.kind != TURN_ROW_KIND or not isinstance(row.payload, dict):
            continue
        turn = row.payload.get("turn")
        if isinstance(turn, dict) and turn.get("role") == "assistant":
            return index
    return -1


def build_attachments_index(turns: list[Any], session_id: str) -> list[dict[str, Any]]:
    """Return every document/image attachment in ``turns``, ordered by turn then block."""
    calls: dict[str, tuple[int, int, dict[str, Any], dict[str, Any]]] = {}
    results: dict[str, dict[str, Any]] = {}
    for turn_index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        parts = turn.get("parts")
        if not isinstance(parts, list):
            continue
        for part_index, block in enumerate(parts):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call_id = block.get("id")
                if isinstance(call_id, str) and call_id and call_id not in calls:
                    calls[call_id] = (turn_index, part_index, turn, block)
                continue
            if block.get("type") == "tool_result":
                result_id = block.get("tool_use_id")
                if isinstance(result_id, str) and result_id and result_id not in results:
                    results[result_id] = block
                    if result_id not in calls:
                        # A result whose call is outside the rebuilt window still
                        # anchors an image at its own position.
                        calls[result_id] = (turn_index, part_index, turn, {})

    items: list[tuple[int, int, int, dict[str, Any]]] = []
    for call_id, (turn_index, part_index, turn, call) in calls.items():
        base = _base_item(turn, turn_index, call)
        found = _attachments_for_call(call_id, call, results.get(call_id), base, session_id)
        for sub_index, item in enumerate(found):
            items.append((turn_index, part_index, sub_index, item))
    items.sort(key=lambda entry: entry[:3])
    return [item for *_, item in items]


def filter_attachments(items: list[dict[str, Any]], kind: str | None) -> list[dict[str, Any]]:
    """Keep only ``kind`` (``document``/``image``); ``None`` keeps everything."""
    if kind is None:
        return items
    return [item for item in items if item.get("kind") == kind]


def _attachments_for_call(
    call_id: str,
    call: dict[str, Any],
    result: dict[str, Any] | None,
    base: dict[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    name = str(call.get("name") or "").strip().lower()
    if name == _PRESENT_FILE_TOOL:
        return [_present_file_item(call_id, call, base, session_id)]
    if name == _SEND_USER_FILE_TOOL:
        return _send_user_file_items(call_id, call, result, base)
    if result is None or result.get("is_error"):
        return []
    image = _read_image_item(call_id, call, result, base, session_id)
    return [image] if image is not None else []


def _base_item(turn: dict[str, Any], turn_index: int, call: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {"turn_seq": turn_index}
    turn_id = turn.get("id")
    if isinstance(turn_id, str) and turn_id:
        base["turn_id"] = turn_id
    stamp = timeline(call)
    at = stamp["observed_at"] if stamp is not None else turn.get("created_at")
    if isinstance(at, str) and at:
        base["at"] = at
    return base


def _present_file_item(
    call_id: str, call: dict[str, Any], base: dict[str, Any], session_id: str
) -> dict[str, Any]:
    tool_input = _plain_input(call)
    file_id = _text(tool_input.get("file_id")) or call_id
    mime = _text(tool_input.get("mime")) or "application/octet-stream"
    item: dict[str, Any] = {
        "kind": _kind_for_mime(mime),
        "carrier": CARRIER_PRESENT_FILE,
        "tool_use_id": call_id,
        "file_id": file_id,
        "name": _text(tool_input.get("name")) or file_id,
        "mime": mime,
        **base,
    }
    size = _positive_int(tool_input.get("size"))
    if size is not None:
        item["size"] = size
    caption = _text(tool_input.get("caption"))
    if caption:
        item["caption"] = caption
    if item["kind"] == KIND_IMAGE:
        item["preview_url"] = f"/api/v1/forge/sessions/{session_id}/files/presented/{file_id}"
    return item


def _send_user_file_items(
    call_id: str, call: dict[str, Any], result: dict[str, Any] | None, base: dict[str, Any]
) -> list[dict[str, Any]]:
    if result is not None and result.get("is_error"):
        return []
    tool_input = _plain_input(call)
    caption = _text(tool_input.get("caption"))
    attachments = _send_user_file_attachments(result) if result is not None else []
    if not attachments:
        # The call is still running (live tail) or its result carried no metadata:
        # list the requested files so the delivery is visible as soon as it starts.
        requested = tool_input.get("files")
        requested = requested if isinstance(requested, list) else []
        attachments = [{"path": path} for path in requested if isinstance(path, str)]
    items = []
    for attachment in attachments:
        path = _text(attachment.get("path"))
        mime = (
            _text(attachment.get("media_type"))
            or _text(attachment.get("mime"))
            or mimetypes.guess_type(path)[0]
            or "application/octet-stream"
        )
        item: dict[str, Any] = {
            "kind": _kind_for_mime(mime),
            "carrier": CARRIER_SEND_USER_FILE,
            "tool_use_id": call_id,
            "name": posixpath.basename(path) or path or call_id,
            "mime": mime,
            **base,
        }
        file_uuid = _text(attachment.get("file_uuid"))
        if file_uuid:
            item["file_id"] = file_uuid
        size = _positive_int(attachment.get("size"))
        if size is not None:
            item["size"] = size
        if caption:
            item["caption"] = caption
        items.append(item)
    return items


def _send_user_file_attachments(result: dict[str, Any]) -> list[dict[str, Any]]:
    content = result.get("content")
    if isinstance(content, list):
        # Anthropic content-array form: the JSON rides in the first text block.
        content = next(
            (
                block.get("text")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ),
            None,
        )
    if isinstance(content, str):
        if len(content) > _SEND_USER_FILE_RESULT_MAX_CHARS:
            return []
        try:
            content = json.loads(content)
        except ValueError:
            return []
    if not isinstance(content, dict):
        return []
    attachments = content.get("attachments")
    if not isinstance(attachments, list):
        return []
    return [item for item in attachments if isinstance(item, dict)]


def _read_image_item(
    call_id: str,
    call: dict[str, Any],
    result: dict[str, Any],
    base: dict[str, Any],
    session_id: str,
) -> dict[str, Any] | None:
    path = _call_path(call)
    hint = _image_hint(result, path)
    if hint is None:
        return None
    mime, width, height = hint
    item: dict[str, Any] = {
        "kind": KIND_IMAGE,
        "carrier": CARRIER_READ,
        "tool_use_id": call_id,
        "name": posixpath.basename(path) or call_id,
        "mime": mime or mimetypes.guess_type(path)[0] or "image/*",
        **base,
    }
    if width is not None and height is not None:
        item["image"] = {"w": width, "h": height}
    item["preview_url"] = f"/api/v1/forge/sessions/{session_id}/tool-result/{call_id}/preview"
    return item


def _image_hint(
    result: dict[str, Any], path: str
) -> tuple[str | None, int | None, int | None] | None:
    """``(mime, w, h)`` when the result is an image, else ``None`` — without decoding it."""
    if result.get("is_image"):
        return (
            _text(result.get("mime_type")) or None,
            _positive_int(result.get("img_w")),
            _positive_int(result.get("img_h")),
        )
    if "content" not in result:
        # Elided placeholder with no image hint (an older elider): fall back to the
        # Read path's extension. A text Read stays excluded.
        if posixpath.splitext(path.lower())[1] in _RASTER_IMAGE_EXTENSIONS:
            return (mimetypes.guess_type(path)[0], None, None)
        return None
    content = result.get("content")
    if isinstance(content, str):
        return _envelope_hint(content)
    if isinstance(content, list):
        return _block_hint(content)
    return None


def _envelope_hint(content: str) -> tuple[str | None, int | None, int | None] | None:
    head = content[:_ENVELOPE_SCAN_CHARS]
    tail = content[-_ENVELOPE_SCAN_CHARS:]
    if not (_ENVELOPE_HEAD_RE.match(head) or _ENVELOPE_TAIL_RE.search(tail)):
        return None

    def find(pattern: re.Pattern[str]) -> str | None:
        match = pattern.search(tail) or pattern.search(head)
        return match.group(1) if match else None

    width, height = find(_ENVELOPE_WIDTH_RE), find(_ENVELOPE_HEIGHT_RE)
    return (
        find(_ENVELOPE_MIME_RE),
        int(width) if width else None,
        int(height) if height else None,
    )


def _block_hint(content: list[Any]) -> tuple[str | None, int | None, int | None] | None:
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _IMAGE_BLOCK_TYPES:
            continue
        source = block.get("source")
        if isinstance(source, dict):
            return (_text(source.get("media_type")) or None, None, None)
        if isinstance(block.get("data"), str):
            return (_text(block.get("mimeType")) or None, None, None)
        url = block.get("image_url") or block.get("imageUrl")
        if isinstance(url, dict):
            url = url.get("url")
        if isinstance(url, str):
            match = _DATA_URL_RE.match(url[:_ENVELOPE_SCAN_CHARS])
            if match:
                return (match.group(1), None, None)
    return None


def _call_path(call: dict[str, Any]) -> str:
    tool_input = call.get("input")
    if not isinstance(tool_input, dict):
        return ""
    if tool_input.get("_elided_input"):
        # The shallow placeholder keeps the primary argument (file_path) as its preview.
        return _text(tool_input.get("preview"))
    return _text(tool_input.get("file_path")) or _text(tool_input.get("path"))


def _plain_input(call: dict[str, Any]) -> dict[str, Any]:
    tool_input = call.get("input")
    if not isinstance(tool_input, dict) or tool_input.get("_elided_input"):
        return {}
    return tool_input


def _kind_for_mime(mime: str) -> str:
    return KIND_IMAGE if mime.lower().startswith("image/") else KIND_DOCUMENT


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None

"""Wire names shared by the broker, the facade and Forge for durable presented files (F2)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The tool name of a presented file's turn part, in every engine.
PRESENT_FILE_TOOL_NAME = "present_file"

#: A present-file id: ``pf_`` and 32 hex digits (the broker mints it).
PRESENTED_FILE_ID_PATTERN = r"^pf_[0-9a-f]{32}$"

#: Forge's default cap on one durable presented file (``presented_files.max_bytes``).
PRESENTED_FILE_DEFAULT_MAX_BYTES = 50 * 1024 * 1024

#: Forge's default read size when it streams a durable copy (``presented_files.read_chunk_bytes``).
PRESENTED_FILE_DEFAULT_READ_CHUNK_BYTES = 1024 * 1024

#: Forge's answer when a durable copy would exceed the host's total quota (LEX-1514):
#: HTTP 507 with ``detail.code`` set to this. The broker fails ``present-file`` with
#: ``detail.message``; the refusal is final, never retried.
PRESENTED_FILES_QUOTA_STATUS = 507
PRESENTED_FILES_QUOTA_CODE = "presented_files_quota"

#: The header carrying a presented file's display name on the durable upload (URL-quoted).
PRESENTED_FILE_NAME_HEADER = "x-presented-file-name"

#: A presented file is agent-supplied: browsers render it and never run it on Forge's origin
#: (HTML or SVG script, sniffed types). Every route that serves one sends these, the public
#: facade included (judge R16-1).
PRESENTED_FILE_SAFETY_HEADERS = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox",
}

#: Types the sandbox would break and that cannot run script on Forge's origin: Chrome refuses
#: to show a PDF in its viewer under ``Content-Security-Policy: sandbox``.
SANDBOX_EXEMPT_TYPES = frozenset({"application/pdf"})


def presented_file_safety_headers(media_type: str | None) -> dict[str, str]:
    """The safety policy for a presented file of ``media_type``: ``nosniff`` always, and the
    sandbox for every type but the exempt ones (judge R16-1)."""
    base = (media_type or "").split(";")[0].strip().lower()
    if base in SANDBOX_EXEMPT_TYPES:
        return {"x-content-type-options": "nosniff"}
    return dict(PRESENTED_FILE_SAFETY_HEADERS)


#: The durable copy never changes under its id (a different file under the same id is a 409),
#: so a browser may keep it for a day; a viewer whose access ends keeps it that long. The live
#: broker's copy is served only while the session runs, so it is never cached.
PRESENTED_FILE_DURABLE_CACHE = "private, max-age=86400, immutable"
PRESENTED_FILE_LIVE_CACHE = "private, no-store"
PRESENTED_FILE_CACHE_POLICIES = frozenset({PRESENTED_FILE_DURABLE_CACHE, PRESENTED_FILE_LIVE_CACHE})


# -- linked versions: one artifact, many presented files ------------------------------

#: An agent's own stable name for a document it presents again and again (``plan``,
#: ``spec``). Letters, digits, ``.``, ``_`` and ``-``, starting with a letter or digit.
PRESENTED_ARTIFACT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

#: The ``present_file`` tool input (and turn part input) that carries the key and the
#: broker's version label for the file.
PRESENTED_ARTIFACT_KEY_FIELD = "artifact_key"
PRESENTED_VERSION_LABEL_FIELD = "version_label"


def presented_artifact_provider_key(session_id: object, artifact_key: str) -> str:
    """Forge's ``provider_key`` for a keyed presented file.

    Scoped to the session: two runs that each present a ``plan`` have two artifacts,
    while every version one run presents under ``plan`` is one artifact. Each presented
    file is a revision of it, and the file's id is that revision's id."""
    return f"{session_id}:{artifact_key}"


def presented_version_label(number: int) -> str:
    """The label of the ``number``-th file presented under one key (``v1``, ``v2``)."""
    return f"v{number}"


#: The mesh RPC a flock member sends the session's broker to present a workspace file:
#: ``{"type": "present_file", "path": <workspace-relative>, "title"?, "caption"?,
#: "artifact_key"?, "peer_id"}``. The reply is ``{"status": "complete", file_id, name,
#: mime, size, url?, durable, artifact_key?, provider_key?, revision_id?,
#: version_label?}`` or ``{"status": "error", "error": ...}``.
PRESENT_FILE_RPC_TYPE = "present_file"


@dataclass(frozen=True)
class PresentedArtifact:
    """One version of a keyed presented file, read from a ``present_file`` turn part."""

    artifact_key: str
    provider_key: str
    revision_id: str
    name: str
    version_label: str | None
    mime: str | None
    url: str | None


def presented_artifacts(session_id: object, turn: Mapping[str, Any]) -> list[PresentedArtifact]:
    """The keyed presented files one ``conversation.turn`` carries (often none).

    The same reading Forge's capture makes: a part named ``present_file`` whose input
    has a ``pf_`` file id and a well-formed artifact key. Files presented without a key
    stand alone and are not artifact versions."""
    found: list[PresentedArtifact] = []
    for part in turn.get("parts") or []:
        if not isinstance(part, Mapping) or part.get("type") != "tool_use":
            continue
        if part.get("name") != PRESENT_FILE_TOOL_NAME:
            continue
        tool_input = part.get("input")
        if not isinstance(tool_input, Mapping):
            continue
        file_id = _text(tool_input.get("file_id"))
        key = _text(tool_input.get(PRESENTED_ARTIFACT_KEY_FIELD))
        if file_id is None or re.fullmatch(PRESENTED_FILE_ID_PATTERN, file_id) is None:
            continue
        if key is None or re.fullmatch(PRESENTED_ARTIFACT_KEY_PATTERN, key) is None:
            continue
        found.append(
            PresentedArtifact(
                artifact_key=key,
                provider_key=presented_artifact_provider_key(session_id, key),
                revision_id=file_id,
                name=_text(tool_input.get("name")) or file_id,
                version_label=_text(tool_input.get(PRESENTED_VERSION_LABEL_FIELD)),
                mime=_text(tool_input.get("mime")),
                url=_text(tool_input.get("url")),
            )
        )
    return found


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None

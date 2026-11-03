"""Hand presented files to Forge's durable store (control-plane F2).

The broker stages every ``present-file`` copy next to itself, which only serves while
the broker runs. Right after staging, it uploads the bytes to Forge
(``PUT /sessions/{id}/files/presented/{file_id}``); Forge keeps them and answers with
the durable URL on the host's tailnet name. A failed upload is retried in the
background and never fails ``present-file`` itself: the live broker still serves the
staged copy, and the Forge route serves the durable one as soon as it lands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from niuu.domain.presented_files import (
    PRESENTED_FILE_NAME_HEADER,
    PRESENTED_FILES_QUOTA_CODE,
    PRESENTED_FILES_QUOTA_STATUS,
)
from skuld.event_log import FORGE_SESSIONS_PATH

logger = logging.getLogger("skuld.broker")

DURABLE_MARKER = "durable.json"
# What the upload needs beyond the bytes (name, mime), written at staging so an upload
# that has not landed can resume after a broker restart.
PENDING_MARKER = "presented.json"
# Forge refused the copy for good (session gone, conflict, too large): never retried.
REFUSED_MARKER = "refused.json"
SIDECARS = frozenset({DURABLE_MARKER, PENDING_MARKER, REFUSED_MARKER})
# 4xx answers that a later attempt can succeed past; every other 4xx is final.
_RETRYABLE_CLIENT_ERRORS = frozenset({408, 425, 429})
NAME_HEADER = PRESENTED_FILE_NAME_HEADER


class PresentedFileQuotaRefusedError(Exception):
    """Forge refused the durable copy: the host's presented-file quota is full (LEX-1514).

    Final, never retried: ``present-file`` fails with Forge's message, which names
    ``presented_files.max_total_bytes`` and the remedy.
    """


def _quota_refusal(response: Any) -> str | None:
    """Forge's quota message when ``response`` is a quota refusal, else ``None``."""
    if response.status_code != PRESENTED_FILES_QUOTA_STATUS:
        return None
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    if isinstance(detail, dict) and detail.get("code") == PRESENTED_FILES_QUOTA_CODE:
        return str(detail.get("message") or "presented-file quota exceeded")
    return None


def durable_record(staged: Path) -> dict[str, Any] | None:
    """The Forge receipt written beside a staged copy once it is durable."""
    marker = staged.parent / DURABLE_MARKER
    try:
        record = json.loads(marker.read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def write_pending_record(staged: Path, *, name: str, mime: str) -> None:
    """Record the upload's name and mime beside the staged copy."""
    try:
        (staged.parent / PENDING_MARKER).write_text(json.dumps({"name": name, "mime": mime}))
    except OSError as exc:
        logger.warning(
            "present-file: no pending record for %s, so a restart cannot resume its upload: %s",
            staged.parent.name,
            repr(exc),
        )


def pending_record(staged: Path) -> dict[str, str] | None:
    """The name and mime recorded at staging (``presented.json``), or ``None``."""
    try:
        record = json.loads((staged.parent / PENDING_MARKER).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not record.get("name") or not record.get("mime"):
        return None
    return {"name": str(record["name"]), "mime": str(record["mime"])}


def is_refused(staged: Path) -> bool:
    return (staged.parent / REFUSED_MARKER).exists()


def pending_uploads(root: Path) -> list[tuple[str, Path, str, str]]:
    """Staged copies whose durable upload has not landed: ``(file_id, path, name, mime)``.

    Only copies with a pending record count; files staged before F2 have none and keep
    being served by the live broker alone."""
    out: list[tuple[str, Path, str, str]] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        content = entry / "content"
        marker = entry / PENDING_MARKER
        if (entry / DURABLE_MARKER).exists() or (entry / REFUSED_MARKER).exists():
            continue
        if content.is_symlink() or not content.is_file():
            continue
        if marker.is_symlink():
            continue
        try:
            record = json.loads(marker.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not record.get("name") or not record.get("mime"):
            continue
        out.append((entry.name, content, str(record["name"]), str(record["mime"])))
    return out


async def upload_presented_file(
    broker: Any,
    file_id: str,
    staged: Path,
    *,
    name: str,
    mime: str,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """Upload one staged copy; return Forge's receipt (``url``, ``path``…) or ``None``."""
    if not broker.volundr_api_url or not broker.session_id:
        return None
    existing = durable_record(staged)
    if existing is not None:
        return existing
    settings = broker._settings.presented_durable
    try:
        data = await asyncio.to_thread(staged.read_bytes)
        client = await broker._get_http_client()
        response = await client.put(
            f"{FORGE_SESSIONS_PATH}/{broker.session_id}/files/presented/{file_id}",
            content=data,
            headers={"content-type": mime, NAME_HEADER: quote(name, safe="")},
            timeout=timeout or settings.upload_timeout_seconds,
        )
    except Exception as exc:  # network, auth refresh or file errors: retry later
        logger.warning("present-file: durable upload of %s failed: %s", file_id, repr(exc))
        return None
    if (quota := _quota_refusal(response)) is not None:
        logger.error("present-file: %s refused by Forge's quota: %s", file_id, quota)
        _mark_refused(staged, response.status_code)
        raise PresentedFileQuotaRefusedError(quota)
    if response.status_code >= 300:
        logger.warning(
            "present-file: Forge refused durable copy of %s (HTTP %d)",
            file_id,
            response.status_code,
        )
        if 400 <= response.status_code < 500 and response.status_code not in (
            _RETRYABLE_CLIENT_ERRORS
        ):
            _mark_refused(staged, response.status_code)
        return None
    try:
        receipt = response.json()
    except ValueError:
        return None
    if not isinstance(receipt, dict):
        return None
    try:
        (staged.parent / DURABLE_MARKER).write_text(json.dumps(receipt))
    except OSError:
        logger.debug("present-file: could not write durable marker for %s", file_id)
    return receipt


def _mark_refused(staged: Path, status_code: int) -> None:
    try:
        (staged.parent / REFUSED_MARKER).write_text(json.dumps({"status": status_code}))
    except OSError:
        logger.debug("present-file: could not write refused marker for %s", staged.parent.name)


async def retry_presented_upload(
    broker: Any, file_id: str, staged: Path, *, name: str, mime: str, immediate: bool = False
) -> dict[str, Any] | None:
    """Retry a failed upload with exponential backoff, bounded by config. ``immediate``
    makes the first attempt without waiting (a resume after restart). A final refusal
    from Forge ends the retries."""
    settings = broker._settings.presented_durable
    delay = settings.retry_backoff_seconds
    for attempt in range(settings.retry_attempts):
        if attempt or not immediate:
            await asyncio.sleep(delay)
            delay = min(delay * 2, settings.retry_backoff_max_seconds)
        try:
            receipt = await upload_presented_file(broker, file_id, staged, name=name, mime=mime)
        except PresentedFileQuotaRefusedError:
            return None  # final and already logged at error; the marker stops resumes
        if receipt is not None:
            return receipt
        if is_refused(staged):
            return None
    logger.error(
        "present-file: %s is not durable after %d uploads "
        "(presented_durable.retry_attempts); the live broker still serves it and the "
        "upload resumes when the broker restarts",
        file_id,
        settings.retry_attempts,
    )
    return None

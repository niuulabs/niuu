"""Tool-result image previews — scaled-down JPEG thumbnails + a disk cache.

An image ``Read`` in a coding session lands in the transcript as a tool_result
whose content is a multi-hundred-KB (up to multi-MB) base64 envelope. A phone
rendering ~20 inline thumbnails over limited bandwidth cannot pull full
envelopes for each one — the strip stalls. This module generates a ~400px-class
JPEG preview (15-25 KB, a ~100x transit cut) server-side ON FIRST REQUEST and
caches it on disk keyed by ``(session_id, tool_use_id)`` so every later connect
serves it in milliseconds.

Layered next to :mod:`skuld.conversation_shallow` on purpose: both are pure
helpers shared by the broker and volundr's REST tier, and the preview endpoint
must live at the VOLUNDR layer — old brokers pre-date the tool-result endpoint
entirely, so volundr's durable-transcript fallback is the only source of the
full envelope for exactly the sessions that need previews most. A tool_use_id's
result is immutable, so cached previews never need invalidation.

Pillow is imported lazily inside :func:`generate_preview_jpeg` so the platform
boots (and every non-preview code path runs) even when Pillow is not installed;
callers map :class:`PreviewUnavailableError` to HTTP 501 and the client falls
back to the full tool-result fetch.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

PREVIEW_MAX_EDGE = 400
"""Longest preview edge in pixels — the ~200pt thumbnail class @2x Retina.

The inline card / drawer chips render at roughly 200 display points; 400px keeps
them sharp on @2x (marginally soft on @3x, thumbnail-fine) and doubles as the
instant scaled-up placeholder when the full-size viewer opens.
"""

PREVIEW_JPEG_QUALITY = 72
"""JPEG quality for previews — ~15-25 KB per image at 400px, visually clean."""

_NEGATIVE_CACHE_MAX = 512
"""Bounded in-memory set of ids confirmed NOT to be images.

A repeat preview request for a text tool_result must not re-trigger a multi-second
durable-transcript rebuild just to re-discover it is not an image.
"""

_SESSION_LOCK_MAX = 256
"""Cap on retained per-session locks (idle, unlocked locks are evicted first)."""


class PreviewUnavailableError(RuntimeError):
    """Pillow is not installed — previews cannot be generated (map to HTTP 501)."""


def extract_image_bytes(content: Any) -> tuple[bytes, str] | None:
    """Return ``(raw_bytes, mime)`` when a tool_result ``content`` is an image.

    Accepts the Skuld ``Read`` envelope — a JSON STRING (or already-parsed dict)
    of ``{"type": "image", "file": {"base64": …, "type": "image/png", …}}`` —
    and, defensively, the Anthropic content-array form
    ``[{"type": "image", "source": {"data": …, "media_type": …}}]``
    (mirroring ``conversation_shallow._content_image_info``).

    Returns ``None`` for anything that is not an image envelope. Raises
    ``ValueError`` (via ``binascii.Error``) when the envelope IS an image but its
    base64 payload is corrupt — the caller maps that to 404, not a 500.
    """
    parsed = content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return None
    b64: Any = None
    mime: Any = None
    if isinstance(parsed, dict) and parsed.get("type") == "image":
        file = parsed.get("file")
        if isinstance(file, dict):
            b64 = file.get("base64")
            mime = file.get("type")
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and item.get("type") == "image":
                source = item.get("source")
                if isinstance(source, dict):
                    b64 = source.get("data")
                    mime = source.get("media_type")
                break
    if not isinstance(b64, str) or not b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"corrupt base64 in image tool_result: {e}") from e
    return raw, mime if isinstance(mime, str) else "application/octet-stream"


def generate_preview_jpeg(raw: bytes, *, max_edge: int = PREVIEW_MAX_EDGE) -> bytes:
    """Downscale raw image bytes to a JPEG whose longest edge is ``max_edge``.

    PNG/palette alpha is flattened onto white (JPEG has no alpha); EXIF
    orientation is honored. Raises :class:`PreviewUnavailableError` when Pillow
    is missing and ``ValueError`` when the bytes are not a decodable image.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as e:  # pragma: no cover - exercised only without Pillow
        raise PreviewUnavailableError("Pillow is not installed") from e

    try:
        with Image.open(io.BytesIO(raw)) as source:
            img = ImageOps.exif_transpose(source)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.split()[-1])
                img = flat
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((max_edge, max_edge))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=PREVIEW_JPEG_QUALITY, optimize=True)
            return buf.getvalue()
    except (OSError, ValueError, SyntaxError) as e:
        # Pillow raises UnidentifiedImageError(OSError) for undecodable bytes and
        # assorted OSError/SyntaxError for truncated/hostile files.
        raise ValueError(f"undecodable image bytes: {e}") from e


class PreviewCache:
    """Disk cache of generated previews + the async single-flight machinery.

    Layout: ``{root}/{session_id}/{sha256(tool_use_id)[:40]}-e{max_edge}.jpg``.
    Hashing the (model-generated, client-supplied) tool_use_id kills any path
    traversal; the session_id path segment is a validated UUID at the REST tier.
    Writes are tmp + ``os.replace`` (atomic, safe across processes). mtime is
    touched on read so pruning evicts least-recently-USED entries when the cache
    exceeds ``max_entries`` / ``max_bytes``. The cache survives platform
    restarts by construction (plain files under ``~/.niuu/preview-cache``).
    """

    def __init__(
        self,
        root: Path | str,
        *,
        max_edge: int = PREVIEW_MAX_EDGE,
        max_entries: int = 2000,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.root = Path(root)
        self.max_edge = max_edge
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Ordered dict-as-set: bounded FIFO of ids confirmed non-image/absent-content.
        self._non_image: dict[tuple[str, str], None] = {}

    # -- paths -------------------------------------------------------------

    def _path(self, session_id: str, tool_use_id: str) -> Path:
        digest = hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()[:40]
        return self.root / str(session_id) / f"{digest}-e{self.max_edge}.jpg"

    # -- disk cache --------------------------------------------------------

    def get(self, session_id: str, tool_use_id: str) -> bytes | None:
        """Return the cached preview bytes, touching mtime as an LRU signal."""
        path = self._path(session_id, tool_use_id)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        try:
            os.utime(path)
        except OSError:  # pragma: no cover - concurrent eviction race
            pass
        return data

    def has(self, session_id: str, tool_use_id: str) -> bool:
        """Existence check without reading bytes (used by the warm pass)."""
        return self._path(session_id, tool_use_id).exists()

    def put(self, session_id: str, tool_use_id: str, jpeg: bytes) -> None:
        """Atomically persist a preview, then opportunistically prune over caps."""
        path = self._path(session_id, tool_use_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{uuid4().hex}.tmp")
        tmp.write_bytes(jpeg)
        os.replace(tmp, path)
        self._prune()

    def _prune(self) -> None:
        """Evict oldest-mtime previews while the cache exceeds its caps."""
        try:
            entries: list[tuple[float, int, Path]] = []
            for path in self.root.glob("*/*.jpg"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))
        except OSError:  # pragma: no cover - cache root vanished
            return
        total = sum(size for _, size, _ in entries)
        if len(entries) <= self.max_entries and total <= self.max_bytes:
            return
        entries.sort(key=lambda item: item[0])
        for _, size, path in entries:
            if len(entries) <= self.max_entries and total <= self.max_bytes:
                break
            try:
                path.unlink()
            except OSError:  # pragma: no cover - concurrent eviction race
                continue
            total -= size
            entries = entries[1:]

    # -- single-flight -----------------------------------------------------

    def session_lock(self, session_id: str) -> asyncio.Lock:
        """Per-session lock serializing first-request generation.

        Load-bearing, not an optimization: a cache-miss on an old-broker session
        costs a multi-second durable-transcript rebuild, and the client fires
        3-4 preview requests in parallel — without single-flight each would pay
        (and contend) its own rebuild. The first holder's warm pass fills the
        cache; the waiters wake into cache hits.
        """
        key = str(session_id)
        lock = self._session_locks.get(key)
        if lock is None:
            if len(self._session_locks) >= _SESSION_LOCK_MAX:
                for stale_key, stale in list(self._session_locks.items()):
                    if not stale.locked():
                        del self._session_locks[stale_key]
                        break
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    # -- negative cache ----------------------------------------------------

    def mark_non_image(self, session_id: str, tool_use_id: str) -> None:
        """Remember that this tool_result is not an image (bounded, in-memory)."""
        key = (str(session_id), tool_use_id)
        self._non_image[key] = None
        while len(self._non_image) > _NEGATIVE_CACHE_MAX:
            self._non_image.pop(next(iter(self._non_image)))

    def is_non_image(self, session_id: str, tool_use_id: str) -> bool:
        return (str(session_id), tool_use_id) in self._non_image


def warm_previews_from_turns(
    cache: PreviewCache,
    session_id: str,
    turns: Any,
) -> int:
    """Generate + cache a preview for EVERY image tool_result in rebuilt turns.

    The 4-5 s cost of a durable-transcript rebuild dominates preview generation;
    once one preview request has paid it, this pass reuses that single rebuilt
    transcript to warm the whole session (e.g. ~69 images for one rebuild instead
    of 69 rebuilds). Non-images and corrupt/undecodable payloads are skipped;
    :class:`PreviewUnavailableError` (no Pillow) propagates so the caller can 501.

    Synchronous by design — run it via ``asyncio.to_thread`` so Pillow work does
    not starve the event loop. Returns the number of previews generated.
    """
    warmed = 0
    if not isinstance(turns, list):
        return warmed
    for turn in turns:
        parts = turn.get("parts") if isinstance(turn, dict) else None
        for block in parts or []:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            if cache.has(session_id, tool_use_id):
                continue
            try:
                extracted = extract_image_bytes(block.get("content"))
            except ValueError:
                logger.warning(
                    "Skipping corrupt image tool_result %s during preview warm",
                    tool_use_id.replace("\n", "\\n").replace("\r", "\\r"),
                )
                continue
            if extracted is None:
                continue
            try:
                jpeg = generate_preview_jpeg(extracted[0], max_edge=cache.max_edge)
            except ValueError:
                logger.warning(
                    "Skipping undecodable image tool_result %s during preview warm",
                    tool_use_id.replace("\n", "\\n").replace("\r", "\\r"),
                )
                continue
            cache.put(session_id, tool_use_id, jpeg)
            warmed += 1
    return warmed

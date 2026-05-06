"""Chronicle watcher: tail Claude Code JSONL session files and report events.

Monitors ``~/.claude/projects/-volundr-sessions-{SESSION_ID}-workspace/``
for new or modified ``.jsonl`` files using inotify (with a polling fallback)
and feeds parsed timeline events to the Volundr chronicles API.

Designed to run as an asyncio task inside skuld's broker, sharing the same
event loop and HTTP client pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from skuld.event_mapper import EventMapper

logger = logging.getLogger("skuld.chronicle_watcher")
FORGE_CHRONICLES_PATH = "/api/v1/forge/chronicles"

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

_STATE_FILENAME = ".chronicle-watcher-state.json"


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to load watcher state from %s", state_path, exc_info=True)
        return {}


def _save_state(state_path: Path, state: dict) -> None:
    try:
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("Failed to save watcher state to %s", state_path, exc_info=True)


# ---------------------------------------------------------------------------
# ChronicleWatcher
# ---------------------------------------------------------------------------


class ChronicleWatcher:
    """Watch JSONL session files and POST timeline events to the API."""

    def __init__(
        self,
        *,
        session_id: str,
        watch_dir: Path,
        api_base_url: str,
        http_headers: dict[str, str],
        debounce_ms: int = 500,
    ) -> None:
        self._session_id = session_id
        self._watch_dir = watch_dir
        self._api_base_url = api_base_url
        self._http_headers = http_headers
        self._debounce_s = debounce_ms / 1000.0

        self._http_client: httpx.AsyncClient | None = None
        self._tail_tasks: dict[str, asyncio.Task] = {}
        self._mappers: dict[str, EventMapper] = {}
        self._state: dict = {}
        self._state_path = watch_dir / _STATE_FILENAME
        self._shutting_down = False
        self._watch_task: asyncio.Task | None = None

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Begin watching the session directory for JSONL files."""
        logger.info("Chronicle watcher starting for session %s", self._session_id)
        logger.info("Watching directory: %s", self._watch_dir)

        self._state = _load_state(self._state_path)

        # Tail any existing JSONL files (resume after restart)
        if self._watch_dir.is_dir():
            for path in sorted(self._watch_dir.glob("*.jsonl")):
                self._ensure_tail(path)

        self._watch_task = asyncio.create_task(self._watch_directory())

    async def stop(self) -> None:
        """Graceful shutdown: cancel tasks and close HTTP client."""
        self._shutting_down = True
        logger.info("Chronicle watcher stopping")

        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                logger.debug("Watcher task cancelled during shutdown")

        for task in self._tail_tasks.values():
            task.cancel()
        if self._tail_tasks:
            await asyncio.gather(*self._tail_tasks.values(), return_exceptions=True)
        self._tail_tasks.clear()

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        _save_state(self._state_path, self._state)
        logger.info("Chronicle watcher stopped")

    # ---- directory watching -----------------------------------------------

    async def _watch_directory(self) -> None:
        """Watch for new/modified JSONL files using inotifywait."""
        self._watch_dir.mkdir(parents=True, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                "inotifywait",
                "-m",  # monitor continuously
                "-e",
                "modify,create",
                "--format",
                "%f",
                str(self._watch_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("inotifywait not available, falling back to polling")
            await self._poll_directory()
            return

        assert proc.stdout is not None
        try:
            while not self._shutting_down:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                filename = line_bytes.decode().strip()
                if not filename.endswith(".jsonl"):
                    continue
                # Skip subagent directories
                if "/" in filename:
                    continue
                self._ensure_tail(self._watch_dir / filename)
        except asyncio.CancelledError:
            logger.debug("Directory watch cancelled during shutdown")
        finally:
            proc.kill()
            await proc.wait()

    async def _poll_directory(self) -> None:
        """Fallback: poll for new/modified JSONL files."""
        known_mtimes: dict[str, float] = {}
        while not self._shutting_down:
            await asyncio.sleep(2)
            try:
                if not self._watch_dir.is_dir():
                    continue
                for path in self._watch_dir.glob("*.jsonl"):
                    mtime = path.stat().st_mtime
                    prev = known_mtimes.get(path.name)
                    if prev is None or mtime != prev:
                        known_mtimes[path.name] = mtime
                        self._ensure_tail(path)
            except Exception:
                logger.debug("Error polling directory", exc_info=True)

    # ---- file tailing -----------------------------------------------------

    def _ensure_tail(self, path: Path) -> None:
        """Start a tail task for this file if one isn't already running."""
        name = path.name
        existing = self._tail_tasks.get(name)
        if existing and not existing.done():
            return
        self._tail_tasks[name] = asyncio.create_task(
            self._tail_file(path),
            name=f"tail-{name}",
        )

    async def _tail_file(self, path: Path) -> None:
        """Tail a single JSONL file, mapping events and reporting them."""
        name = path.name
        mapper = self._mappers.get(name)
        if mapper is None:
            mapper = EventMapper()
            self._mappers[name] = mapper

        # Resume from saved offset
        file_state = self._state.get(name) or {}
        offset = file_state.get("offset", 0)

        logger.info("Tailing %s from offset %d", name, offset)

        batch: list[dict] = []
        last_flush = asyncio.get_event_loop().time()

        try:
            while not self._shutting_down:
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    break

                if size <= offset:
                    # No new data — wait for inotify or polling to wake us
                    await asyncio.sleep(0.5)
                    continue

                with open(path, encoding="utf-8") as f:
                    f.seek(offset)
                    new_data = f.read()
                    new_offset = f.tell()

                for line_str in new_data.splitlines():
                    line_str = line_str.strip()
                    if not line_str:
                        continue
                    try:
                        line = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    events = mapper.map_event(line)
                    batch.extend(events)

                    # Track last UUID for dedup
                    uuid = line.get("uuid")
                    if uuid:
                        file_state["last_uuid"] = uuid

                offset = new_offset
                file_state["offset"] = offset
                self._state[name] = file_state

                # Flush batch if debounce window elapsed or batch is large
                now = asyncio.get_event_loop().time()
                if batch and (now - last_flush >= self._debounce_s or len(batch) >= 20):
                    await self._report_batch(batch)
                    batch = []
                    last_flush = now
                    _save_state(self._state_path, self._state)

        except asyncio.CancelledError:
            logger.debug("Tail task cancelled for %s", name)
        finally:
            # Flush remaining events
            if batch:
                await self._report_batch(batch)
            self._state[name] = file_state
            _save_state(self._state_path, self._state)

    # ---- auth / header updates --------------------------------------------

    def update_headers(self, headers: dict[str, str]) -> None:
        """Replace HTTP headers (e.g. when a user JWT becomes available).

        Forces the HTTP client to be recreated on the next request so the
        new headers take effect.
        """
        self._http_headers = headers
        if self._http_client is not None:
            # Schedule close on next event-loop tick; recreated lazily
            asyncio.ensure_future(self._http_client.aclose())
            self._http_client = None

    # ---- API reporting ----------------------------------------------------

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._api_base_url,
                timeout=10.0,
                headers=self._http_headers,
            )
        return self._http_client

    async def _report_batch(self, events: list[dict]) -> None:
        """POST each timeline event to the Volundr chronicles API."""
        if not events:
            return

        client = await self._get_http_client()
        url = f"{FORGE_CHRONICLES_PATH}/{self._session_id}/timeline"

        for event in events:
            try:
                response = await client.post(url, json=event)
                if response.status_code < 300:
                    logger.debug(
                        "Watcher timeline event: type=%s, t=%d, label=%s",
                        event.get("type"),
                        event.get("t", 0),
                        event.get("label", "")[:40],
                    )
                else:
                    logger.debug(
                        "Watcher timeline event failed (%d): %s",
                        response.status_code,
                        response.text[:200],
                    )
            except Exception:
                logger.debug(
                    "Failed to report watcher timeline event: type=%s",
                    event.get("type"),
                    exc_info=True,
                )

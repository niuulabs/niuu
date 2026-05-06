"""Modular system prompt builder with two-layer caching.

The PromptBuilder composes a system prompt from named sections.  Static
sections (identity, tool schemas, guidance) are marked cacheable and can be
served from an in-process LRU or a disk snapshot.  Dynamic sections (memory
context, shared context) bypass the cache.

Sections are rendered either as a plain string (for any LLM backend) or as a
list of Anthropic-format text blocks with optional ``cache_control`` for
Claude prompt-caching.

Two-layer cache
---------------
1. **In-process LRU** — ``collections.OrderedDict`` keyed by a content hash.
   Fast path; lives for the duration of the process.
2. **Disk snapshot** — JSON file under ``cache_dir``.  Validated against a
   mtime/size manifest of the source files that produced the static content.
   Survives process restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from ravn.domain.models import AgentTask

logger = logging.getLogger(__name__)

# Separator used when rendering sections to a plain string.
_SECTION_SEP = "\n\n"

# ── Guidance text injected for non-Claude models ────────────────────────────

_NON_CLAUDE_GUIDANCE = (
    "Tool use instructions: when a tool is available and useful, always call "
    "it using the provided JSON function-call schema.  Never simulate tool "
    "output without actually calling the tool.  If multiple tools are needed, "
    "call them in sequence, one at a time."
)


# ---------------------------------------------------------------------------
# PromptSection
# ---------------------------------------------------------------------------


@dataclass
class PromptSection:
    """A single named section of a system prompt."""

    name: str
    content: str
    cacheable: bool = True  # If True, eligible for LRU / disk cache


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    """Assembles a system prompt from layered, independently-cacheable sections.

    Sections are added in declaration order and rendered in that order.

    Usage::

        builder = PromptBuilder(cache=PromptCache())
        builder.set_identity("You are Ravn …")
        builder.set_tool_schemas(tool_defs)
        builder.set_project_context(ctx_text, source_files=[Path("RAVN.md")])
        builder.set_guidance(model="claude-sonnet-4-6")

        # Render to plain string (any backend):
        system_str = builder.render()

        # Render to Anthropic blocks (Claude prompt caching):
        system_blocks = builder.render_blocks()
    """

    def __init__(self, cache: PromptCache | None = None) -> None:
        self._sections: list[PromptSection] = []
        self._cache = cache
        self._source_files: list[Path] = []  # Files tracked in manifest

    # ------------------------------------------------------------------
    # Section setters
    # ------------------------------------------------------------------

    def set_identity(self, text: str) -> None:
        """Set the identity/persona section (static, cacheable)."""
        self._replace_or_add(PromptSection(name="identity", content=text, cacheable=True))

    def set_memory_context(self, text: str) -> None:
        """Set the episodic memory context (dynamic, not cached)."""
        self._replace_or_add(PromptSection(name="memory_context", content=text, cacheable=False))

    def set_project_context(self, text: str, source_files: list[Path] | None = None) -> None:
        """Set the project context (static unless files change).

        *source_files* are added to the mtime/size manifest so the disk cache
        is invalidated automatically when a file is modified.
        """
        self._replace_or_add(PromptSection(name="project_context", content=text, cacheable=True))
        if source_files:
            self._source_files = list(source_files)

    def set_tool_schemas(self, tools: list[dict]) -> None:
        """Render tool definitions into a text section (static, cacheable)."""
        text = _format_tool_schemas(tools)
        self._replace_or_add(PromptSection(name="tool_schemas", content=text, cacheable=True))

    def set_guidance(self, model: str) -> None:
        """Inject model-specific guidance (static, cacheable)."""
        text = "" if _is_claude(model) else _NON_CLAUDE_GUIDANCE
        self._replace_or_add(PromptSection(name="guidance", content=text, cacheable=True))

    def set_shared_context(self, text: str) -> None:
        """Set shared Layer-3 context from parent (dynamic, not cached)."""
        self._replace_or_add(PromptSection(name="shared_context", content=text, cacheable=False))

    def set_learnings_context(self, text: str) -> None:
        """Set past learnings from Mímir (dynamic, not cached — NIU-588)."""
        self._replace_or_add(PromptSection(name="learnings_context", content=text, cacheable=False))

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Render all non-empty sections to a single string."""
        blocks = self._all_blocks_text()
        return _SECTION_SEP.join(b["text"] for b in blocks if b.get("text"))

    def render_blocks(self) -> list[dict]:
        """Render to Anthropic-format blocks with ``cache_control`` on static sections.

        Static sections carry ``"cache_control": {"type": "ephemeral"}`` so
        Claude can cache them as a stable prefix.  Dynamic sections have no
        cache directive.

        Uses the two-layer cache (LRU → disk) for the static core.
        """
        static_content = _SECTION_SEP.join(
            s.content for s in self._sections if s.cacheable and s.content
        )
        cache_key = _content_hash(static_content)
        manifest = _build_manifest(self._source_files)

        cached = self._cache_get(cache_key, manifest) if self._cache else None

        if cached is not None:
            dynamic_blocks = self._dynamic_blocks()
            return cached + dynamic_blocks

        static_blocks = [
            {
                "type": "text",
                "text": s.content,
                "cache_control": {"type": "ephemeral"},
            }
            for s in self._sections
            if s.cacheable and s.content
        ]

        if self._cache and static_blocks:
            self._cache.put(cache_key, manifest, static_blocks)

        dynamic_blocks = self._dynamic_blocks()
        return static_blocks + dynamic_blocks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replace_or_add(self, section: PromptSection) -> None:
        for idx, existing in enumerate(self._sections):
            if existing.name == section.name:
                self._sections[idx] = section
                return
        self._sections.append(section)

    def _dynamic_blocks(self) -> list[dict]:
        return [
            {"type": "text", "text": s.content}
            for s in self._sections
            if not s.cacheable and s.content
        ]

    def _all_blocks_text(self) -> list[dict]:
        return [{"type": "text", "text": s.content} for s in self._sections if s.content]

    def _cache_get(self, key: str, manifest: dict) -> list[dict] | None:
        if self._cache is None:
            return None
        return self._cache.get(key, manifest)


# ---------------------------------------------------------------------------
# PromptCache — two-layer (in-process LRU + disk snapshot)
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    blocks: list[dict]
    manifest: dict[str, tuple[float, int]]


class PromptCache:
    """Two-layer prompt cache: in-process LRU backed by a disk snapshot.

    The cache stores rendered *static* Anthropic-format blocks keyed by a
    content hash.  Each entry carries a mtime/size manifest so stale entries
    are invalidated when source files change.

    Thread safety: not thread-safe.  Use one cache per asyncio event loop.
    """

    def __init__(
        self,
        max_entries: int = 16,
        cache_dir: str | Path = "",
    ) -> None:
        self._max_entries = max_entries
        self._lru: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_dir = Path(cache_dir).expanduser() if cache_dir else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, manifest: dict) -> list[dict] | None:
        """Return cached blocks if available and still valid, else None."""
        entry = self._lru_get(key)
        if entry is not None and _manifest_valid(entry.manifest, manifest):
            return entry.blocks

        entry = self._disk_get(key)
        if entry is not None and _manifest_valid(entry.manifest, manifest):
            self._lru_put(key, entry)
            return entry.blocks

        return None

    def put(self, key: str, manifest: dict, blocks: list[dict]) -> None:
        """Store blocks in both the LRU and the disk snapshot."""
        entry = _CacheEntry(blocks=blocks, manifest=manifest)
        self._lru_put(key, entry)
        self._disk_put(key, entry)

    def clear(self) -> None:
        """Clear the in-process LRU (disk snapshots are not removed)."""
        self._lru.clear()

    # ------------------------------------------------------------------
    # LRU helpers
    # ------------------------------------------------------------------

    def _lru_get(self, key: str) -> _CacheEntry | None:
        if key not in self._lru:
            return None
        self._lru.move_to_end(key)
        return self._lru[key]

    def _lru_put(self, key: str, entry: _CacheEntry) -> None:
        self._lru[key] = entry
        self._lru.move_to_end(key)
        while len(self._lru) > self._max_entries:
            self._lru.popitem(last=False)

    # ------------------------------------------------------------------
    # Disk helpers
    # ------------------------------------------------------------------

    def _disk_path(self, key: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{key}.json"

    def _disk_get(self, key: str) -> _CacheEntry | None:
        path = self._disk_path(key)
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            blocks = raw["blocks"]
            manifest = {k: tuple(v) for k, v in raw.get("manifest", {}).items()}
            return _CacheEntry(blocks=blocks, manifest=manifest)
        except Exception:
            return None

    def _disk_put(self, key: str, entry: _CacheEntry) -> None:
        path = self._disk_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"blocks": entry.blocks, "manifest": entry.manifest}
            path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            logger.debug("PromptCache disk write failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _build_manifest(files: list[Path]) -> dict[str, tuple[float, int]]:
    """Return {path_str: (mtime, size)} for each file that exists."""
    result: dict[str, tuple[float, int]] = {}
    for p in files:
        try:
            stat = p.stat()
            result[str(p)] = (stat.st_mtime, stat.st_size)
        except OSError:
            pass
    return result


def _manifest_valid(
    stored: dict[str, tuple[float, int]],
    current: dict[str, tuple[float, int]],
) -> bool:
    """Return True when all files in *stored* still match *current*."""
    for path, (mtime, size) in stored.items():
        cur = current.get(path)
        if cur is None:
            return False
        if cur != (mtime, size):
            return False
    return True


_CLAUDE_MODEL_PREFIXES = ("claude-",)


def _is_claude(model: str) -> bool:
    lower = model.lower()
    return any(lower.startswith(p) for p in _CLAUDE_MODEL_PREFIXES)


def build_initiative_prompt(task: AgentTask) -> str:
    """Build the synthetic user message for a drive-loop initiative task.

    The returned string is passed directly to ``agent.run_turn()`` in place
    of a human message.  The agent should default to silent output and only
    produce a response starting with ``[SURFACE]`` if something requires
    attention.
    """
    return (
        f"[INITIATIVE TASK — triggered by: {task.triggered_by}]\n"
        f"Title: {task.title}\n"
        "\n"
        "You are running autonomously. No human sent this message.\n"
        "\n"
        "Context:\n"
        "<initiative_context>\n"
        f"{task.initiative_context}\n"
        "</initiative_context>\n"
        "\n"
        "Instructions:\n"
        "1. Complete the task described above using available tools.\n"
        "2. When finished, output your ---outcome--- block and STOP.\n"
        "3. Do NOT continue calling tools after producing the outcome block.\n"
        "4. Keep your work focused and concise. Avoid unnecessary iterations.\n"
        "\n"
        "Output:\n"
        "- Default to SILENT unless something requires human attention.\n"
        "- If escalation needed, prefix with [SURFACE].\n"
        "- Always end with your ---outcome--- block when the task is complete."
    )


def _format_tool_schemas(tools: list[dict]) -> str:
    """Render tool definitions to a readable text block."""
    if not tools:
        return ""
    lines = ["## Available Tools", ""]
    for tool in tools:
        name = tool.get("name", "unknown")
        desc = tool.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    return "\n".join(lines)

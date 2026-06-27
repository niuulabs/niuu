"""File operation tools for Ravn agents."""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path

from ravn.adapters.tools.file_security import (
    DEFAULT_BINARY_CHECK_BYTES,
    DEFAULT_MAX_READ_BYTES,
    DEFAULT_MAX_WRITE_BYTES,
    PathSecurityError,
    is_binary,
    resolve_safe,
)
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION_READ = "file:read"
_PERMISSION_WRITE = "file:write"


def _unified_diff(original: str, updated: str, path: str) -> str | None:
    """Return a unified diff of *original* → *updated*, or None if identical."""
    if original == updated:
        return None
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )
    if not diff_lines:
        return None
    return "".join(diff_lines)


class ReadFileTool(ToolPort):
    """Read a file's contents with 1-based line numbers."""

    def __init__(
        self,
        workspace: Path,
        max_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> None:
        self._workspace = workspace
        self._max_bytes = max_bytes

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. "
            "Returns lines prefixed with their 1-based line number. "
            "Use offset and limit to page through large files."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read."},
                "offset": {
                    "type": "integer",
                    "description": "1-based line number to start reading from (default: 1).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return.",
                },
            },
            "required": ["path"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        path_str = input.get("path", "")
        offset = input.get("offset", 1)
        limit = input.get("limit")

        try:
            safe_path = resolve_safe(path_str, self._workspace)
        except PathSecurityError as exc:
            guidance = _workspace_path_guidance(self._workspace)
            return ToolResult(
                tool_call_id="",
                content=f"{exc}{guidance}",
                is_error=True,
            )

        if not safe_path.exists():
            return ToolResult(
                tool_call_id="",
                content=f"File not found: '{path_str}'",
                is_error=True,
            )

        if not safe_path.is_file():
            return ToolResult(
                tool_call_id="",
                content=f"Not a file: '{path_str}'",
                is_error=True,
            )

        size = safe_path.stat().st_size
        if size > self._max_bytes:
            return ToolResult(
                tool_call_id="",
                content=f"File too large: {size} bytes (limit {self._max_bytes})",
                is_error=True,
            )

        try:
            text = safe_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(tool_call_id="", content=f"Read error: {exc}", is_error=True)

        lines = text.splitlines(keepends=True)
        start = max(0, (offset or 1) - 1)
        slice_ = lines[start:] if limit is None else lines[start : start + limit]

        numbered = "".join(f"{start + idx + 1}\t{line}" for idx, line in enumerate(slice_))
        return ToolResult(tool_call_id="", content=numbered)


def _workspace_path_guidance(workspace: Path) -> str:
    try:
        candidates = sorted(
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".yaml", ".yml"}
        )[:10]
    except OSError:
        candidates = []
    if not candidates:
        return " Use paths relative to the configured workspace root."
    return (
        " Use paths relative to the configured workspace root. "
        f"Known workspace files include: {', '.join(candidates)}."
    )


class WriteFileTool(ToolPort):
    """Create or overwrite a file within the workspace."""

    def __init__(
        self,
        workspace: Path,
        max_bytes: int = DEFAULT_MAX_WRITE_BYTES,
        binary_check_bytes: int = DEFAULT_BINARY_CHECK_BYTES,
    ) -> None:
        self._workspace = workspace
        self._max_bytes = max_bytes
        self._binary_check_bytes = binary_check_bytes

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Create or overwrite a file with the given text content."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to write."},
                "content": {"type": "string", "description": "Text content to write."},
            },
            "required": ["path", "content"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_WRITE

    def diff_preview(self, input: dict) -> str | None:
        path_str = input.get("path", "")
        new_content = input.get("content", "")
        try:
            safe_path = resolve_safe(path_str, self._workspace)
        except PathSecurityError:
            return None
        if not safe_path.exists() or not safe_path.is_file():
            return None
        try:
            original = safe_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        rel = str(safe_path.relative_to(self._workspace))
        return _unified_diff(original, new_content, rel)

    async def execute(self, input: dict) -> ToolResult:
        path_str = input.get("path", "")
        content = input.get("content", "")

        try:
            safe_path = resolve_safe(path_str, self._workspace)
        except PathSecurityError as exc:
            return ToolResult(tool_call_id="", content=str(exc), is_error=True)

        encoded = content.encode("utf-8")

        if len(encoded) > self._max_bytes:
            return ToolResult(
                tool_call_id="",
                content=f"Content too large: {len(encoded)} bytes (limit {self._max_bytes})",
                is_error=True,
            )

        if is_binary(encoded, self._binary_check_bytes):
            return ToolResult(
                tool_call_id="",
                content="Binary content detected; refusing to write",
                is_error=True,
            )

        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(tool_call_id="", content=f"Write error: {exc}", is_error=True)

        return ToolResult(tool_call_id="", content=f"Written: '{safe_path}'")


class EditFileTool(ToolPort):
    """Exact string replacement in a file."""

    def __init__(
        self,
        workspace: Path,
        max_bytes: int = DEFAULT_MAX_WRITE_BYTES,
        binary_check_bytes: int = DEFAULT_BINARY_CHECK_BYTES,
    ) -> None:
        self._workspace = workspace
        self._max_bytes = max_bytes
        self._binary_check_bytes = binary_check_bytes

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace an exact string in a file. "
            "Fails if old_string is not found or appears more than once "
            "(unless replace_all is set)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find and replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "Replace all occurrences instead of requiring uniqueness (default: false)."
                    ),
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_WRITE

    def diff_preview(self, input: dict) -> str | None:
        path_str = input.get("path", "")
        old_string = input.get("old_string", "")
        new_string = input.get("new_string", "")
        replace_all = input.get("replace_all", False)
        try:
            safe_path = resolve_safe(path_str, self._workspace)
        except PathSecurityError:
            return None
        if not safe_path.exists() or not safe_path.is_file():
            return None
        try:
            original = safe_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if old_string not in original:
            return None
        if not replace_all and original.count(old_string) > 1:
            return None
        if replace_all:
            updated = original.replace(old_string, new_string)
        else:
            updated = original.replace(old_string, new_string, 1)
        rel = str(safe_path.relative_to(self._workspace))
        return _unified_diff(original, updated, rel)

    async def execute(self, input: dict) -> ToolResult:
        path_str = input.get("path", "")
        old_string = input.get("old_string", "")
        new_string = input.get("new_string", "")
        replace_all = input.get("replace_all", False)

        try:
            safe_path = resolve_safe(path_str, self._workspace)
        except PathSecurityError as exc:
            return ToolResult(tool_call_id="", content=str(exc), is_error=True)

        if not safe_path.exists():
            return ToolResult(
                tool_call_id="",
                content=f"File not found: '{path_str}'",
                is_error=True,
            )

        if not safe_path.is_file():
            return ToolResult(
                tool_call_id="",
                content=f"Not a file: '{path_str}'",
                is_error=True,
            )

        try:
            original = safe_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(tool_call_id="", content=f"Read error: {exc}", is_error=True)

        count = original.count(old_string)

        if count == 0:
            return ToolResult(
                tool_call_id="",
                content=f"old_string not found in '{path_str}'",
                is_error=True,
            )

        if not replace_all and count > 1:
            return ToolResult(
                tool_call_id="",
                content=(
                    f"old_string found {count} times in '{path_str}'; "
                    "use replace_all=true to replace all occurrences"
                ),
                is_error=True,
            )

        updated = original.replace(old_string, new_string)
        encoded = updated.encode("utf-8")

        if len(encoded) > self._max_bytes:
            return ToolResult(
                tool_call_id="",
                content=f"Result too large: {len(encoded)} bytes (limit {self._max_bytes})",
                is_error=True,
            )

        if is_binary(encoded, self._binary_check_bytes):
            return ToolResult(
                tool_call_id="",
                content="Binary content detected after replacement; refusing to write",
                is_error=True,
            )

        try:
            safe_path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult(tool_call_id="", content=f"Write error: {exc}", is_error=True)

        replacements = count if replace_all else 1
        return ToolResult(
            tool_call_id="",
            content=f"Replaced {replacements} occurrence(s) in '{safe_path}'",
        )


class GlobSearchTool(ToolPort):
    """Find files by glob pattern within the workspace."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "glob_search"

    @property
    def description(self) -> str:
        return (
            "Find files by glob pattern. "
            "Returns paths relative to the workspace root, one per line."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts').",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory for the search (default: workspace root).",
                },
            },
            "required": ["pattern"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        pattern = input.get("pattern", "")
        base_str = input.get("path")

        base = self._workspace
        if base_str:
            try:
                base = resolve_safe(base_str, self._workspace)
            except PathSecurityError as exc:
                return ToolResult(tool_call_id="", content=str(exc), is_error=True)

            if not base.is_dir():
                return ToolResult(
                    tool_call_id="",
                    content=f"Not a directory: '{base_str}'",
                    is_error=True,
                )

        matches = sorted(
            str(p.relative_to(self._workspace)) for p in base.glob(pattern) if p.is_file()
        )
        return ToolResult(tool_call_id="", content="\n".join(matches) if matches else "")


class GrepSearchTool(ToolPort):
    """Search file contents by regular expression."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "grep_search"

    @property
    def description(self) -> str:
        return (
            "Search file contents by regular expression. "
            "Returns matching lines formatted as path:line_number:line_content."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: workspace root).",
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Glob pattern to filter files when searching a directory (default: **/*)."
                    ),
                },
            },
            "required": ["pattern"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        pattern = input.get("pattern", "")
        path_str = input.get("path")
        glob_pattern = input.get("glob", "**/*")

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(
                tool_call_id="",
                content=f"Invalid regex pattern: {exc}",
                is_error=True,
            )

        if path_str:
            try:
                candidate = resolve_safe(path_str, self._workspace)
            except PathSecurityError as exc:
                return ToolResult(tool_call_id="", content=str(exc), is_error=True)

            if candidate.is_file():
                return self._grep_file(candidate, regex)

            if not candidate.is_dir():
                return ToolResult(
                    tool_call_id="",
                    content=f"Path not found: '{path_str}'",
                    is_error=True,
                )

            base = candidate
        else:
            base = self._workspace

        results: list[str] = []
        for filepath in sorted(base.glob(glob_pattern)):
            if not filepath.is_file():
                continue
            results.extend(self._grep_file_lines(filepath, regex))

        return ToolResult(tool_call_id="", content="\n".join(results) if results else "")

    def _grep_file(self, filepath: Path, regex: re.Pattern) -> ToolResult:  # type: ignore[type-arg]
        lines = self._grep_file_lines(filepath, regex)
        return ToolResult(tool_call_id="", content="\n".join(lines) if lines else "")

    def _grep_file_lines(self, filepath: Path, regex: re.Pattern) -> list[str]:  # type: ignore[type-arg]
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        rel = filepath.relative_to(self._workspace)
        return [
            f"{rel}:{lineno}:{line}"
            for lineno, line in enumerate(text.splitlines(), start=1)
            if regex.search(line)
        ]

"""Unit tests for file tools — diff preview, write, edit, glob, and grep."""

from __future__ import annotations

import io
from pathlib import Path

from niuu.mesh.events import MeshEvent, MeshEventType
from ravn.adapters.cli_channel import CliChannel
from ravn.adapters.tools.file_tools import (
    EditFileTool,
    GlobSearchTool,
    GrepSearchTool,
    ReadFileTool,
    WriteFileTool,
    _unified_diff,
)

# ---------------------------------------------------------------------------
# _unified_diff helper
# ---------------------------------------------------------------------------


class TestUnifiedDiff:
    def test_returns_none_when_identical(self) -> None:
        assert _unified_diff("hello\n", "hello\n", "file.py") is None

    def test_returns_diff_string_when_different(self) -> None:
        result = _unified_diff("old\n", "new\n", "file.py")
        assert result is not None
        assert "-old" in result
        assert "+new" in result

    def test_diff_contains_path(self) -> None:
        result = _unified_diff("a\n", "b\n", "src/ravn/agent.py")
        assert result is not None
        assert "src/ravn/agent.py" in result

    def test_returns_none_for_empty_to_empty(self) -> None:
        assert _unified_diff("", "", "f.py") is None

    def test_multiline_diff(self) -> None:
        original = "line1\nline2\nline3\n"
        updated = "line1\nLINE2\nline3\n"
        result = _unified_diff(original, updated, "f.py")
        assert result is not None
        assert "-line2" in result
        assert "+LINE2" in result

    def test_returns_string_not_lines_list(self) -> None:
        result = _unified_diff("a\n", "b\n", "f.py")
        assert isinstance(result, str)

    def test_adding_line(self) -> None:
        result = _unified_diff("line1\n", "line1\nline2\n", "f.py")
        assert result is not None
        assert "+line2" in result

    def test_removing_line(self) -> None:
        result = _unified_diff("line1\nline2\n", "line1\n", "f.py")
        assert result is not None
        assert "-line2" in result


# ---------------------------------------------------------------------------
# WriteFileTool.diff_preview
# ---------------------------------------------------------------------------


class TestWriteFileToolDiffPreview:
    def test_returns_none_for_new_file(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path)
        path = str(tmp_path / "newfile.py")
        result = tool.diff_preview({"path": path, "content": "hello\n"})
        assert result is None

    def test_returns_none_on_security_violation(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path)
        # Path resolves outside tmp_path workspace
        result = tool.diff_preview({"path": "/tmp/escape.py", "content": "x"})
        assert result is None

    def test_returns_none_when_content_identical(self, tmp_path: Path) -> None:
        f = tmp_path / "same.py"
        f.write_text("hello\n", encoding="utf-8")
        tool = WriteFileTool(workspace=tmp_path)
        result = tool.diff_preview({"path": str(f), "content": "hello\n"})
        assert result is None

    def test_returns_diff_when_content_differs(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.py"
        f.write_text("def run(self):\n    pass\n", encoding="utf-8")
        tool = WriteFileTool(workspace=tmp_path)
        result = tool.diff_preview({"path": str(f), "content": "def run(self, ctx):\n    pass\n"})
        assert result is not None
        assert "-def run(self):" in result
        assert "+def run(self, ctx):" in result

    def test_diff_contains_relative_path(self, tmp_path: Path) -> None:
        subdir = tmp_path / "src"
        subdir.mkdir()
        f = subdir / "foo.py"
        f.write_text("old\n", encoding="utf-8")
        tool = WriteFileTool(workspace=tmp_path)
        result = tool.diff_preview({"path": str(f), "content": "new\n"})
        assert result is not None
        assert "src/foo.py" in result

    def test_returns_none_when_path_is_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "adir"
        d.mkdir()
        tool = WriteFileTool(workspace=tmp_path)
        result = tool.diff_preview({"path": str(d), "content": "x"})
        assert result is None


# ---------------------------------------------------------------------------
# EditFileTool.diff_preview
# ---------------------------------------------------------------------------


class TestEditFileToolDiffPreview:
    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview(
            {"path": str(tmp_path / "missing.py"), "old_string": "x", "new_string": "y"}
        )
        assert result is None

    def test_returns_none_on_security_violation(self, tmp_path: Path) -> None:
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview({"path": "/tmp/evil.py", "old_string": "x", "new_string": "y"})
        assert result is None

    def test_returns_none_when_old_string_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("hello world\n", encoding="utf-8")
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview(
            {"path": str(f), "old_string": "missing", "new_string": "replacement"}
        )
        assert result is None

    def test_returns_diff_for_single_replacement(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\n", encoding="utf-8")
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview(
            {"path": str(f), "old_string": "def foo():", "new_string": "def bar():"}
        )
        assert result is not None
        assert "-def foo():" in result
        assert "+def bar():" in result

    def test_returns_diff_for_replace_all(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("x = 1\nx = 1\n", encoding="utf-8")
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview(
            {
                "path": str(f),
                "old_string": "x = 1",
                "new_string": "x = 2",
                "replace_all": True,
            }
        )
        assert result is not None
        assert "+x = 2" in result

    def test_returns_none_when_multiple_occurrences_without_replace_all(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "code.py"
        f.write_text("a\na\n", encoding="utf-8")
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview(
            {"path": str(f), "old_string": "a", "new_string": "b", "replace_all": False}
        )
        # execute() would reject this case — diff_preview must not show a misleading preview
        assert result is None

    def test_diff_contains_relative_path(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("original\n", encoding="utf-8")
        tool = EditFileTool(workspace=tmp_path)
        result = tool.diff_preview(
            {"path": str(f), "old_string": "original", "new_string": "updated"}
        )
        assert result is not None
        assert "mod.py" in result


# ---------------------------------------------------------------------------
# MeshEvent.tool_start — diff in payload
# ---------------------------------------------------------------------------

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"


class TestMeshEventToolStart:
    def test_no_diff_key_when_diff_is_none(self) -> None:
        event = MeshEvent.tool_start(
            _SRC, "write_file", {"path": "f.py", "content": "x"}, _CID, _SID
        )
        assert "diff" not in event.payload

    def test_diff_key_present_when_diff_provided(self) -> None:
        event = MeshEvent.tool_start(
            _SRC,
            "write_file",
            {"path": "f.py"},
            _CID,
            _SID,
            diff="--- f.py\n+++ f.py\n@@ -1 +1 @@\n-old\n+new\n",
        )
        assert "diff" in event.payload
        assert event.payload["diff"] == "--- f.py\n+++ f.py\n@@ -1 +1 @@\n-old\n+new\n"

    def test_input_always_in_payload(self) -> None:
        event = MeshEvent.tool_start(_SRC, "read_file", {"path": "x.py"}, _CID, _SID)
        assert event.payload["input"] == {"path": "x.py"}

    def test_event_type_is_tool_start(self) -> None:
        event = MeshEvent.tool_start(_SRC, "t", {}, _CID, _SID)
        assert event.type == MeshEventType.TOOL_START

    def test_tool_name_in_payload(self) -> None:
        event = MeshEvent.tool_start(_SRC, "write_file", {}, _CID, _SID)
        assert event.payload["tool_name"] == "write_file"


# ---------------------------------------------------------------------------
# CliChannel — diff rendering
# ---------------------------------------------------------------------------


class TestCliChannelDiffRendering:
    async def test_renders_diff_separator_and_content(self) -> None:
        buf = io.StringIO()
        cli = CliChannel(file=buf)
        event = MeshEvent.tool_start(
            _SRC,
            "write_file",
            {"path": "f.py"},
            _CID,
            _SID,
            diff="--- f.py\n+++ f.py\n@@ -1 +1 @@\n-old\n+new\n",
        )
        await cli.emit(event)
        output = buf.getvalue()
        assert "─" * 33 in output
        assert "-old" in output
        assert "+new" in output

    async def test_no_separator_when_no_diff(self) -> None:
        buf = io.StringIO()
        cli = CliChannel(file=buf)
        event = MeshEvent.tool_start(_SRC, "read_file", {"path": "f.py"}, _CID, _SID)
        await cli.emit(event)
        output = buf.getvalue()
        assert "─" not in output

    async def test_tool_name_rendered_with_rotation_symbol(self) -> None:
        buf = io.StringIO()
        cli = CliChannel(file=buf)
        event = MeshEvent.tool_start(_SRC, "write_file", {"path": "f.py"}, _CID, _SID)
        await cli.emit(event)
        output = buf.getvalue()
        assert "⟳" in output
        assert "write_file" in output

    async def test_two_separators_around_diff(self) -> None:
        buf = io.StringIO()
        cli = CliChannel(file=buf)
        diff = "--- f.py\n+++ f.py\n@@ -1 +1 @@\n-a\n+b\n"
        event = MeshEvent.tool_start(_SRC, "write_file", {}, _CID, _SID, diff=diff)
        await cli.emit(event)
        output = buf.getvalue()
        sep = "─" * 33
        assert output.count(sep) == 2


# ---------------------------------------------------------------------------
# WriteFileTool.execute (functional correctness)
# ---------------------------------------------------------------------------


class TestWriteFileToolExecute:
    async def test_creates_new_file(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path)
        path = str(tmp_path / "new.txt")
        result = await tool.execute({"path": path, "content": "hello"})
        assert not result.is_error
        assert (tmp_path / "new.txt").read_text() == "hello"

    async def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "existing.txt"
        f.write_text("old content")
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute({"path": str(f), "content": "new content"})
        assert not result.is_error
        assert f.read_text() == "new content"

    async def test_security_violation_returns_error(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path)
        result = await tool.execute({"path": "/tmp/escape.txt", "content": "x"})
        assert result.is_error

    async def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path)
        nested = tmp_path / "a" / "b" / "c.txt"
        result = await tool.execute({"path": str(nested), "content": "deep"})
        assert not result.is_error
        assert nested.read_text() == "deep"

    async def test_content_too_large_returns_error(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path, max_bytes=10)
        result = await tool.execute({"path": str(tmp_path / "big.txt"), "content": "x" * 20})
        assert result.is_error
        assert "too large" in result.content.lower()

    async def test_binary_content_returns_error(self, tmp_path: Path) -> None:
        tool = WriteFileTool(workspace=tmp_path)
        binary = "\x00" * 100
        result = await tool.execute({"path": str(tmp_path / "bin.txt"), "content": binary})
        assert result.is_error
        assert "binary" in result.content.lower()


# ---------------------------------------------------------------------------
# EditFileTool.execute (functional correctness)
# ---------------------------------------------------------------------------


class TestEditFileToolExecute:
    async def test_replaces_unique_occurrence(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\n")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            {"path": str(f), "old_string": "def foo():", "new_string": "def bar():"}
        )
        assert not result.is_error
        assert f.read_text() == "def bar():\n    pass\n"

    async def test_error_when_old_string_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("hello world\n")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute({"path": str(f), "old_string": "missing", "new_string": "x"})
        assert result.is_error

    async def test_error_when_multiple_occurrences_without_replace_all(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "code.py"
        f.write_text("x\nx\n")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute({"path": str(f), "old_string": "x", "new_string": "y"})
        assert result.is_error
        assert "replace_all" in result.content

    async def test_replace_all_replaces_every_occurrence(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("x\nx\n")
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            {"path": str(f), "old_string": "x", "new_string": "y", "replace_all": True}
        )
        assert not result.is_error
        assert f.read_text() == "y\ny\n"

    async def test_error_on_security_violation(self, tmp_path: Path) -> None:
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute({"path": "/tmp/evil.py", "old_string": "x", "new_string": "y"})
        assert result.is_error

    async def test_error_when_file_not_found(self, tmp_path: Path) -> None:
        tool = EditFileTool(workspace=tmp_path)
        result = await tool.execute(
            {"path": str(tmp_path / "ghost.py"), "old_string": "x", "new_string": "y"}
        )
        assert result.is_error


# ---------------------------------------------------------------------------
# ReadFileTool.execute (functional correctness)
# ---------------------------------------------------------------------------


class TestReadFileToolExecute:
    async def test_reads_file_with_line_numbers(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\nline3\n")
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute({"path": str(f)})
        assert not result.is_error
        assert "1\tline1" in result.content
        assert "2\tline2" in result.content

    async def test_error_when_file_not_found(self, tmp_path: Path) -> None:
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute({"path": str(tmp_path / "missing.txt")})
        assert result.is_error

    async def test_security_violation_returns_error(self, tmp_path: Path) -> None:
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute({"path": "/tmp/secret.txt"})
        assert result.is_error

    async def test_offset_and_limit(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
        tool = ReadFileTool(workspace=tmp_path)
        result = await tool.execute({"path": str(f), "offset": 3, "limit": 2})
        assert not result.is_error
        assert "3\tline3" in result.content
        assert "4\tline4" in result.content
        assert "line1" not in result.content
        assert "line5" not in result.content


# ---------------------------------------------------------------------------
# GlobSearchTool.execute
# ---------------------------------------------------------------------------


class TestGlobSearchToolExecute:
    async def test_finds_matching_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("x")
        (tmp_path / "c.txt").write_text("x")
        tool = GlobSearchTool(workspace=tmp_path)
        result = await tool.execute({"pattern": "*.py"})
        assert not result.is_error
        assert "a.py" in result.content
        assert "b.py" in result.content
        assert "c.txt" not in result.content

    async def test_empty_result_when_no_match(self, tmp_path: Path) -> None:
        tool = GlobSearchTool(workspace=tmp_path)
        result = await tool.execute({"pattern": "*.rs"})
        assert not result.is_error
        assert result.content == ""

    async def test_security_violation_on_base_dir(self, tmp_path: Path) -> None:
        tool = GlobSearchTool(workspace=tmp_path)
        result = await tool.execute({"pattern": "*.py", "path": "/tmp/outside"})
        assert result.is_error


# ---------------------------------------------------------------------------
# GrepSearchTool.execute
# ---------------------------------------------------------------------------


class TestGrepSearchToolExecute:
    async def test_finds_matching_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("def foo():\n    pass\ndef bar():\n    return 1\n")
        tool = GrepSearchTool(workspace=tmp_path)
        result = await tool.execute({"pattern": "def ", "path": str(f)})
        assert not result.is_error
        assert "def foo" in result.content
        assert "def bar" in result.content

    async def test_invalid_regex_returns_error(self, tmp_path: Path) -> None:
        tool = GrepSearchTool(workspace=tmp_path)
        result = await tool.execute({"pattern": "[invalid"})
        assert result.is_error

    async def test_empty_result_when_no_match(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text("hello world\n")
        tool = GrepSearchTool(workspace=tmp_path)
        result = await tool.execute({"pattern": "xyz_nomatch", "path": str(f)})
        assert not result.is_error
        assert result.content == ""

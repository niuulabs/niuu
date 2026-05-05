"""Tests for LocalGitService adapter — parsing and subprocess logic."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from volundr.adapters.outbound.local_git import (
    LocalGitService,
    parse_log,
    parse_numstat,
    parse_pr_view,
    resolve_repo_dir,
)

# ---------------------------------------------------------------------------
# Pure parsing tests
# ---------------------------------------------------------------------------


class TestParseNumstat:
    """Tests for parse_numstat (git diff --numstat output)."""

    def test_simple_output(self):
        raw = "10\t5\tsrc/main.py\n3\t1\tREADME.md\n"
        result = parse_numstat(raw)
        assert result == [
            {"path": "src/main.py", "additions": 10, "deletions": 5},
            {"path": "README.md", "additions": 3, "deletions": 1},
        ]

    def test_binary_file(self):
        raw = "-\t-\timage.png\n2\t0\tsrc/app.py\n"
        result = parse_numstat(raw)
        assert result == [
            {"path": "image.png", "additions": 0, "deletions": 0},
            {"path": "src/app.py", "additions": 2, "deletions": 0},
        ]

    def test_empty_output(self):
        assert parse_numstat("") == []
        assert parse_numstat("   \n  ") == []

    def test_single_file(self):
        raw = "42\t7\tvolundr/config.py"
        result = parse_numstat(raw)
        assert len(result) == 1
        assert result[0]["path"] == "volundr/config.py"
        assert result[0]["additions"] == 42
        assert result[0]["deletions"] == 7

    def test_malformed_line_skipped(self):
        raw = "not_valid\n10\t5\tgood.py\n"
        result = parse_numstat(raw)
        assert len(result) == 1
        assert result[0]["path"] == "good.py"

    def test_path_with_spaces(self):
        raw = "1\t2\tpath with spaces/file name.py\n"
        result = parse_numstat(raw)
        assert result[0]["path"] == "path with spaces/file name.py"


class TestParseLog:
    """Tests for parse_log (git log --pretty=format output)."""

    def test_simple_log(self):
        raw = (
            "abc1234\x00feat: add feature\x00abc1234567890abcdef1234567890abcdef12345678\n"
            "def5678\x00fix: bug fix\x00def5678901234abcdef5678901234abcdef56789012\n"
        )
        result = parse_log(raw)
        assert len(result) == 2
        assert result[0] == {
            "short_hash": "abc1234",
            "message": "feat: add feature",
            "hash": "abc1234567890abcdef1234567890abcdef12345678",
        }
        assert result[1]["short_hash"] == "def5678"

    def test_empty_output(self):
        assert parse_log("") == []
        assert parse_log("\n\n") == []

    def test_message_with_pipe(self):
        """Pipe characters in commit messages are preserved correctly."""
        raw = "abc\x00feat: some | thing\x00abc123fullhash"
        result = parse_log(raw)
        assert len(result) == 1
        assert result[0]["message"] == "feat: some | thing"
        assert result[0]["hash"] == "abc123fullhash"

    def test_malformed_line_skipped(self):
        raw = "only_one_field\nabc\x00msg\x00hash123\n"
        result = parse_log(raw)
        assert len(result) == 1
        assert result[0]["short_hash"] == "abc"


class TestParsePrView:
    """Tests for parse_pr_view (gh pr view --json output)."""

    def test_full_pr_data(self):
        data = {
            "number": 42,
            "url": "https://github.com/org/repo/pull/42",
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [
                {"name": "tests", "conclusion": "SUCCESS"},
                {"name": "lint", "conclusion": "FAILURE"},
            ],
        }
        result = parse_pr_view(json.dumps(data))
        assert result is not None
        assert result["number"] == 42
        assert result["url"] == "https://github.com/org/repo/pull/42"
        assert result["state"] == "OPEN"
        assert result["mergeable"] == "MERGEABLE"
        assert len(result["checks"]) == 2
        assert result["checks"][0] == {"name": "tests", "status": "SUCCESS"}
        assert result["checks"][1] == {"name": "lint", "status": "FAILURE"}

    def test_no_checks(self):
        data = {
            "number": 1,
            "url": "https://github.com/org/repo/pull/1",
            "state": "OPEN",
            "mergeable": "UNKNOWN",
        }
        result = parse_pr_view(json.dumps(data))
        assert result is not None
        assert result["checks"] == []

    def test_invalid_json(self):
        assert parse_pr_view("not json") is None
        assert parse_pr_view("") is None

    def test_check_with_context_field(self):
        """Status checks may use 'context' instead of 'name'."""
        data = {
            "number": 5,
            "url": "",
            "state": "OPEN",
            "mergeable": "UNKNOWN",
            "statusCheckRollup": [
                {"context": "ci/circleci", "state": "success"},
            ],
        }
        result = parse_pr_view(json.dumps(data))
        assert result["checks"][0]["name"] == "ci/circleci"
        assert result["checks"][0]["status"] == "success"

    def test_missing_fields_use_defaults(self):
        data = {"number": 10}
        result = parse_pr_view(json.dumps(data))
        assert result is not None
        assert result["url"] == ""
        assert result["state"] == ""
        assert result["mergeable"] == "UNKNOWN"


class TestResolveRepoDir:
    """Tests for workspace repo-root resolution."""

    def test_prefers_nested_repo_checkout_when_present(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        nested_repo = workspace / "repo" / ".git"
        nested_repo.mkdir(parents=True)

        assert resolve_repo_dir(str(workspace)) == str(workspace / "repo")

    def test_falls_back_to_workspace_root(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        assert resolve_repo_dir(str(workspace)) == str(workspace)


# ---------------------------------------------------------------------------
# LocalGitService subprocess tests (mocked)
# ---------------------------------------------------------------------------


class TestLocalGitServiceDiffFiles:
    """Tests for LocalGitService.diff_files."""

    @pytest.mark.asyncio
    async def test_returns_parsed_output(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, "10\t5\tsrc/main.py\n", ""),
        ) as mock_run:
            result = await service.diff_files("/workspace")
        assert len(result) == 1
        assert result[0]["path"] == "src/main.py"
        assert mock_run.await_args.kwargs["cwd"] == "/workspace"

    @pytest.mark.asyncio
    async def test_prefers_nested_repo_directory(self, tmp_path: Path):
        service = LocalGitService()
        workspace = tmp_path / "workspace"
        (workspace / "repo" / ".git").mkdir(parents=True)
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, "10\t5\tsrc/main.py\n", ""),
        ) as mock_run:
            await service.diff_files(str(workspace))
        assert mock_run.await_args.kwargs["cwd"] == str(workspace / "repo")

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(128, "", "fatal: not a git repository"),
        ):
            result = await service.diff_files("/workspace")
        assert result == []


class TestLocalGitServiceFileDiff:
    """Tests for LocalGitService.file_diff."""

    @pytest.mark.asyncio
    async def test_returns_diff_text(self):
        service = LocalGitService()
        diff_output = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n"
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, diff_output, ""),
        ) as mock_run:
            result = await service.file_diff("/workspace", "f.py", "main")
        assert result == diff_output
        assert mock_run.await_args.kwargs["cwd"] == "/workspace"

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_diff(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, "  \n", ""),
        ):
            result = await service.file_diff("/workspace", "f.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(1, "", "error"),
        ):
            result = await service.file_diff("/workspace", "f.py")
        assert result is None


class TestLocalGitServiceCommitLog:
    """Tests for LocalGitService.commit_log."""

    @pytest.mark.asyncio
    async def test_returns_parsed_commits(self):
        service = LocalGitService()
        raw = "abc\x00feat: add\x00abc123full\ndef\x00fix: bug\x00def456full\n"
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, raw, ""),
        ):
            result = await service.commit_log("/workspace")
        assert len(result) == 2
        assert result[0]["short_hash"] == "abc"

    @pytest.mark.asyncio
    async def test_passes_since_flag(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, "", ""),
        ) as mock_run:
            await service.commit_log("/workspace", since="2025-01-01")
        args = mock_run.call_args
        cmd_args = args[0]
        assert any("--since=2025-01-01" in str(a) for a in cmd_args)
        assert mock_run.await_args.kwargs["cwd"] == "/workspace"

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(128, "", "fatal"),
        ):
            result = await service.commit_log("/workspace")
        assert result == []


class TestLocalGitServicePrStatus:
    """Tests for LocalGitService.pr_status."""

    @pytest.mark.asyncio
    async def test_returns_parsed_pr(self):
        service = LocalGitService()
        data = json.dumps(
            {
                "number": 42,
                "url": "https://github.com/org/repo/pull/42",
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "statusCheckRollup": [],
            }
        )
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, data, ""),
        ) as mock_run:
            result = await service.pr_status("/workspace")
        assert result is not None
        assert result["number"] == 42
        assert mock_run.await_args.kwargs["cwd"] == "/workspace"

    @pytest.mark.asyncio
    async def test_returns_none_when_gh_not_installed(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(127, "", "gh: command not found"),
        ):
            result = await service.pr_status("/workspace")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_pr(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(1, "", "no pull requests found"),
        ):
            result = await service.pr_status("/workspace")
        assert result is None


class TestLocalGitServiceCurrentBranch:
    """Tests for LocalGitService.current_branch."""

    @pytest.mark.asyncio
    async def test_returns_branch_name(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, "feat/my-branch\n", ""),
        ) as mock_run:
            result = await service.current_branch("/workspace")
        assert result == "feat/my-branch"
        assert mock_run.await_args.kwargs["cwd"] == "/workspace"

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(128, "", "fatal: not a git repo"),
        ):
            result = await service.current_branch("/workspace")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_output(self):
        service = LocalGitService()
        with patch(
            "volundr.adapters.outbound.local_git._run",
            new_callable=AsyncMock,
            return_value=(0, "", ""),
        ):
            result = await service.current_branch("/workspace")
        assert result is None

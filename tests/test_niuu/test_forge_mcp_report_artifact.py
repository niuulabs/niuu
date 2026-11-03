"""The ``report_artifact`` Forge MCP tool: validation, dispatch and the feed-mode exclusion."""

from __future__ import annotations

from typing import Any

import pytest

from niuu.domain.artifact_reports import ArtifactReport
from niuu.forge_mcp.models import ForgeApiError
from niuu.forge_mcp.tools import TRANSCRIPT_ONLY_TOOLS, UnknownToolError
from tests.test_niuu.test_forge_mcp_tools import FakeHost, _feed_toolbox, _toolbox


class ReportingHost(FakeHost):
    def __init__(self) -> None:
        super().__init__()
        self.reports: list[ArtifactReport] = []

    async def report_artifact(self, report: ArtifactReport) -> dict[str, Any]:
        self.reports.append(report)
        return {"turn_id": "ar_x", "session_seq": 3, "url": report.url, "state": "committed"}


async def test_valid_report_reaches_the_host():
    toolbox, _, _ = _toolbox()
    host = ReportingHost()
    toolbox._host = host
    result = await toolbox.call(
        "report_artifact",
        {"url": "https://claude.ai/artifact/AbC9", "title": "  My   deck ", "type": "deck"},
    )
    assert not result.is_error
    assert result.structured["state"] == "committed"
    [report] = host.reports
    assert (report.url, report.title, report.type) == (
        "https://claude.ai/artifact/AbC9",
        "My deck",
        "deck",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"url": "https://example.com/artifact/x", "title": "t"},
        {"url": "https://claude.ai/artifact/AbC9", "title": "   "},
        {"url": "https://claude.ai/artifact/AbC9", "title": "t", "type": "spreadsheet"},
    ],
)
async def test_invalid_report_is_a_tool_error(arguments):
    toolbox, _, _ = _toolbox()
    host = ReportingHost()
    toolbox._host = host
    result = await toolbox.call("report_artifact", arguments)
    assert result.is_error
    assert host.reports == []


async def test_missing_required_fields_are_a_tool_error():
    toolbox, _, _ = _toolbox()
    result = await toolbox.call("report_artifact", {"title": "t"})
    assert result.is_error


async def test_default_host_refuses_with_501():
    toolbox, _, _ = _toolbox()  # FakeHost keeps the port's default report_artifact
    result = await toolbox.call(
        "report_artifact", {"url": "https://claude.ai/artifact/AbC9", "title": "t"}
    )
    assert result.is_error
    assert "own MCP" in result.text
    with pytest.raises(ForgeApiError) as raised:
        await FakeHost().report_artifact(
            ArtifactReport(url="https://claude.ai/artifact/AbC9", title="t")
        )
    assert raised.value.status == 501


async def test_feed_mode_does_not_offer_it():
    toolbox, _ = _feed_toolbox()
    assert "report_artifact" in TRANSCRIPT_ONLY_TOOLS
    assert "report_artifact" not in [tool["name"] for tool in toolbox.list_tools()]
    with pytest.raises(UnknownToolError):
        await toolbox.call("report_artifact", {"url": "https://claude.ai/artifact/A", "title": "t"})


def test_schema_requires_url_and_title():
    toolbox, _, _ = _toolbox()
    tool = next(t for t in toolbox.list_tools() if t["name"] == "report_artifact")
    assert tool["inputSchema"]["required"] == ["url", "title"]
    assert "do not report those" in tool["description"]

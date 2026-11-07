"""Tests for port interfaces and ToolPort.to_api_dict()."""

from __future__ import annotations

from niuu.domain.tool import ToolResult
from tests.test_ravn.conftest import EchoTool


class TestToolPort:
    def test_to_api_dict(self) -> None:
        tool = EchoTool()
        d = tool.to_api_dict()
        assert d["name"] == "echo"
        assert d["description"] == "Echoes the message back."
        assert "properties" in d["input_schema"]

    def test_all_required_properties(self) -> None:
        tool = EchoTool()
        assert tool.name == "echo"
        assert tool.description
        assert tool.input_schema
        assert tool.required_permission == "tool:echo"

    async def test_execute(self) -> None:
        tool = EchoTool()
        result = await tool.execute({"message": "hello"})
        assert isinstance(result, ToolResult)
        assert result.content == "hello"

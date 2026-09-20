"""Graph-level tool restrictions are validated when a workflow document loads."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from ting.domain.workflow_document import WorkflowDocumentError, load_workflow_document
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH


def _document(**graph_overrides: Any) -> str:
    source = (BUNDLED_SYSTEM_WORKFLOWS_PATH / "code-review-flow.yaml").read_text(encoding="utf-8")
    document = yaml.safe_load(source)
    document["graph"].update(graph_overrides)
    return yaml.safe_dump(document, sort_keys=False)


def test_restrictions_are_optional() -> None:
    assert "toolActions" not in load_workflow_document(_document()).graph


def test_valid_restrictions_load() -> None:
    graph = load_workflow_document(
        _document(toolActions={"workspace": ["verify"]}, disabledTools=["publisher"])
    ).graph

    assert graph["toolActions"] == {"workspace": ["verify"]}
    assert graph["disabledTools"] == ["publisher"]


@pytest.mark.parametrize(
    "tool_actions",
    [{}, [], {"": ["verify"]}, {"workspace": []}, {"workspace": [""]}, {"workspace": "verify"}],
)
def test_malformed_tool_actions_are_rejected(tool_actions: object) -> None:
    with pytest.raises(WorkflowDocumentError, match="toolActions"):
        load_workflow_document(_document(toolActions=tool_actions))


def test_repeated_tool_action_is_rejected() -> None:
    with pytest.raises(WorkflowDocumentError, match="must not repeat action names"):
        load_workflow_document(_document(toolActions={"workspace": ["verify", "verify"]}))


@pytest.mark.parametrize("disabled", [[], {}, "publisher", [""], [7]])
def test_malformed_disabled_tools_are_rejected(disabled: object) -> None:
    with pytest.raises(WorkflowDocumentError, match="disabledTools"):
        load_workflow_document(_document(disabledTools=disabled))


def test_repeated_disabled_tool_is_rejected() -> None:
    with pytest.raises(WorkflowDocumentError, match="must not repeat tool names"):
        load_workflow_document(_document(disabledTools=["publisher", "publisher"]))


def test_a_tool_cannot_be_both_withheld_and_narrowed() -> None:
    with pytest.raises(WorkflowDocumentError, match="either withheld or narrowed"):
        load_workflow_document(
            _document(toolActions={"workspace": ["verify"]}, disabledTools=["workspace"])
        )

from __future__ import annotations

import pytest

from ravn.adapters.resident_state.gbrain import GBrainResidentStateAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState, MimirResidentState
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentPolicyObservation,
    ResidentTurnRecord,
    ResidentWorkingStateRecord,
)


class RecordingGBrainResidentState(GBrainResidentStateAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mcp_calls: list[tuple[str, dict]] = []
        self.ingests: list[tuple[str, str]] = []

    async def _call_mcp_tool(self, name: str, arguments: dict):
        self.mcp_calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "[]"}]}

    async def _ingest_gbrain(self, slug: str, content: str) -> None:
        self.ingests.append((slug, content))


@pytest.mark.asyncio
async def test_local_resident_state_is_single_memory_boundary(tmp_path):
    state = LocalResidentState(tmp_path)

    turn_ref = await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert turn_ref.startswith("resident/continuation/turns/")
    assert (tmp_path / turn_ref).exists()
    assert turn_ref in await state.list_refs()


@pytest.mark.asyncio
async def test_local_resident_state_rejects_refs_outside_root(tmp_path):
    root = tmp_path / "resident-state"
    state = LocalResidentState(root)
    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("not resident state", encoding="utf-8")

    assert await state.list_refs("../outside") == []
    assert await state.list_refs(str(outside)) == []
    assert len(await state.list_refs()) == 1
    assert len(await state.list_refs("resident/continuation/turns")) == 1
    assert await state.list_refs("resident/continuation/policy") == []


@pytest.mark.asyncio
async def test_mimir_resident_state_is_single_memory_boundary(tmp_path):
    from mimir.adapters.markdown import MarkdownMimirAdapter

    mimir = MarkdownMimirAdapter(root=tmp_path / "mimir")
    state = MimirResidentState(mimir)

    turn_ref = await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert turn_ref.startswith("resident/continuation/turns/")
    assert "resident state recorded" in await mimir.read_page(turn_ref)
    assert turn_ref in await state.list_refs()

    working_ref = await state.write_working_state(
        ResidentWorkingStateRecord(
            resident_id="resident-alpha",
            state={"unknowns": ["whether the source is reachable"]},
            source_turn_ref=turn_ref,
            source_case_id="case-1",
            source_task_id="task-1",
        )
    )
    working = await state.read_working_state("resident-alpha")
    assert working_ref == "resident/continuation/working-state/resident-alpha.md"
    assert working is not None
    assert "whether the source is reachable" in working.content


@pytest.mark.asyncio
async def test_gbrain_resident_state_prefers_synchronous_put_page_with_mcp(tmp_path):
    state = RecordingGBrainResidentState(
        tmp_path,
        mcp_url="http://127.0.0.1:3131/mcp",
        ingest_url="http://127.0.0.1:3131/ingest",
        api_token="token",
    )

    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert [name for name, _arguments in state.mcp_calls] == ["put_page"]
    assert state.ingests == []
    name, arguments = state.mcp_calls[0]
    assert name == "put_page"
    assert arguments["slug"].startswith("resident/continuation/turns/")
    projected_name = arguments["slug"].rsplit("/", 1)[-1]
    assert projected_name.startswith("t")
    assert "T" not in projected_name
    assert "Z" not in projected_name
    assert "Ravn resident memory" in arguments["content"]


@pytest.mark.asyncio
async def test_gbrain_resident_state_can_use_explicit_ingest_mode(tmp_path):
    state = RecordingGBrainResidentState(
        tmp_path,
        mcp_url="http://127.0.0.1:3131/mcp",
        ingest_url="http://127.0.0.1:3131/ingest",
        api_token="token",
        write_mode="ingest",
    )

    await state.write_turn(
        ResidentTurnRecord(
            turn_index=1,
            prompt="inspect resident state",
            response="resident state recorded",
            outcome_fields={},
            tool_names=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
    )

    assert state.mcp_calls == []
    assert len(state.ingests) == 1
    assert state.ingests[0][0].startswith("resident/continuation/turns/")


@pytest.mark.asyncio
async def test_gbrain_resident_state_projects_operator_feedback(tmp_path):
    state = RecordingGBrainResidentState(
        tmp_path,
        mcp_url="http://127.0.0.1:3131/mcp",
        api_token="token",
    )

    answer_ref = await state.write_operator_answer(
        "Not approved. Investigate Kanuck Valley Models online instead."
    )
    policy_ref = await state.write_policy_observation(
        ResidentPolicyObservation(
            subject="operator-contact:approval",
            observation="Not approved. Investigate Kanuck Valley Models online instead.",
            source="operator_answer",
            status="candidate",
        )
    )

    slugs = [arguments["slug"] for _name, arguments in state.mcp_calls]
    assert answer_ref == "resident/continuation/operator-answers/latest.md"
    assert policy_ref == "resident/continuation/policy/operator-contact-approval.md"
    assert "resident/continuation/operator-answers/latest" in slugs
    assert "resident/continuation/policy/operator-contact-approval" in slugs


@pytest.mark.asyncio
async def test_gbrain_availability_is_fatal_not_a_fallback(tmp_path) -> None:
    """Replaces a test asserting selection fell through to a second adapter.

    Falling through ran every resident against its local store while the
    configured preference was unreachable — permanently, and unnoticed until a
    counter was added months later. There is now no second adapter to fall to.
    """
    from ravn.adapters.resident_state import select_resident_state

    absent = GBrainResidentStateAdapter(tmp_path / "g1", command="gbrain-not-installed-xyz")
    assert await absent.available() is False
    with pytest.raises(RuntimeError, match="backend is not available"):
        await select_resident_state(absent)

    present = GBrainResidentStateAdapter(
        tmp_path / "g2", mcp_url="https://brain.example", api_token="tok"
    )
    assert await present.available() is True
    assert await select_resident_state(present) is present

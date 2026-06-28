from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.momentum.models import (
    MomentumAttentionDecision,
    MomentumDelegationBrief,
    MomentumExtractionRun,
)
from ravn.momentum.render import (
    render_attention_decision,
    render_delegation_brief,
    render_run,
)
from ravn.resident_inbox.serialization import parse_inbox_signal

FIXTURES = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path(os.environ.get("TMPDIR", "/tmp")) / "ravn-niu1080-proof"
ROOT_ENV = "RAVN_MOMENTUM_HANDOFF_PROOF_ROOT"
CURRENT_STATE_REF = "resident/continuation/momentum/state/current.md"
RUN_REF = "resident/momentum/runs/niu1080-fixture/run.md"
JUDGMENT_REF = (
    "resident/momentum/runs/niu1080-fixture/judgment/"
    "judgment-handoff-current-state-attention.md"
)
ATTENTION_REF = "resident/continuation/momentum/attention/attention-niu1080-fixture.md"
BRIEF_REF = "resident/continuation/momentum/delegations/delegation-niu1080-handoff-fixture.md"
SIGNAL_REF = "resident/inbox/signals/20260628T100500Z-current-state-attention.md"
DISTRACTOR_REF = "resident/inbox/signals/20260628T100400Z-distractor.md"


async def seed(root: Path) -> None:
    state = LocalResidentState(root / "state")
    current_state = (FIXTURES / "momentum_attention_current_state.md").read_text(
        encoding="utf-8"
    )
    current_ref = await state.write_artifact(CURRENT_STATE_REF, current_state)

    mimir = MarkdownMimirAdapter(root=root / "mimir")
    signal_rows = []
    for name, ref in [
        ("momentum_attention_signal_relevant.md", SIGNAL_REF),
        ("momentum_attention_signal_distractor.md", DISTRACTOR_REF),
    ]:
        content = (FIXTURES / name).read_text(encoding="utf-8")
        signal = parse_inbox_signal(content)
        await mimir.upsert_page(ref, content)
        signal_rows.append((ref, signal.id, signal.summary))

    attention = MomentumAttentionDecision(
        decision_id="attention-niu1080-fixture",
        selected_signal_id="sig-attention-current-state-relevant",
        selected_signal_ref=SIGNAL_REF,
        no_attention_needed=False,
        selected_tension_ids=["tension-current-state-attention"],
        validation_status="valid",
        attention_tier="present",
        rationale=(
            "The selected signal addresses the open current-state attention tension."
        ),
        why_now="The current Momentum state names this proof tension as open.",
        evidence_refs=[current_ref],
        signal_refs=[SIGNAL_REF, DISTRACTOR_REF],
        recommended_next_action="extract_selected_signal",
        confidence=0.82,
        source_refs=[current_ref, SIGNAL_REF, DISTRACTOR_REF],
        created_at=datetime(2026, 6, 28, 10, 10, tzinfo=UTC),
        current_state_ref=current_ref,
        current_state_present=True,
        candidate_count=2,
        candidate_limit=2,
        candidates_truncated=0,
        procedure_name="committed_fixture_equivalent",
        model_name="committed-fixture",
    )
    attention_ref = await state.write_artifact(
        ATTENTION_REF,
        render_attention_decision(attention),
    )

    judgment = _judgment_markdown()
    judgment_ref = await state.write_artifact(JUDGMENT_REF, judgment)
    run = MomentumExtractionRun(
        run_id="niu1080-fixture",
        source_path=SIGNAL_REF,
        source_sha256="0" * 64,
        input_state_ref=current_ref,
        input_state_sha256="1" * 64,
        procedure_name="committed_fixture_equivalent",
        model_name="committed-fixture",
        created_at=datetime(2026, 6, 28, 10, 12, tzinfo=UTC),
        provenance_fully_verified=True,
        artifact_refs=[],
        judgment_ref=judgment_ref,
        packet_ref=None,
        attention_ref=attention_ref,
        attention_decision_id=attention.decision_id,
        selected_signal_id=attention.selected_signal_id,
        selected_signal_ref=attention.selected_signal_ref,
    )
    run_ref = await state.write_artifact(RUN_REF, render_run(run))

    brief = MomentumDelegationBrief(
        brief_id="delegation-niu1080-handoff-fixture",
        handoff_recommended=True,
        no_handoff_reason="",
        title="Inspect the bounded Momentum handoff context",
        rationale=(
            "The judgment asks for an executor-class runtime to inspect a bounded "
            "handoff frame and report what it received."
        ),
        desired_outcome=(
            "A non-mutating executor report that confirms the linked brief, judgment, "
            "run, attention decision, signal, and current state were visible."
        ),
        bounded_request=(
            "Inspect the handoff frame only. Do not edit files, run tests, create issues, "
            "or call external project systems. Return a concise report naming the source "
            "refs you saw and whether reflection should follow."
        ),
        evidence_refs=[run_ref, judgment_ref, attention_ref, current_ref],
        constraints=[
            "Use native executor behavior and permissions.",
            "Do not mutate repository files.",
            "Do not create follow-up work items.",
        ],
        out_of_scope_boundaries=[
            "No code changes.",
            "No reflection execution.",
            "No capability registration.",
        ],
        success_proof=(
            "The persisted handoff result links back to this brief and contains the "
            "executor's report."
        ),
        expected_return_format=(
            "Plain text summary with observed refs, status, produced evidence refs if any, "
            "and recommended follow-up."
        ),
        suggested_executor_context="local Codex read-only inspection",
        skill_or_tool_hints=["Read the provided handoff frame."],
        capability_gap_notes=[],
        handoff_notes="This is a v0 non-mutating handoff proof over committed fixtures.",
        confidence=0.83,
        execution_performed=False,
        source_run_ref=run_ref,
        source_judgment_ref=judgment_ref,
        source_attention_ref=attention_ref,
        source_signal_id=attention.selected_signal_id,
        source_signal_ref=attention.selected_signal_ref,
        validation_status="valid",
        created_at=datetime(2026, 6, 28, 10, 15, tzinfo=UTC),
        procedure_name="committed_fixture_equivalent",
        model_name="committed-fixture",
    )
    brief_ref = await state.write_artifact(BRIEF_REF, render_delegation_brief(brief))
    config_path = _write_config(root)

    print(f"proof_root: {root}")
    print(f"config: {config_path}")
    print(f"current_state_ref: {current_ref}")
    for ref, signal_id, summary in signal_rows:
        print(f"candidate_ref: {ref}")
        print(f"candidate_id: {signal_id}")
        print(f"candidate_summary: {summary}")
    print(f"attention_ref: {attention_ref}")
    print(f"selected_signal_id: {attention.selected_signal_id}")
    print(f"selected_signal_ref: {attention.selected_signal_ref}")
    print(f"run_ref: {run_ref}")
    print(f"judgment_ref: {judgment_ref}")
    print(f"brief_ref: {brief_ref}")


def _judgment_markdown() -> str:
    return (
        "# Handoff current-state attention judgment\n\n"
        "- judgment_id: judgment-handoff-current-state-attention\n"
        "- recommended_next_action: prepare_delegation\n"
        "- confidence: 0.82\n\n"
        "## Changed Understanding\n\n"
        "The current Momentum state can steer attention toward a relevant resident signal.\n\n"
        "## Recommended Action\n\n"
        "Prepare a bounded, non-mutating handoff so an executor-class runtime can inspect "
        "the linked evidence context and report what it saw.\n"
    )


def _write_config(root: Path) -> Path:
    config = root / "ravn.yaml"
    config.write_text(
        "resident_state:\n"
        "  adapter: ravn.adapters.resident_state.mimir.LocalResidentState\n"
        "  kwargs:\n"
        f"    root: {root / 'state'}\n"
        "  fallback_adapter: ravn.adapters.resident_state.mimir.LocalResidentState\n"
        "  fallback_kwargs:\n"
        f"    root: {root / 'fallback-state'}\n"
        "mimir:\n"
        "  enabled: true\n"
        f"  path: {root / 'mimir'}\n"
        "momentum_executor:\n"
        "  adapter: ravn.adapters.executors.cli.CliTransportExecutor\n"
        "  kwargs:\n"
        "    transport_adapter: skuld.transports.codex.CodexSubprocessTransport\n"
        "    transport_kwargs:\n"
        "      model: ''\n",
        encoding="utf-8",
    )
    return config


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed committed fixtures for the NIU-1080 Momentum handoff proof."
    )
    parser.add_argument(
        "--root",
        default=os.environ.get(ROOT_ENV, str(DEFAULT_ROOT)),
        help=f"Temporary proof root. Defaults to ${ROOT_ENV} or {DEFAULT_ROOT}.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    parsed = _args()
    asyncio.run(seed(Path(parsed.root).expanduser().resolve()))

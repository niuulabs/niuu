"""LLM-facing worker for momentum extraction."""

from __future__ import annotations

import json
import re

from ravn.momentum.models import (
    MomentumAttentionDecisionDraft,
    MomentumDelegationBriefDraft,
    MomentumExtractionDraft,
    MomentumJudgmentDisposition,
    MomentumReflectionDraft,
)
from ravn.ports.llm import LLMPort

PROCEDURE_NAME = "ravn.momentum.extract.v1"
REFLECTION_PROCEDURE_NAME = "ravn.momentum.reflect.v1"
ATTENTION_PROCEDURE_NAME = "ravn.momentum.attend.v1"
DELEGATION_PROCEDURE_NAME = "ravn.momentum.delegate.v1"

SYSTEM_PROMPT = """You extract the living shape of an idea into typed artifacts.

Return only JSON matching this shape:
{
  "artifacts": [
    {
      "kind": "durable_insight|rejected_direction|unresolved_tension",
      "title": "...",
      "summary": "...",
      "reason": "...",
      "source": {"excerpt": "...", "line_start": 1, "line_end": 3},
      "tags": ["..."]
    }
  ],
  "resident_patch": {
    "title": "...",
    "summary": "...",
    "reason": "...",
    "beliefs": ["..."],
    "constraints": ["..."],
    "corrections": ["..."],
    "source": {"excerpt": "..."}
  },
  "judgment": {
    "title": "...",
    "environment_id": "resident:niuu",
    "valkyrie_id": "ravn-momentum",
    "changed_understanding": "...",
    "tension_that_matters": "...",
    "why_attention_now": "...",
    "recommended_next_action": "write_momentum_packet|update_understanding_only|ask_human",
    "recommended_action": "...",
    "attention_tier": "silent|ambient|present|urgent",
    "authority_boundary": "human_review_required",
    "operational_state": "proposing",
    "confidence": 0.82,
    "signal_refs": ["resident inbox signal id, source path, or evidence ref"],
    "evidence_artifact_titles": ["..."],
    "target_surfaces": ["resident/momentum"],
    "source": {"excerpt": "..."}
  },
  "packet": null or {
    "title": "...",
    "implementation_slice": "...",
    "why_it_matters": "...",
    "caused_by": ["artifact title or id"],
    "must_not_lose": ["..."],
    "reuse_guidance": ["..."],
    "out_of_scope": ["..."],
    "success_proof": "...",
    "reflection_prompts": ["..."],
    "source": {"excerpt": "..."}
  }
}

Semantic judgment belongs to you. Preserve durable insights, rejected directions,
unresolved tensions, resident-understanding updates, and one bounded Momentum
Packet when the judgment recommends one. The judgment answers what changed in
understanding, which tension now matters most, why it deserves attention, and
what should happen next. Do not turn the packet into a generic ticket.

Every source.excerpt must be a verbatim contiguous substring copied from the
resident signal markdown. Do not paraphrase, summarize, or clean up source
excerpts. Line ranges are useful only when they point at the cited text. If you
are not certain about exact rendered line numbers, omit line_start and line_end;
an exact source excerpt without line numbers is better than an inaccurate range.
"""


class MomentumExtractionWorker:
    """Single typed cognitive procedure; later split points stay at this boundary."""

    def __init__(
        self,
        llm: LLMPort,
        *,
        model: str,
        procedure_name: str = PROCEDURE_NAME,
        max_tokens: int = 6000,
    ) -> None:
        self._llm = llm
        self.model = model
        self.procedure_name = procedure_name
        self._max_tokens = max_tokens

    async def extract(
        self,
        markdown: str,
        *,
        memory_frame: str = "",
        current_state_frame: str = "",
    ) -> MomentumExtractionDraft:
        response = await self._llm.generate(
            [
                {
                    "role": "user",
                    "content": _input_frame(
                        markdown,
                        memory_frame=memory_frame,
                        current_state_frame=current_state_frame,
                    ),
                }
            ],
            tools=[],
            system=SYSTEM_PROMPT,
            model=self.model,
            max_tokens=self._max_tokens,
        )
        return MomentumExtractionDraft.model_validate_json(_json_payload(response.content))


REFLECTION_SYSTEM_PROMPT = """You reflect on a Momentum judgment after an outcome is known.

Return only JSON matching this shape:
{
  "changed_understanding": "...",
  "lesson_learned": "...",
  "original_judgment_useful": true,
  "remember_next_time": ["..."],
  "resident_corrections": ["..."],
  "candidate_reflexes": ["candidate only, do not promote"],
  "candidate_capability_gaps": ["candidate only, do not register"],
  "state_patch": {
    "beliefs": ["..."],
    "constraints": ["..."],
    "corrections": ["..."],
    "open_tensions": [
      {
        "tension_id": "stable-explicit-id",
        "title": "...",
        "summary": "...",
        "status": "pending|open|confirmed|changed|resolved",
        "evidence_refs": ["..."],
        "source_refs": ["..."]
      }
    ],
    "changed_tensions": [],
    "resolved_tension_ids": ["..."],
    "confirmed_tension_ids": ["..."],
    "stale_assumptions": ["..."],
    "recent_lessons": ["..."],
    "candidate_reflexes": ["candidate only, do not promote"],
    "candidate_capability_gaps": ["candidate only, do not register"]
  }
}

Semantic learning belongs to you. The deterministic system only records the
operator disposition, applies your state_patch, and persists your reflection.
Use state_patch to say what changed in current understanding, what should be
remembered next time, what tensions are confirmed/resolved/changed/opened, and
which corrections apply. Candidate reflexes and capability gaps are notes for
later review; do not describe them as promoted, executed, registered, or applied.
"""


class MomentumReflectionWorker:
    """Typed reflection over a persisted judgment disposition."""

    def __init__(
        self,
        llm: LLMPort,
        *,
        model: str,
        procedure_name: str = REFLECTION_PROCEDURE_NAME,
        max_tokens: int = 3000,
    ) -> None:
        self._llm = llm
        self.model = model
        self.procedure_name = procedure_name
        self._max_tokens = max_tokens

    async def reflect(
        self,
        *,
        target_ref: str,
        target_content: str,
        run_content: str,
        judgment_content: str,
        artifact_contents: list[str],
        disposition: MomentumJudgmentDisposition,
        memory_frame: str = "",
        current_state_frame: str = "",
    ) -> MomentumReflectionDraft:
        response = await self._llm.generate(
            [
                {
                    "role": "user",
                    "content": _reflection_input_frame(
                        target_ref=target_ref,
                        target_content=target_content,
                        run_content=run_content,
                        judgment_content=judgment_content,
                        artifact_contents=artifact_contents,
                        disposition=disposition,
                        memory_frame=memory_frame,
                        current_state_frame=current_state_frame,
                    ),
                }
            ],
            tools=[],
            system=REFLECTION_SYSTEM_PROMPT,
            model=self.model,
            max_tokens=self._max_tokens,
        )
        return MomentumReflectionDraft.model_validate_json(_json_payload(response.content))


ATTENTION_SYSTEM_PROMPT = """You select Momentum attention from current resident state
and candidate signals.

Return only JSON matching this shape:
{
  "selected_signal_id": "signal id or null",
  "selected_signal_ref": "signal ref or null",
  "no_attention_needed": false,
  "selected_tension_ids": ["current-state tension id"],
  "attention_tier": "silent|ambient|present|urgent",
  "rationale": "...",
  "why_now": "...",
  "evidence_refs": ["candidate signal ref/id or current-state ref"],
  "signal_refs": ["candidate signal ref/id"],
  "recommended_next_action": "extract_selected_signal",
  "confidence": 0.82,
  "source_refs": ["candidate signal ref/id or current-state ref"]
}

Select based on current Momentum state and open tensions, not simply newest,
loudest, or most detailed. If nothing deserves attention now, set
no_attention_needed true, selected_signal_id/ref null, recommended_next_action
no_action, and explain why.

Allowed recommended_next_action values: extract_selected_signal, ask_human,
update_understanding_only, no_action.
"""


class MomentumAttentionWorker:
    """Typed attention selection over current state and candidate signals."""

    def __init__(
        self,
        llm: LLMPort,
        *,
        model: str,
        procedure_name: str = ATTENTION_PROCEDURE_NAME,
        max_tokens: int = 3000,
    ) -> None:
        self._llm = llm
        self.model = model
        self.procedure_name = procedure_name
        self._max_tokens = max_tokens

    async def attend(
        self,
        *,
        memory_frame: str,
        current_state_frame: str,
        candidate_frame: str,
        truncation_note: str,
    ) -> MomentumAttentionDecisionDraft:
        response = await self._llm.generate(
            [
                {
                    "role": "user",
                    "content": _attention_input_frame(
                        memory_frame=memory_frame,
                        current_state_frame=current_state_frame,
                        candidate_frame=candidate_frame,
                        truncation_note=truncation_note,
                    ),
                }
            ],
            tools=[],
            system=ATTENTION_SYSTEM_PROMPT,
            model=self.model,
            max_tokens=self._max_tokens,
        )
        return MomentumAttentionDecisionDraft.model_validate_json(
            _json_payload(response.content)
        )


DELEGATION_SYSTEM_PROMPT = """You prepare bounded Momentum delegation briefs.

Return only JSON matching this shape:
{
  "handoff_recommended": true,
  "no_handoff_reason": "",
  "title": "...",
  "rationale": "...",
  "desired_outcome": "...",
  "bounded_request": "...",
  "evidence_refs": ["judgment, run, attention, packet, artifact, or state refs"],
  "constraints": ["..."],
  "out_of_scope_boundaries": ["..."],
  "success_proof": "...",
  "expected_return_format": "...",
  "suggested_executor_context": "optional free text, or empty",
  "skill_or_tool_hints": ["optional free text hints, not an allowlist"],
  "capability_gap_notes": ["optional missing skill/capability notes"],
  "handoff_notes": "...",
  "confidence": 0.82,
  "execution_performed": false
}

Prepare a handoff brief only. Do not execute, delegate, contact humans, create
tickets, start workflows, register capabilities, call tools, or choose concrete
tool calls. The future executor will use its own native tools, skills, and
permission system. Focus on intent, evidence, constraints, desired outcome, and
success proof. If no handoff is needed, set handoff_recommended false and
explain no_handoff_reason. If a missing skill or capability is discovered,
record it as a capability_gap_note, not as a registration or action.
"""


class MomentumDelegationWorker:
    """Typed delegation brief preparation over a persisted Momentum judgment."""

    def __init__(
        self,
        llm: LLMPort,
        *,
        model: str,
        procedure_name: str = DELEGATION_PROCEDURE_NAME,
        max_tokens: int = 3000,
    ) -> None:
        self._llm = llm
        self.model = model
        self.procedure_name = procedure_name
        self._max_tokens = max_tokens

    async def prepare(
        self,
        *,
        source_ref: str,
        judgment_content: str,
        run_content: str,
        packet_content: str,
        attention_content: str,
        artifact_contents: list[str],
        current_state_frame: str,
    ) -> MomentumDelegationBriefDraft:
        response = await self._llm.generate(
            [
                {
                    "role": "user",
                    "content": _delegation_input_frame(
                        source_ref=source_ref,
                        judgment_content=judgment_content,
                        run_content=run_content,
                        packet_content=packet_content,
                        attention_content=attention_content,
                        artifact_contents=artifact_contents,
                        current_state_frame=current_state_frame,
                    ),
                }
            ],
            tools=[],
            system=DELEGATION_SYSTEM_PROMPT,
            model=self.model,
            max_tokens=self._max_tokens,
        )
        return MomentumDelegationBriefDraft.model_validate_json(
            _json_payload(response.content)
        )


def _input_frame(markdown: str, *, memory_frame: str, current_state_frame: str) -> str:
    return (
        "## Existing resident memory frame\n\n"
        f"{memory_frame or '(none)'}\n\n"
        "## Current Momentum state\n\n"
        f"{current_state_frame or '(none)'}\n\n"
        "## Resident signal markdown\n\n"
        f"{markdown}"
    )


def _attention_input_frame(
    *,
    memory_frame: str,
    current_state_frame: str,
    candidate_frame: str,
    truncation_note: str,
) -> str:
    return (
        "## Existing resident memory frame\n\n"
        f"{memory_frame or '(none)'}\n\n"
        "## Current Momentum state\n\n"
        f"{current_state_frame or '(none)'}\n\n"
        "## Candidate resident signals\n\n"
        f"{candidate_frame or '(none)'}\n\n"
        "## Candidate frame bounds\n\n"
        f"{truncation_note or 'No candidate truncation.'}"
    )


def _reflection_input_frame(
    *,
    target_ref: str,
    target_content: str,
    run_content: str,
    judgment_content: str,
    artifact_contents: list[str],
    disposition: MomentumJudgmentDisposition,
    memory_frame: str,
    current_state_frame: str,
) -> str:
    return (
        "## Existing resident memory frame\n\n"
        f"{memory_frame or '(none)'}\n\n"
        "## Current Momentum state\n\n"
        f"{current_state_frame or '(none)'}\n\n"
        "## Disposition\n\n"
        f"- target_ref: {target_ref}\n"
        f"- outcome: {disposition.outcome}\n"
        f"- actor: {disposition.actor}\n"
        f"- note: {disposition.note}\n"
        f"- created_at: {disposition.created_at.isoformat()}\n\n"
        "## Target artifact\n\n"
        f"{target_content}\n\n"
        "## Run artifact\n\n"
        f"{run_content or '(unavailable)'}\n\n"
        "## Judgment artifact\n\n"
        f"{judgment_content or '(unavailable)'}\n\n"
        "## Related artifacts\n\n"
        f"{_join_sections(artifact_contents)}"
    )


def _delegation_input_frame(
    *,
    source_ref: str,
    judgment_content: str,
    run_content: str,
    packet_content: str,
    attention_content: str,
    artifact_contents: list[str],
    current_state_frame: str,
) -> str:
    return (
        "## Delegation preparation bounds\n\n"
        "Prepare a handoff brief artifact only. Do not execute anything. "
        "Do not choose concrete tool calls. The future executor uses its own "
        "native tools, skills, and permissions.\n\n"
        "## Source ref\n\n"
        f"{source_ref}\n\n"
        "## Current Momentum state\n\n"
        f"{current_state_frame or '(none)'}\n\n"
        "## Judgment artifact\n\n"
        f"{judgment_content or '(unavailable)'}\n\n"
        "## Run artifact\n\n"
        f"{run_content or '(unavailable)'}\n\n"
        "## Packet artifact\n\n"
        f"{packet_content or '(unavailable)'}\n\n"
        "## Linked attention decision\n\n"
        f"{attention_content or '(unavailable)'}\n\n"
        "## Related artifacts\n\n"
        f"{_join_sections(artifact_contents)}"
    )


def _join_sections(contents: list[str]) -> str:
    if not contents:
        return "(none)"
    return "\n\n---\n\n".join(contents)


def _json_payload(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL)
    payload = match.group(1) if match else stripped
    json.loads(payload)
    return payload

"""LLM-facing worker for momentum extraction."""

from __future__ import annotations

import json
import re

from ravn.momentum.models import (
    MomentumExtractionDraft,
    MomentumJudgmentDisposition,
    MomentumReflectionDraft,
)
from ravn.ports.llm import LLMPort

PROCEDURE_NAME = "ravn.momentum.extract.v1"
REFLECTION_PROCEDURE_NAME = "ravn.momentum.reflect.v1"

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

    async def extract(self, markdown: str, *, memory_frame: str = "") -> MomentumExtractionDraft:
        response = await self._llm.generate(
            [
                {
                    "role": "user",
                    "content": _input_frame(markdown, memory_frame=memory_frame),
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
  "candidate_capability_gaps": ["candidate only, do not register"]
}

Semantic learning belongs to you. The deterministic system only records the
operator disposition and persists your reflection. Candidate reflexes and
capability gaps are notes for later review; do not describe them as promoted,
executed, registered, or applied.
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
                    ),
                }
            ],
            tools=[],
            system=REFLECTION_SYSTEM_PROMPT,
            model=self.model,
            max_tokens=self._max_tokens,
        )
        return MomentumReflectionDraft.model_validate_json(_json_payload(response.content))


def _input_frame(markdown: str, *, memory_frame: str) -> str:
    return (
        "## Existing resident memory frame\n\n"
        f"{memory_frame or '(none)'}\n\n"
        "## Resident signal markdown\n\n"
        f"{markdown}"
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
) -> str:
    return (
        "## Existing resident memory frame\n\n"
        f"{memory_frame or '(none)'}\n\n"
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

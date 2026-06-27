"""LLM-facing worker for momentum extraction."""

from __future__ import annotations

import json
import re

from ravn.momentum.models import MomentumExtractionDraft
from ravn.ports.llm import LLMPort

PROCEDURE_NAME = "ravn.momentum.extract.v1"

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
  "packet": {
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
Packet. Do not turn the packet into a generic ticket. Cite exact source excerpts
from the markdown; line ranges are useful only when they point at the cited text.
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


def _input_frame(markdown: str, *, memory_frame: str) -> str:
    return (
        "## Existing resident memory frame\n\n"
        f"{memory_frame or '(none)'}\n\n"
        "## Source markdown\n\n"
        f"{markdown}"
    )


def _json_payload(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL)
    payload = match.group(1) if match else stripped
    json.loads(payload)
    return payload

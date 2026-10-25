"""Fakes for the lesson consolidation tests: a keyword embedding and a scripted judge.

The embedding places every text in a small concept space, so paraphrases of one
GitHub-route lesson land close together while unrelated lessons stay apart. The
LLM answers reflection prompts from a script and judge prompts by rule, so the
tests exercise the real prompt plumbing rather than mocking the judge away.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from unittest.mock import MagicMock

from ravn.adapters.reflection.post_session import _build_candidate_content
from ravn.ports.embedding import EmbeddingPort

#: The concept axes of the fake embedding.
CONCEPTS = ("github", "403", "route", "diff", "host", "migration", "helm")

# Regin's real rediscoveries (2026-10-23/24), and the contradicting claim.
DIFF_ROUTE = (
    "Use github.com/.diff when api.github.com checks return 403",
    "When api.github.com check-run endpoints return 403 from the resident, fetch the PR "
    "patch from github.com/<owner>/<repo>/pull/<n>.diff instead.",
)
DISTINCT_HOSTS = (
    "Probe distinct hosts before declaring a capability gap",
    "A 403 from one GitHub host does not mean every GitHub route is blocked; probe the "
    "github.com web host before declaring a gap.",
)
ALTERNATE_ROUTES = (
    "Test alternate GitHub web routes before declaring a read-route capability gap",
    "When the GitHub API route fails with 403, try the github.com web route (.diff or "
    ".patch) before reporting the capability as missing.",
)
ALL_ROUTES_BLOCKED = (
    "Public api.github.com is egress-blocked in resident env — all routes 403",
    "Every GitHub route returns 403 from the resident environment; GitHub reads are "
    "structurally unreachable.",
)
UNRELATED = (
    "Migrations go in both the migrations directory and the Helm configmap",
    "A migration added only under migrations/ never reaches the cluster; the Helm "
    "configmap embeds them.",
)

PROCEDURE = (
    "When api.github.com returns 403 for PR checks, fetch github.com/<owner>/<repo>/pull/"
    "<n>.diff before declaring a capability gap."
)


class KeywordEmbedding(EmbeddingPort):
    """Counts concept keywords; paraphrases about one concept embed together."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, text: str) -> list[float]:
        return _vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [_vector(text) for text in texts]

    @property
    def dimension(self) -> int:
        return len(CONCEPTS)


def _vector(text: str) -> list[float]:
    lowered = text.lower()
    return [float(lowered.count(concept)) for concept in CONCEPTS]


def is_blocked_claim(text: str) -> bool:
    lowered = text.lower()
    return "all routes 403" in lowered or "structurally unreachable" in lowered


def is_github(text: str) -> bool:
    return "github" in text.lower()


class ScriptedLLM:
    """Reflection answers from a script; judge answers by rule over the prompt."""

    def __init__(
        self,
        reflections: list[dict | None] | None = None,
        plan: object = None,
    ) -> None:
        self.reflections = list(reflections or [])
        self.prompts: list[str] = []
        self.judge_calls = 0
        # A planner answer (dict) or a callable(prompt) -> dict; default: commission
        # a tool for every recurring capability gap, nothing else.
        self.plan = plan

    async def generate(self, messages, tools, system, model, max_tokens):  # noqa: ARG002
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        if "reviewing friction you keep hitting" in prompt:
            self.judge_calls += 1
            if callable(self.plan):
                answer = self.plan(prompt)
            elif self.plan is not None:
                answer = self.plan
            else:
                answer = commission_capability_gaps(prompt)
        elif "Recorded lessons:" in prompt:
            self.judge_calls += 1
            answer: object = _match(prompt)
        elif "Compare two lessons" in prompt:
            self.judge_calls += 1
            answer = _relate(prompt)
        elif "procedural rule" in prompt:
            self.judge_calls += 1
            answer = {"procedure": PROCEDURE}
        else:
            answer = self.reflections.pop(0)
        response = MagicMock()
        response.content = json.dumps(answer)
        return response


def commission_capability_gaps(prompt: str) -> dict:
    """Planner rule: a recurring capability gap gets a tool commission."""
    actions = [
        {
            "friction": f"F{number}",
            "action": "commission_tool",
            "lesson": None,
            "need": subject.strip(),
            "expected_benefit": "the need is met by a tool instead of an operator",
            "rationale": "no existing capability covers this recurring need",
        }
        for number, subject in re.findall(
            r"^\[F(\d+)\] capability_gap, seen \d+ time\(s\), last [^:]*: (.*)$",
            prompt,
            flags=re.MULTILINE,
        )
    ]
    return {"actions": actions}


def _same_family(left: str, right: str) -> bool:
    return (
        is_github(left) and is_github(right) and (is_blocked_claim(left) == is_blocked_claim(right))
    )


def _match(prompt: str) -> dict:
    new = prompt.split("New lesson:", 1)[1].split("Recorded lessons:", 1)[0]
    recorded = prompt.split("Recorded lessons:", 1)[1].split("Respond with", 1)[0]
    entries = re.split(r"^\[(\d+)\] ", recorded, flags=re.MULTILINE)[1:]
    for number, text in zip(entries[::2], entries[1::2], strict=True):
        if _same_family(new, text):
            return {"same": int(number), "reason": "same GitHub route lesson"}
    return {"same": None, "reason": "no recorded lesson says this"}


def _relate(prompt: str) -> dict:
    left = prompt.split("A:\n", 1)[1].split("\n\nB:\n", 1)[0]
    right = prompt.split("\n\nB:\n", 1)[1].split("Respond with", 1)[0]
    if _same_family(left, right):
        return {"relation": "same", "reason": "same GitHub route lesson"}
    if is_github(left) and is_github(right):
        return {"relation": "contradicts", "reason": "one says the route works, one says not"}
    return {"relation": "distinct", "reason": "different subjects"}


def reflection(lesson: tuple[str, str], evidence: str, **extra: object) -> dict:
    title, claim = lesson
    return {
        "title": title,
        "learning": claim,
        "type": "observation",
        "tags": ["github"],
        "evidence": evidence,
        **extra,
    }


def candidate_page(
    lesson: tuple[str, str],
    *,
    session_id: str,
    evidence: str,
    date: datetime,
) -> str:
    title, claim = lesson
    return _build_candidate_content(
        title=title,
        learning=claim,
        page_type="observation",
        tags=["github"],
        evidence=evidence,
        repo_slug="",
        session_id=session_id,
        date=date,
    )

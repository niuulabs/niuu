from __future__ import annotations

import json
import os
import sys
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


def main() -> None:
    prompt = sys.stdin.read()
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": _prompt(prompt),
        "stream": False,
        "format": _schema(prompt),
        "options": {"temperature": 0.0, "num_predict": 2500},
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.loads(response.read().decode("utf-8"))
    print(data["response"].strip())


def _prompt(prompt: str) -> str:
    if "bounded Momentum delegation briefs" in prompt:
        return (
            prompt
            + "\n\nPrepare a bounded non-executing handoff brief from the linked "
            "Momentum evidence. Do not run tools, create tasks, register "
            "capabilities, or delegate. Set execution_performed false."
        )
    if "select Momentum attention" in prompt:
        return (
            prompt
            + "\n\nUse the current Momentum state and candidate signal content. "
            "Choose the candidate that best addresses an open or confirmed "
            "tension, if one is warranted. If you select a signal for judgment, "
            "the next action should pursue extraction of that selected signal."
        )
    if "You extract the living shape of an idea into typed artifacts" in prompt:
        return (
            prompt
            + "\n\nExtract the selected signal. Use exact source excerpts copied "
            "from the signal content; omit line ranges if unsure. Recommend a "
            "bounded non-executing next action. Ensure "
            "judgment.evidence_artifact_titles exactly match titles you return "
            "in artifacts or resident_patch. For every source.excerpt field, "
            "copy one exact contiguous substring from the provided resident "
            "signal; do not reconstruct headings, metadata, or punctuation."
        )
    raise ValueError("unrecognized Momentum proof prompt")


def _schema(prompt: str) -> dict:
    if "bounded Momentum delegation briefs" in prompt:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "handoff_recommended",
                "no_handoff_reason",
                "title",
                "rationale",
                "desired_outcome",
                "bounded_request",
                "evidence_refs",
                "constraints",
                "out_of_scope_boundaries",
                "success_proof",
                "expected_return_format",
                "suggested_executor_context",
                "skill_or_tool_hints",
                "capability_gap_notes",
                "handoff_notes",
                "confidence",
                "execution_performed",
            ],
            "properties": {
                "handoff_recommended": {"type": "boolean"},
                "no_handoff_reason": {"type": "string"},
                "title": {"type": "string", "minLength": 1},
                "rationale": {"type": "string", "minLength": 1},
                "desired_outcome": {"type": "string", "minLength": 1},
                "bounded_request": {"type": "string", "minLength": 1},
                "evidence_refs": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "out_of_scope_boundaries": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "success_proof": {"type": "string", "minLength": 1},
                "expected_return_format": {"type": "string", "minLength": 1},
                "suggested_executor_context": {"type": "string"},
                "skill_or_tool_hints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "capability_gap_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "handoff_notes": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "execution_performed": {"const": False},
            },
        }
    if "select Momentum attention" in prompt:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "selected_signal_id",
                "selected_signal_ref",
                "no_attention_needed",
                "selected_tension_ids",
                "attention_tier",
                "rationale",
                "why_now",
                "evidence_refs",
                "signal_refs",
                "recommended_next_action",
                "confidence",
                "source_refs",
            ],
            "properties": {
                "selected_signal_id": {"type": "string"},
                "selected_signal_ref": {"type": "string"},
                "no_attention_needed": {"type": "boolean"},
                "selected_tension_ids": {"type": "array", "items": {"type": "string"}},
                "attention_tier": {"enum": ["present", "urgent", "ambient", "silent"]},
                "rationale": {"type": "string"},
                "why_now": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "signal_refs": {"type": "array", "items": {"type": "string"}},
                "recommended_next_action": {
                    "enum": [
                        "extract_selected_signal",
                        "ask_human",
                        "update_understanding_only",
                        "no_action",
                    ]
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source_refs": {"type": "array", "items": {"type": "string"}},
            },
        }
    if "You extract the living shape of an idea into typed artifacts" in prompt:
        return _extraction_schema()
    raise ValueError("unrecognized Momentum proof prompt")


def _extraction_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["artifacts", "resident_patch", "judgment", "packet"],
        "properties": {
            "artifacts": {
                "type": "array",
                "minItems": 1,
                "items": _artifact_schema(),
            },
            "resident_patch": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "summary",
                    "reason",
                    "beliefs",
                    "constraints",
                    "corrections",
                    "source",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "beliefs": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "corrections": {"type": "array", "items": {"type": "string"}},
                    "source": _source_schema(),
                },
            },
            "judgment": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "environment_id",
                    "valkyrie_id",
                    "changed_understanding",
                    "tension_that_matters",
                    "why_attention_now",
                    "recommended_next_action",
                    "recommended_action",
                    "attention_tier",
                    "authority_boundary",
                    "operational_state",
                    "confidence",
                    "signal_refs",
                    "evidence_artifact_titles",
                    "target_surfaces",
                    "source",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "environment_id": {"type": "string", "minLength": 1},
                    "valkyrie_id": {"type": "string", "minLength": 1},
                    "changed_understanding": {"type": "string", "minLength": 1},
                    "tension_that_matters": {"type": "string", "minLength": 1},
                    "why_attention_now": {"type": "string", "minLength": 1},
                    "recommended_next_action": {
                        "enum": [
                            "write_momentum_packet",
                            "ask_human",
                            "update_understanding_only",
                        ]
                    },
                    "recommended_action": {"type": "string", "minLength": 1},
                    "attention_tier": {"enum": ["present", "urgent", "ambient", "silent"]},
                    "authority_boundary": {"type": "string", "minLength": 1},
                    "operational_state": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "signal_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "evidence_artifact_titles": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "target_surfaces": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "source": _source_schema(),
                },
            },
            "packet": {
                "anyOf": [
                    {"type": "null"},
                    _packet_schema(),
                ]
            },
        },
    }


def _artifact_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "title", "summary", "reason", "source", "tags"],
        "properties": {
            "kind": {
                "enum": [
                    "durable_insight",
                    "rejected_direction",
                    "unresolved_tension",
                    "resident_understanding_patch",
                ]
            },
            "title": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1},
            "source": _source_schema(),
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }


def _packet_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title",
            "implementation_slice",
            "why_it_matters",
            "caused_by",
            "must_not_lose",
            "reuse_guidance",
            "out_of_scope",
            "success_proof",
            "reflection_prompts",
            "source",
        ],
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "implementation_slice": {"type": "string", "minLength": 1},
            "why_it_matters": {"type": "string", "minLength": 1},
            "caused_by": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "must_not_lose": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "reuse_guidance": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "out_of_scope": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "success_proof": {"type": "string", "minLength": 1},
            "reflection_prompts": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "source": _source_schema(),
        },
    }


def _source_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["excerpt"],
        "properties": {
            "excerpt": {"type": "string", "minLength": 1},
            "line_start": {"type": "integer", "minimum": 1},
            "line_end": {"type": "integer", "minimum": 1},
        },
    }


if __name__ == "__main__":
    main()

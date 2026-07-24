"""Heuristic classification of resident inbox signals."""

from __future__ import annotations

import re
from typing import Any

from .models import ResidentInboxClassification, ResidentInboxSignal

_MEMORY_CLASSIFICATIONS = {
    ResidentInboxClassification.FACT.value,
    ResidentInboxClassification.PREFERENCE.value,
    ResidentInboxClassification.POLICY.value,
    ResidentInboxClassification.APPROVAL.value,
    ResidentInboxClassification.DENIAL.value,
    ResidentInboxClassification.CORRECTION.value,
    ResidentInboxClassification.RISK.value,
    ResidentInboxClassification.STATUS_UPDATE.value,
}

_WORK_CLASSIFICATIONS = {
    ResidentInboxClassification.TASK_REQUEST.value,
    ResidentInboxClassification.IDEA.value,
    ResidentInboxClassification.SOURCE_EVIDENCE.value,
    ResidentInboxClassification.URL_REFERENCE.value,
    ResidentInboxClassification.FILE_REFERENCE.value,
    ResidentInboxClassification.PHYSICAL_OBSERVATION.value,
}


def classify_inbox_signal(signal: ResidentInboxSignal) -> tuple[str, float, str]:
    return classify_text(signal.summary, payload=signal.payload)


# Ordered keyword rules: the first whose pattern matches the haystack wins.
# Precedence is encoded by position, so adding a classification is a table edit.
_CLASSIFICATION_RULES: tuple[tuple[str, ResidentInboxClassification, float, str], ...] = (
    (
        # bare "no" denies, but not approving phrases ("no problem", "no objections")
        r"\b(?:not approved|do not approve|don't approve|denied|deny)\b"
        r"|\bno\b(?!\s+(?:problem|worries|worry|objection|objections|issue|issues|biggie))",
        ResidentInboxClassification.DENIAL,
        0.9,
        "operator text denies approval",
    ),
    (
        r"\b(approved|yes|go ahead|proceed|allowed)\b",
        ResidentInboxClassification.APPROVAL,
        0.84,
        "operator text grants approval",
    ),
    (
        r"\b(policy|rule|always ask|never|must not|requires approval)\b",
        ResidentInboxClassification.POLICY,
        0.82,
        "message states a policy boundary",
    ),
    (
        r"\b(prefer|preference|i like|i hate|avoid)\b",
        ResidentInboxClassification.PREFERENCE,
        0.78,
        "message states a preference",
    ),
    (
        r"\b(correction|actually|instead|wrong|fix that)\b",
        ResidentInboxClassification.CORRECTION,
        0.72,
        "message corrects prior state",
    ),
    (
        r"\b(look into|please|can you|could you|todo|task|need you to|"
        r"do some research|research|investigate|find out|explore)\b",
        ResidentInboxClassification.TASK_REQUEST,
        0.75,
        "message asks for work",
    ),
    (
        r"\b(idea|maybe|what if|could add|opportunity)\b",
        ResidentInboxClassification.IDEA,
        0.68,
        "message suggests an idea",
    ),
    (
        r"\b(risk|unsafe|danger|customer data|private)\b",
        ResidentInboxClassification.RISK,
        0.76,
        "message identifies a risk",
    ),
    (
        r"\b(status|done|completed|blocked|waiting)\b",
        ResidentInboxClassification.STATUS_UPDATE,
        0.64,
        "message reports status",
    ),
    (
        r"(/[^\s]+|\b[a-z0-9_.-]+\.(md|json|yaml|yml|stl|3mf|py|ts|tsx)\b)",
        ResidentInboxClassification.FILE_REFERENCE,
        0.72,
        "message references a file",
    ),
)


def classify_text(text: str, *, payload: dict[str, Any] | None = None) -> tuple[str, float, str]:
    payload = payload or {}
    haystack = " ".join(
        str(item or "")
        for item in (
            text,
            payload.get("kind"),
            payload.get("signal_kind"),
            payload.get("source"),
            payload.get("evidence_ref"),
            payload.get("raw_payload_ref"),
        )
    ).casefold()
    if _contains_url(haystack):
        return (
            ResidentInboxClassification.SOURCE_EVIDENCE.value,
            0.86,
            "message contains a URL or public source reference",
        )
    if str(payload.get("kind") or "").startswith("signal.") or str(
        payload.get("signal_kind") or ""
    ):
        return (
            ResidentInboxClassification.SOURCE_EVIDENCE.value,
            0.78,
            "environment signal is source evidence for resident attention",
        )
    for pattern, classification, confidence, reason in _CLASSIFICATION_RULES:
        if re.search(pattern, haystack):
            return classification.value, confidence, reason
    return ResidentInboxClassification.FACT.value, 0.55, "message is useful resident context"


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.casefold())
    stop = {
        "and",
        "the",
        "for",
        "that",
        "this",
        "with",
        "into",
        "resident",
        "signal",
        "inbox",
        "from",
        "should",
        "could",
        "would",
    }
    return [word for word in words if word not in stop]


def _contains_url(text: str) -> bool:
    return bool(re.search(r"https?://|www\.|[a-z0-9.-]+\.(com|ca|net|org|io)\b", text))

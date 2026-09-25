"""The one wire contract a resident-built capability travels to peers in.

Before this module existed, the interactive ``build_tool`` path and the
autonomous resident-learning path each built the ``flock.learning.proposed``
event payload by hand, with different kwargs. ``build_tool``'s publisher
omitted ``test_code``, ``requirements``, and ``canary_sample`` entirely, so a
peer receiving one of its proposals had nothing to independently re-verify
and nothing to run a canary against. The receiving side never read
``builder_evidence`` either, so even when a proposal did carry it, it went
nowhere.

:class:`CapabilityProposal` is the single shape both publishers build and
every peer parses back with :meth:`CapabilityProposal.from_event_payload`.
There is no second, narrower publisher, and a peer that cannot parse a
proposal declines it with a recorded reason instead of installing whatever
partial data it did get.

Proposal *signing* — an unforgeable guarantee that the code a peer runs is
what the builder actually produced — is separate, later work. What this
module provides is a content-addressed digest: a peer recomputes it from the
bytes it actually received, which is tamper-evident for logging and dedupe,
never an integrity proof by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

#: A conservative PEP 508-shaped allowlist: a bare distribution name, optional
#: ``[extras]``, optional version-specifier clauses. Deliberately NOT the
#: full PEP 508 grammar — no environment markers, no direct URL/VCS
#: references (``git+...``, ``-e ...``, local paths) — because this string
#: goes straight to ``pip``/``uv install`` inside a peer's own verification
#: and execution boundary. A CLI-option-shaped entry (``--index-url=...``,
#: ``-e``) or a URL/path is refused outright rather than passed through.
_SAFE_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"  # distribution name
    r"(\[[A-Za-z0-9,._-]+\])?"  # optional extras
    r"("
    r"\s*(==|!=|<=|>=|~=|===|<|>)\s*[A-Za-z0-9.*+!-]+"
    r"(\s*,\s*(==|!=|<=|>=|~=|===|<|>)\s*[A-Za-z0-9.*+!-]+)*"
    r")?$"
)


class CapabilityProposalError(ValueError):
    """A capability proposal payload is malformed or incomplete.

    Raised by :meth:`CapabilityProposal.from_event_payload` when the payload
    cannot be trusted to build a runnable artifact from. The caller declines
    the proposal with this message as the reason — never a partial install.
    """


def compute_artifact_digest(
    *,
    tool_code: str,
    test_code: str,
    requirements: list[str],
    manifest: Mapping[str, Any],
) -> str:
    """The one digest computation, shared by :attr:`CapabilityProposal.
    artifact_digest` and the verification record a peer stamps into
    provenance — so "this exact digest was verified" and "this proposal's
    digest is" can never silently drift into two different hashes of the
    same fields.

    Not a signature or an integrity check against a sender's claim (that is
    separate, later work): this ties a resident's own verification record to
    the exact bytes it verified, recomputed by whoever asks, never trusted
    from a payload.
    """
    canonical = json.dumps(
        {
            "tool_code": tool_code,
            "test_code": test_code,
            "requirements": sorted(requirements),
            "manifest": dict(manifest),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_str(value: Any, field_name: str) -> str:
    """Strict string coercion: ``None``/missing become ``""``; anything else
    that is not already a ``str`` is rejected rather than silently
    ``str()``-coerced. A dict or list handed to ``tool_code`` would otherwise
    stringify into plausible-looking but garbage code."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CapabilityProposalError(
            f"capability proposal field {field_name!r} must be a string, got {type(value).__name__}"
        )
    return value


def _require_str_list(value: Any, field_name: str) -> list[str]:
    """Strict list-of-str coercion. A bare string must never be accepted here:
    iterating it character-by-character silently turns 'requests' into
    ['r', 'e', 'q', ...], each of which then fails/mangles installation."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise CapabilityProposalError(
            f"capability proposal field {field_name!r} must be a list, got {type(value).__name__}"
        )
    for item in value:
        if not isinstance(item, str):
            raise CapabilityProposalError(
                f"capability proposal field {field_name!r} entries must be strings, "
                f"got {type(item).__name__}"
            )
    return value


class CapabilityProposal(BaseModel):
    """Everything a peer needs to independently verify and run a capability.

    Both the resident install pipeline (``resident_learning``) and the
    interactive ``build_tool`` session tool build this record from what they
    have, and every peer parses it back. A field either travels in this
    contract or it does not exist on the other side.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    learning_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = ""
    artifact_type: str = Field(min_length=1)
    content: str = ""
    scope: str = ""
    domain: str = ""
    #: Domain used only to compute the scoped flock NATS fan-out subject, when
    #: it differs from ``domain`` (the artifact's own domain). Empty falls
    #: back to ``domain`` at publish time.
    subject_domain: str = ""
    confidence: float = 0.0
    redaction_status: str = ""
    promotion_id: str = ""
    flock_id: str = ""
    source_environment_id: str = ""
    source_valkyrie_id: str = ""
    artifact_path: str = ""

    tool_code: str = ""
    tool_entry_point: str = "run"
    learned_tool_manifest: dict[str, Any] = Field(default_factory=dict)
    #: Self-contained test module travelling with the proposal; peers re-run
    #: it independently before installing.
    test_code: str = ""
    #: pip requirement strings the tool needs ([] for stdlib-only tools).
    requirements: list[str] = Field(default_factory=list)
    #: A sample payload the peer canaries the installed tool against.
    canary_sample: dict[str, Any] = Field(default_factory=dict)
    #: artifact_id of the version this artifact replaces ("" for a first build).
    supersedes: str = ""

    #: The builder's own, self-reported claims (its review outcome, its own
    #: verification log, ...). Never trusted on its own — a peer re-verifies
    #: independently — but carried so a decline or an audit can see what the
    #: builder claimed.
    builder_evidence: dict[str, Any] = Field(default_factory=dict)

    review_outcome: str = ""
    correlation_id: str = ""
    causation_id: str = ""

    @field_validator("requirements")
    @classmethod
    def _validate_requirements(cls, value: list[str]) -> list[str]:
        for requirement in value:
            if not _SAFE_REQUIREMENT_RE.match(requirement.strip()):
                raise CapabilityProposalError(
                    f"unsafe or invalid requirement specifier: {requirement!r} — only a "
                    "plain distribution name with an optional version specifier is "
                    "accepted (no CLI options, URLs, VCS references, or paths)"
                )
        return value

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            raise CapabilityProposalError(f"confidence must be a finite number, got {value!r}")
        return value

    @model_validator(mode="after")
    def _require_tool_payload_for_agent_tool(self) -> CapabilityProposal:
        """An ``agent_tool`` proposal with no code or no named manifest is unusable.

        Refusing here — before a ``ResidentLearningArtifact`` is ever built
        from it — is what keeps a malformed proposal from reaching
        ``require_verified_artifact`` as a capability a peer can see listed
        but never run.
        """
        if self.artifact_type != "agent_tool":
            return self
        if not self.tool_code.strip():
            raise CapabilityProposalError(
                f"capability proposal {self.learning_id!r} declares "
                "artifact_type='agent_tool' but carries no tool_code"
            )
        manifest_name = str(self.learned_tool_manifest.get("name") or "").strip()
        if not manifest_name:
            raise CapabilityProposalError(
                f"capability proposal {self.learning_id!r} declares "
                "artifact_type='agent_tool' but its learned_tool_manifest has no name"
            )
        return self

    @property
    def artifact_digest(self) -> str:
        """Content-addressed digest of the code and tests a peer will run.

        Always recomputed from this proposal's own fields — never trusted
        from an incoming payload — so it reflects what was actually received,
        not what a sender claimed to send.
        """
        return compute_artifact_digest(
            tool_code=self.tool_code,
            test_code=self.test_code,
            requirements=self.requirements,
            manifest=self.learned_tool_manifest,
        )

    @classmethod
    def from_event_payload(cls, payload: Mapping[str, Any]) -> CapabilityProposal:
        """Strictly parse a ``flock.learning.proposed`` event payload.

        Raises :class:`CapabilityProposalError` on anything unparseable or
        missing what an ``agent_tool`` needs to run — the caller declines
        with the message as the reason rather than installing a partial
        artifact.
        """
        try:
            return cls(
                learning_id=_require_str(payload.get("learning_id"), "learning_id"),
                title=_require_str(payload.get("title") or payload.get("artifact_name"), "title"),
                summary=_require_str(payload.get("summary"), "summary"),
                artifact_type=_require_str(payload.get("artifact_type"), "artifact_type"),
                content=_require_str(
                    payload.get("content") or payload.get("artifact_content"), "content"
                ),
                scope=_require_str(payload.get("scope"), "scope"),
                domain=_require_str(payload.get("domain"), "domain"),
                confidence=float(payload.get("confidence") or 0.0),
                redaction_status=_require_str(payload.get("redaction_status"), "redaction_status"),
                promotion_id=_require_str(
                    payload.get("promotion_id") or payload.get("learning_id"), "promotion_id"
                ),
                flock_id=_require_str(payload.get("flock_id"), "flock_id"),
                source_environment_id=_require_str(
                    payload.get("source_environment_id") or payload.get("environment_id"),
                    "source_environment_id",
                ),
                source_valkyrie_id=_require_str(
                    payload.get("source_valkyrie_id"), "source_valkyrie_id"
                ),
                artifact_path=_require_str(
                    payload.get("artifact_path") or payload.get("promoted_path"), "artifact_path"
                ),
                # tool_code/test_code are never silently str()-coerced: a dict
                # or list handed here would otherwise stringify into
                # plausible-looking but garbage code that still clears the
                # "carries no tool_code" check.
                tool_code=_require_str(payload.get("tool_code"), "tool_code"),
                tool_entry_point=_require_str(payload.get("tool_entry_point"), "tool_entry_point")
                or "run",
                learned_tool_manifest=dict(payload.get("learned_tool_manifest") or {}),
                test_code=_require_str(payload.get("test_code"), "test_code"),
                requirements=_require_str_list(payload.get("requirements"), "requirements"),
                canary_sample=dict(payload.get("canary_sample") or {}),
                supersedes=_require_str(payload.get("supersedes"), "supersedes"),
                builder_evidence=dict(payload.get("builder_evidence") or {}),
                review_outcome=_require_str(payload.get("review_outcome"), "review_outcome"),
                correlation_id=_require_str(payload.get("correlation_id"), "correlation_id"),
                causation_id=_require_str(payload.get("causation_id"), "causation_id"),
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise CapabilityProposalError(f"malformed capability proposal: {exc}") from exc

    def to_event_payload(self) -> dict[str, Any]:
        """Flatten into the ``flock.learning.proposed`` event payload shape."""
        payload = self.model_dump(exclude={"subject_domain"})
        # Historical duplicate key some consumers read instead of "content".
        payload["artifact_content"] = self.content
        payload["artifact_digest"] = self.artifact_digest
        return payload

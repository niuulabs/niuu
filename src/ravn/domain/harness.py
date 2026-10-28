"""Provider-neutral contract types for Ravn execution harnesses.

The harness is the runtime boundary that executes one Ravn turn.  These types
model the part Ravn consumes; transport- or provider-specific controls remain
behind the adapter that implements :class:`ravn.ports.executor.RavnHarnessPort`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ravn.domain.checkpoint import InterruptReason
from ravn.domain.models import TurnResult


class HarnessKind(StrEnum):
    """The implementation family of a Ravn harness."""

    NATIVE = "native"
    CLI_BACKED = "cli_backed"


class HarnessCapability(StrEnum):
    """Provider-neutral features a Ravn harness can declare and require."""

    TOOLS = "tools"
    REASONING = "reasoning"
    ATTACHMENTS = "attachments"
    IMAGES = "images"
    INTERRUPT = "interrupt"
    STEERING = "steering"
    SESSION_RESUME = "session_resume"
    DYNAMIC_TOOLS = "dynamic_tools"


class SteeringMode(StrEnum):
    """How a harness applies an in-flight steering instruction."""

    NONE = "none"
    LIVE = "live"
    INTERRUPT_RESUME = "interrupt_resume"


class HarnessControlKind(StrEnum):
    """Controls Ravn may apply to an active harness turn."""

    INTERRUPT = "interrupt"
    STEER = "steer"


class HarnessContractError(ValueError):
    """Raised when a harness contract declaration is invalid."""


class UnsupportedHarnessCapabilityError(HarnessContractError):
    """Raised when a requested turn feature is absent from the declared profile."""


class HarnessRequirementError(HarnessContractError):
    """Raised when no selected harness satisfies declared requirements."""


class HarnessLifecycleError(RuntimeError):
    """Raised when a turn or control is attempted outside the harness lifecycle."""


@dataclass(frozen=True)
class HarnessCapabilityProfile:
    """Capabilities Ravn may consume from one harness implementation.

    ``supports_tools`` covers execution of Ravn ``ToolPort`` tools.  ``reasoning``
    is provider reasoning made available to the harness, not an assertion that a
    provider always emits it.  Attachments and images describe accepted turn
    input.  Controls only apply while a turn is active.  ``session_resume`` is
    an opaque provider-session continuation; Ravn's own ``Session`` remains
    provider-neutral.  Dynamic tools means the tool surface can change after
    harness startup.

    A profile is validated eagerly so composition fails deterministically before
    a runtime can be selected.
    """

    kind: HarnessKind
    supports_tools: bool
    supports_reasoning: bool
    supports_attachments: bool
    supports_images: bool
    supports_interrupt: bool
    steering_mode: SteeringMode
    supports_session_resume: bool
    supports_dynamic_tools: bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HarnessKind):
            raise HarnessContractError("harness kind must be a HarnessKind")
        if not isinstance(self.steering_mode, SteeringMode):
            raise HarnessContractError("steering mode must be a SteeringMode")
        for field_name in (
            "supports_tools",
            "supports_reasoning",
            "supports_attachments",
            "supports_images",
            "supports_interrupt",
            "supports_session_resume",
            "supports_dynamic_tools",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise HarnessContractError(f"{field_name} must be a bool")
        if self.supports_images and not self.supports_attachments:
            raise HarnessContractError("image input requires attachment input support")
        if self.supports_dynamic_tools and not self.supports_tools:
            raise HarnessContractError("dynamic tool exposure requires tool execution support")
        if self.steering_mode is SteeringMode.INTERRUPT_RESUME and not self.supports_interrupt:
            raise HarnessContractError("interrupt-resume steering requires interrupt support")

    @property
    def supports_steering(self) -> bool:
        """Whether the harness can apply a steering control during a turn."""
        return self.steering_mode is not SteeringMode.NONE

    @property
    def capabilities(self) -> frozenset[HarnessCapability]:
        """Return the canonical capability names supported by this profile."""
        capabilities: set[HarnessCapability] = set()
        if self.supports_tools:
            capabilities.add(HarnessCapability.TOOLS)
        if self.supports_reasoning:
            capabilities.add(HarnessCapability.REASONING)
        if self.supports_attachments:
            capabilities.add(HarnessCapability.ATTACHMENTS)
        if self.supports_images:
            capabilities.add(HarnessCapability.IMAGES)
        if self.supports_interrupt:
            capabilities.add(HarnessCapability.INTERRUPT)
        if self.supports_steering:
            capabilities.add(HarnessCapability.STEERING)
        if self.supports_session_resume:
            capabilities.add(HarnessCapability.SESSION_RESUME)
        if self.supports_dynamic_tools:
            capabilities.add(HarnessCapability.DYNAMIC_TOOLS)
        return frozenset(capabilities)

    def missing_requirements(
        self, requirements: frozenset[HarnessCapability]
    ) -> frozenset[HarnessCapability]:
        """Return every canonical requirement this profile cannot meet."""
        return requirements - self.capabilities

    @classmethod
    def native_ravn_agent(cls) -> HarnessCapabilityProfile:
        """Profile of the in-process ``RavnAgent`` turn loop."""
        return cls(
            kind=HarnessKind.NATIVE,
            supports_tools=True,
            supports_reasoning=True,
            supports_attachments=False,
            supports_images=False,
            supports_interrupt=True,
            steering_mode=SteeringMode.NONE,
            supports_session_resume=False,
            supports_dynamic_tools=True,
        )

    @classmethod
    def cli_backed(
        cls,
        *,
        supports_tools: bool = True,
        supports_reasoning: bool = False,
        supports_attachments: bool = False,
        supports_images: bool = False,
        supports_interrupt: bool = False,
        steering_mode: SteeringMode = SteeringMode.NONE,
        supports_session_resume: bool = False,
        supports_dynamic_tools: bool = False,
    ) -> HarnessCapabilityProfile:
        """Build a CLI-backed profile from declared transport capabilities.

        The caller supplies capability facts rather than a transport class, so
        Ravn never needs a concrete CLI implementation check to select behavior.
        """
        return cls(
            kind=HarnessKind.CLI_BACKED,
            supports_tools=supports_tools,
            supports_reasoning=supports_reasoning,
            supports_attachments=supports_attachments,
            supports_images=supports_images,
            supports_interrupt=supports_interrupt,
            steering_mode=steering_mode,
            supports_session_resume=supports_session_resume,
            supports_dynamic_tools=supports_dynamic_tools,
        )


def parse_harness_requirements(values: object) -> frozenset[HarnessCapability]:
    """Validate a config-facing collection of canonical capability names."""
    if values is None:
        return frozenset()
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise HarnessContractError("harness requires must be a list of canonical capabilities")

    requirements: set[HarnessCapability] = set()
    for value in values:
        if not isinstance(value, str):
            raise HarnessContractError("harness requirements must be capability names")
        try:
            capability = HarnessCapability(value)
        except ValueError as exc:
            supported = ", ".join(capability.value for capability in HarnessCapability)
            raise HarnessContractError(
                f"unknown harness capability {value!r}; supported capabilities: {supported}"
            ) from exc
        if capability in requirements:
            raise HarnessContractError(f"duplicate harness requirement {capability.value!r}")
        requirements.add(capability)
    return frozenset(requirements)


@dataclass(frozen=True)
class HarnessSelection:
    """Resolved harness facts suitable for config inspection and observability."""

    adapter: str
    runtime_identity: str
    profile: HarnessCapabilityProfile
    requirements: frozenset[HarnessCapability]
    reason: str
    model: str = ""

    @property
    def capabilities(self) -> frozenset[HarnessCapability]:
        """Return the selected harness's canonical capability set."""
        return self.profile.capabilities

    def with_model(self, model: str) -> HarnessSelection:
        """Attach the resolved model identity without retaining any prompt data."""
        return HarnessSelection(
            adapter=self.adapter,
            runtime_identity=self.runtime_identity,
            profile=self.profile,
            requirements=self.requirements,
            reason=self.reason,
            model=model,
        )

    def to_dict(self) -> dict[str, object]:
        """Return content-safe selection facts for existing inspection surfaces."""
        return {
            "adapter": self.adapter,
            "runtime_identity": self.runtime_identity,
            "model": self.model,
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "requires": sorted(capability.value for capability in self.requirements),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HarnessAttachment:
    """One opaque attachment accepted as part of a harness turn input."""

    media_type: str
    content: bytes
    name: str = ""


@dataclass(frozen=True)
class HarnessTurnInput:
    """Provider-neutral input for exactly one harness turn.

    ``provider_session_id`` is opaque to Ravn and is only meaningful when the
    profile declares ``supports_session_resume``.  Implementations reject
    attachments, images, or resume requests their profile does not declare.
    """

    user_input: str
    attachments: tuple[HarnessAttachment, ...] = ()
    provider_session_id: str | None = None
    recall_query: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_input, str):
            raise HarnessContractError("turn user_input must be a str")
        if not isinstance(self.attachments, tuple):
            raise HarnessContractError("turn attachments must be a tuple")
        if not all(isinstance(attachment, HarnessAttachment) for attachment in self.attachments):
            raise HarnessContractError("turn attachments must contain HarnessAttachment values")
        if self.provider_session_id is not None and not isinstance(self.provider_session_id, str):
            raise HarnessContractError("provider session id must be a str or None")
        if self.recall_query is not None and not isinstance(self.recall_query, str):
            raise HarnessContractError("turn recall_query must be a str or None")


@dataclass(frozen=True)
class HarnessControl:
    """A Ravn-owned control request for an active harness turn.

    ``INTERRUPT`` requires ``interrupt_reason``.  ``STEER`` requires nonblank
    ``content``.  Unsupported controls return ``False``; malformed controls
    raise :class:`HarnessContractError`.
    """

    kind: HarnessControlKind
    content: str = ""
    interrupt_reason: InterruptReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HarnessControlKind):
            raise HarnessContractError("control kind must be a HarnessControlKind")
        if not isinstance(self.content, str):
            raise HarnessContractError("control content must be a str")
        if self.interrupt_reason is not None and not isinstance(
            self.interrupt_reason, InterruptReason
        ):
            raise HarnessContractError("interrupt reason must be an InterruptReason")
        if self.kind is HarnessControlKind.INTERRUPT and self.interrupt_reason is None:
            raise HarnessContractError("interrupt control requires an interrupt reason")
        if self.kind is HarnessControlKind.INTERRUPT and self.content:
            raise HarnessContractError("interrupt control does not accept steering content")
        if self.kind is HarnessControlKind.STEER and not self.content.strip():
            raise HarnessContractError("steering control requires nonblank content")
        if self.kind is HarnessControlKind.STEER and self.interrupt_reason is not None:
            raise HarnessContractError("steering control does not accept an interrupt reason")


@dataclass(frozen=True)
class HarnessTurnResult:
    """The completed result of a harness turn.

    ``turn`` carries the existing public Ravn result shape.  A provider, tool,
    or cancellation failure is raised rather than represented as a successful
    result; a tool failure remains represented in ``turn.tool_results``.
    """

    turn: TurnResult
    provider_session_id: str | None = None
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

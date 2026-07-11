"""Validated request models for the resident Valkyrie API."""

from pydantic import BaseModel, Field


class HuddleSendRequest(BaseModel):
    huddleId: str  # noqa: N815
    body: str
    directedTo: list[str] = Field(default_factory=list)  # noqa: N815
    authorId: str  # noqa: N815


class HuddleJoinRequest(BaseModel):
    huddleId: str  # noqa: N815
    participantId: str  # noqa: N815
    displayName: str = ""  # noqa: N815
    action: str = "observe"
    targetFlockId: str = ""  # noqa: N815
    capabilities: list[str] = Field(default_factory=list)


class LearningDecisionRequest(BaseModel):
    learningId: str  # noqa: N815
    reason: str = ""
    operatorId: str = "operator"  # noqa: N815
    targetScope: str = ""  # noqa: N815
    canaryEnvironmentId: str = ""  # noqa: N815


#: Operator feedback verdicts accepted by the learning feedback endpoint.
LEARNING_FEEDBACK_VERDICTS = ("useful", "good_action", "bad_action", "dismissed", "wrong_tier")


class LearningFeedbackRequest(BaseModel):
    verdict: str
    reason: str = ""
    operatorId: str = "operator"  # noqa: N815
    targetScope: str = ""  # noqa: N815


class LearningReviseRequest(BaseModel):
    title: str = ""
    summary: str = ""
    content: str = ""
    reason: str = ""
    operatorId: str = "operator"  # noqa: N815


class AutonomyUpdateRequest(BaseModel):
    valkyrieId: str  # noqa: N815
    mode: str
    reason: str = ""
    participantId: str = ""  # noqa: N815

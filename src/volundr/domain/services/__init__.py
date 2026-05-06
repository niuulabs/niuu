"""Domain services package.

Re-exports all public names for backward compatibility so that
``from volundr.domain.services import SessionService`` continues to work.
"""

from __future__ import annotations

from .chronicle import ChronicleNotFoundError, ChronicleService
from .feature import FeatureModule, FeatureService, UserFeaturePreference
from .forge import ForgeService
from .git_workflow import ConfidenceScorer, GitWorkflowService
from .preset import PresetDuplicateNameError, PresetNotFoundError, PresetService
from .profile import (
    ForgeProfileService,
    ProfileNotFoundError,
    ProfileReadOnlyError,
    ProfileValidationError,
)
from .prompt import PromptNotFoundError, PromptService
from .repo import ProviderInfo, RepoService
from .session import (
    RepoValidationError,
    SessionAccessDeniedError,
    SessionNotFoundError,
    SessionService,
    SessionStateError,
)
from .stats import StatsService
from .tenant import TenantAlreadyExistsError, TenantNotFoundError, TenantService
from .token import SessionNotRunningError, TokenService
from .tracker import TrackerIssueNotFoundError, TrackerMappingNotFoundError, TrackerService
from .workspace import WorkspaceService

__all__ = [
    # Exceptions
    "ChronicleNotFoundError",
    "PresetDuplicateNameError",
    "PresetNotFoundError",
    "ProfileNotFoundError",
    "ProfileReadOnlyError",
    "ProfileValidationError",
    "PromptNotFoundError",
    "RepoValidationError",
    "SessionAccessDeniedError",
    "SessionNotFoundError",
    "SessionNotRunningError",
    "SessionStateError",
    "TenantAlreadyExistsError",
    "TenantNotFoundError",
    "TrackerIssueNotFoundError",
    "TrackerMappingNotFoundError",
    # Services
    "FeatureService",
    "ForgeService",
    "ChronicleService",
    "ConfidenceScorer",
    "ForgeProfileService",
    "PresetService",
    "GitWorkflowService",
    "PromptService",
    "RepoService",
    "SessionService",
    "StatsService",
    "TenantService",
    "TokenService",
    "TrackerService",
    "WorkspaceService",
    # Data classes
    "FeatureModule",
    "UserFeaturePreference",
    "ProviderInfo",
]

"""Domain services package.

Re-exports all public names for backward compatibility so that
``from volundr.domain.services import SessionService`` continues to work.
"""

from __future__ import annotations

from .chronicle import ChronicleNotFoundError, ChronicleService
from .external_sessions import (
    ExternalSessionAlreadyImportedError,
    ExternalSessionNotFoundError,
    ExternalSessionPathNotAllowedError,
    ExternalSessionProviderNotFoundError,
    ExternalSessionService,
    ExternalSessionWorkspaceError,
)
from .feature import FeatureModule, FeatureService, UserFeaturePreference
from .forge import ForgeService
from .git_workflow import ConfidenceScorer, GitWorkflowService
from .launch_spec import (
    LaunchSpecDuplicateNameError,
    LaunchSpecNotFoundError,
    LaunchSpecService,
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
from .session_archive import SessionArchiveNotAvailableError, SessionArchiveService
from .stats import StatsService
from .tenant import TenantAlreadyExistsError, TenantNotFoundError, TenantService
from .token import SessionNotRunningError, TokenService
from .tracker import TrackerIssueNotFoundError, TrackerMappingNotFoundError, TrackerService
from .transcript_rebuild import RebuildResult, rebuild_turns
from .workspace import WorkspaceService

__all__ = [
    # Exceptions
    "ChronicleNotFoundError",
    "ExternalSessionAlreadyImportedError",
    "ExternalSessionNotFoundError",
    "ExternalSessionPathNotAllowedError",
    "ExternalSessionProviderNotFoundError",
    "ExternalSessionWorkspaceError",
    "LaunchSpecDuplicateNameError",
    "LaunchSpecNotFoundError",
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
    "ExternalSessionService",
    "FeatureService",
    "ForgeService",
    "ChronicleService",
    "ConfidenceScorer",
    "LaunchSpecService",
    "GitWorkflowService",
    "PromptService",
    "RepoService",
    "SessionArchiveNotAvailableError",
    "SessionArchiveService",
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
    "RebuildResult",
    # Functions
    "rebuild_turns",
]

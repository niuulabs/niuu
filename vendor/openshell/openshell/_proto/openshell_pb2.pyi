import datetime

from . import datamodel_pb2 as _datamodel_pb2
from google.protobuf import duration_pb2 as _duration_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from . import options_pb2 as _options_pb2
from . import sandbox_pb2 as _sandbox_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ExtensionKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EXTENSION_KIND_UNSPECIFIED: _ClassVar[ExtensionKind]
    EXTENSION_KIND_COMPUTE_DRIVER: _ClassVar[ExtensionKind]
    EXTENSION_KIND_CREDENTIAL_DRIVER: _ClassVar[ExtensionKind]
    EXTENSION_KIND_GATEWAY_INTERCEPTOR: _ClassVar[ExtensionKind]
    EXTENSION_KIND_SUPERVISOR_MIDDLEWARE: _ClassVar[ExtensionKind]

class SandboxPhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SANDBOX_PHASE_UNSPECIFIED: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_PROVISIONING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_READY: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_ERROR: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_DELETING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_UNKNOWN: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_STOPPING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_STOPPED: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_STARTING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_COMPLETED: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_SUSPENDING: _ClassVar[SandboxPhase]
    SANDBOX_PHASE_SUSPENDED: _ClassVar[SandboxPhase]

class ProviderMutationKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_MUTATION_KIND_UNSPECIFIED: _ClassVar[ProviderMutationKind]
    PROVIDER_MUTATION_KIND_ATTACH: _ClassVar[ProviderMutationKind]
    PROVIDER_MUTATION_KIND_DETACH: _ClassVar[ProviderMutationKind]
    PROVIDER_MUTATION_KIND_UPDATE: _ClassVar[ProviderMutationKind]
    PROVIDER_MUTATION_KIND_OBSERVE: _ClassVar[ProviderMutationKind]

class ProviderReadinessState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_READINESS_STATE_UNSPECIFIED: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_PERSISTED: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_PENDING: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_READY: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_WITHHELD: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_REVOKED: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_FAILED: _ClassVar[ProviderReadinessState]
    PROVIDER_READINESS_STATE_SUPERSEDED: _ClassVar[ProviderReadinessState]

class ProviderReadinessReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_READINESS_REASON_UNSPECIFIED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_WAITING_FOR_SUPERVISOR: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_WAITING_FOR_CREDENTIALS: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_WAITING_FOR_POLICY: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_WAITING_FOR_PROCESS: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_UNSUPPORTED_SUPERVISOR: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_CREDENTIALS_WITHHELD: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_CREDENTIAL_INSTALL_FAILED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_POLICY_ACTIVATION_FAILED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_PROCESS_INSTALL_FAILED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_SUPERVISOR_DISCONNECTED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_SUPERVISOR_LEASE_EXPIRED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_DESIRED_STATE_CHANGED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_CREDENTIAL_EXPIRED: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_LOCAL_POLICY: _ClassVar[ProviderReadinessReason]
    PROVIDER_READINESS_REASON_SNAPSHOT_MISMATCH: _ClassVar[ProviderReadinessReason]

class ConfigComponent(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONFIG_COMPONENT_UNSPECIFIED: _ClassVar[ConfigComponent]
    CONFIG_COMPONENT_SANDBOX_CONFIG: _ClassVar[ConfigComponent]
    CONFIG_COMPONENT_PROVIDER_ENVIRONMENT: _ClassVar[ConfigComponent]

class ConfigApplyOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONFIG_APPLY_OUTCOME_UNSPECIFIED: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_APPLIED: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_IGNORED_DUPLICATE: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_IGNORED_STALE: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_RETAINED_LOCAL_OVERRIDE: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_DEGRADED: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_FAILED_RETAINED_LAST_KNOWN_GOOD: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_FAILED_CLOSED: _ClassVar[ConfigApplyOutcome]
    CONFIG_APPLY_OUTCOME_UNSUPPORTED: _ClassVar[ConfigApplyOutcome]

class ConfigUpdateOperationState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONFIG_UPDATE_OPERATION_STATE_UNSPECIFIED: _ClassVar[ConfigUpdateOperationState]
    CONFIG_UPDATE_OPERATION_STATE_PENDING: _ClassVar[ConfigUpdateOperationState]
    CONFIG_UPDATE_OPERATION_STATE_APPLIED: _ClassVar[ConfigUpdateOperationState]
    CONFIG_UPDATE_OPERATION_STATE_INACTIVE: _ClassVar[ConfigUpdateOperationState]
    CONFIG_UPDATE_OPERATION_STATE_FAILED: _ClassVar[ConfigUpdateOperationState]
    CONFIG_UPDATE_OPERATION_STATE_SUPERSEDED: _ClassVar[ConfigUpdateOperationState]
    CONFIG_UPDATE_OPERATION_STATE_CANCELLED: _ClassVar[ConfigUpdateOperationState]

class ProviderCredentialTokenGrantType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_UNSPECIFIED: _ClassVar[ProviderCredentialTokenGrantType]
    PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_CLIENT_CREDENTIALS: _ClassVar[ProviderCredentialTokenGrantType]
    PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_TOKEN_EXCHANGE: _ClassVar[ProviderCredentialTokenGrantType]

class ProviderCredentialRefreshStrategy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_UNSPECIFIED: _ClassVar[ProviderCredentialRefreshStrategy]
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_STATIC: _ClassVar[ProviderCredentialRefreshStrategy]
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_EXTERNAL: _ClassVar[ProviderCredentialRefreshStrategy]
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_OAUTH2_REFRESH_TOKEN: _ClassVar[ProviderCredentialRefreshStrategy]
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_OAUTH2_CLIENT_CREDENTIALS: _ClassVar[ProviderCredentialRefreshStrategy]
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_GOOGLE_SERVICE_ACCOUNT_JWT: _ClassVar[ProviderCredentialRefreshStrategy]
    PROVIDER_CREDENTIAL_REFRESH_STRATEGY_AWS_STS_ASSUME_ROLE: _ClassVar[ProviderCredentialRefreshStrategy]

class ProviderProfileCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_PROFILE_CATEGORY_UNSPECIFIED: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_OTHER: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_INFERENCE: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_AGENT: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_SOURCE_CONTROL: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_MESSAGING: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_DATA: _ClassVar[ProviderProfileCategory]
    PROVIDER_PROFILE_CATEGORY_KNOWLEDGE: _ClassVar[ProviderProfileCategory]

class ProviderEnvironmentValueClassification(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_ENVIRONMENT_VALUE_CLASSIFICATION_UNSPECIFIED: _ClassVar[ProviderEnvironmentValueClassification]
    PROVIDER_ENVIRONMENT_VALUE_CLASSIFICATION_NON_SECRET: _ClassVar[ProviderEnvironmentValueClassification]
    PROVIDER_ENVIRONMENT_VALUE_CLASSIFICATION_STATIC_CREDENTIAL: _ClassVar[ProviderEnvironmentValueClassification]

class ConfigurationAdmissionState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONFIGURATION_ADMISSION_STATE_UNSPECIFIED: _ClassVar[ConfigurationAdmissionState]
    CONFIGURATION_ADMISSION_STATE_PENDING: _ClassVar[ConfigurationAdmissionState]
    CONFIGURATION_ADMISSION_STATE_ACCEPTED: _ClassVar[ConfigurationAdmissionState]
    CONFIGURATION_ADMISSION_STATE_REJECTED: _ClassVar[ConfigurationAdmissionState]

class PolicyStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    POLICY_STATUS_UNSPECIFIED: _ClassVar[PolicyStatus]
    POLICY_STATUS_PENDING: _ClassVar[PolicyStatus]
    POLICY_STATUS_LOADED: _ClassVar[PolicyStatus]
    POLICY_STATUS_FAILED: _ClassVar[PolicyStatus]
    POLICY_STATUS_SUPERSEDED: _ClassVar[PolicyStatus]

class ServiceStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SERVICE_STATUS_UNSPECIFIED: _ClassVar[ServiceStatus]
    SERVICE_STATUS_HEALTHY: _ClassVar[ServiceStatus]
    SERVICE_STATUS_DEGRADED: _ClassVar[ServiceStatus]
    SERVICE_STATUS_UNHEALTHY: _ClassVar[ServiceStatus]

class WorkspaceRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WORKSPACE_ROLE_UNSPECIFIED: _ClassVar[WorkspaceRole]
    WORKSPACE_ROLE_USER: _ClassVar[WorkspaceRole]
    WORKSPACE_ROLE_ADMIN: _ClassVar[WorkspaceRole]

class ProviderCredentialRefreshRecoveryAction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_UNSPECIFIED: _ClassVar[ProviderCredentialRefreshRecoveryAction]
    PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_RETRY: _ClassVar[ProviderCredentialRefreshRecoveryAction]
    PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_REAUTHORIZE: _ClassVar[ProviderCredentialRefreshRecoveryAction]
    PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_FIX_CONFIGURATION: _ClassVar[ProviderCredentialRefreshRecoveryAction]
    PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_INVESTIGATE: _ClassVar[ProviderCredentialRefreshRecoveryAction]

class SandboxRestartPolicy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SANDBOX_RESTART_POLICY_UNSPECIFIED: _ClassVar[SandboxRestartPolicy]
    SANDBOX_RESTART_POLICY_NEVER: _ClassVar[SandboxRestartPolicy]
    SANDBOX_RESTART_POLICY_ON_FAILURE: _ClassVar[SandboxRestartPolicy]
    SANDBOX_RESTART_POLICY_ALWAYS: _ClassVar[SandboxRestartPolicy]

class DeletionOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DELETION_OUTCOME_UNSPECIFIED: _ClassVar[DeletionOutcome]
    DELETION_OUTCOME_COMPLETED: _ClassVar[DeletionOutcome]
    DELETION_OUTCOME_ACCEPTED: _ClassVar[DeletionOutcome]
    DELETION_OUTCOME_ALREADY_ABSENT: _ClassVar[DeletionOutcome]

class EndpointResult(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ENDPOINT_RESULT_UNSPECIFIED: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_NO_OBSERVED_EXCHANGE: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_HTTP_RESPONSE_RECEIVED: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_POLICY_DENIED: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_CREDENTIAL_UNAVAILABLE: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_TLS_FAILED: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_TRANSPORT_FAILED: _ClassVar[EndpointResult]
    ENDPOINT_RESULT_UPSTREAM_REJECTED: _ClassVar[EndpointResult]

class ServiceAuthorizationMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SERVICE_AUTHORIZATION_MODE_UNSPECIFIED: _ClassVar[ServiceAuthorizationMode]
    SERVICE_AUTHORIZATION_MODE_STRIP: _ClassVar[ServiceAuthorizationMode]
    SERVICE_AUTHORIZATION_MODE_BEARER_PASSTHROUGH: _ClassVar[ServiceAuthorizationMode]

class EndpointFailureDomain(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ENDPOINT_FAILURE_DOMAIN_UNSPECIFIED: _ClassVar[EndpointFailureDomain]
    ENDPOINT_FAILURE_DOMAIN_TOKEN_GRANT: _ClassVar[EndpointFailureDomain]
EXTENSION_KIND_UNSPECIFIED: ExtensionKind
EXTENSION_KIND_COMPUTE_DRIVER: ExtensionKind
EXTENSION_KIND_CREDENTIAL_DRIVER: ExtensionKind
EXTENSION_KIND_GATEWAY_INTERCEPTOR: ExtensionKind
EXTENSION_KIND_SUPERVISOR_MIDDLEWARE: ExtensionKind
SANDBOX_PHASE_UNSPECIFIED: SandboxPhase
SANDBOX_PHASE_PROVISIONING: SandboxPhase
SANDBOX_PHASE_READY: SandboxPhase
SANDBOX_PHASE_ERROR: SandboxPhase
SANDBOX_PHASE_DELETING: SandboxPhase
SANDBOX_PHASE_UNKNOWN: SandboxPhase
SANDBOX_PHASE_STOPPING: SandboxPhase
SANDBOX_PHASE_STOPPED: SandboxPhase
SANDBOX_PHASE_STARTING: SandboxPhase
SANDBOX_PHASE_COMPLETED: SandboxPhase
SANDBOX_PHASE_SUSPENDING: SandboxPhase
SANDBOX_PHASE_SUSPENDED: SandboxPhase
PROVIDER_MUTATION_KIND_UNSPECIFIED: ProviderMutationKind
PROVIDER_MUTATION_KIND_ATTACH: ProviderMutationKind
PROVIDER_MUTATION_KIND_DETACH: ProviderMutationKind
PROVIDER_MUTATION_KIND_UPDATE: ProviderMutationKind
PROVIDER_MUTATION_KIND_OBSERVE: ProviderMutationKind
PROVIDER_READINESS_STATE_UNSPECIFIED: ProviderReadinessState
PROVIDER_READINESS_STATE_PERSISTED: ProviderReadinessState
PROVIDER_READINESS_STATE_PENDING: ProviderReadinessState
PROVIDER_READINESS_STATE_READY: ProviderReadinessState
PROVIDER_READINESS_STATE_WITHHELD: ProviderReadinessState
PROVIDER_READINESS_STATE_REVOKED: ProviderReadinessState
PROVIDER_READINESS_STATE_FAILED: ProviderReadinessState
PROVIDER_READINESS_STATE_SUPERSEDED: ProviderReadinessState
PROVIDER_READINESS_REASON_UNSPECIFIED: ProviderReadinessReason
PROVIDER_READINESS_REASON_WAITING_FOR_SUPERVISOR: ProviderReadinessReason
PROVIDER_READINESS_REASON_WAITING_FOR_CREDENTIALS: ProviderReadinessReason
PROVIDER_READINESS_REASON_WAITING_FOR_POLICY: ProviderReadinessReason
PROVIDER_READINESS_REASON_WAITING_FOR_PROCESS: ProviderReadinessReason
PROVIDER_READINESS_REASON_UNSUPPORTED_SUPERVISOR: ProviderReadinessReason
PROVIDER_READINESS_REASON_CREDENTIALS_WITHHELD: ProviderReadinessReason
PROVIDER_READINESS_REASON_CREDENTIAL_INSTALL_FAILED: ProviderReadinessReason
PROVIDER_READINESS_REASON_POLICY_ACTIVATION_FAILED: ProviderReadinessReason
PROVIDER_READINESS_REASON_PROCESS_INSTALL_FAILED: ProviderReadinessReason
PROVIDER_READINESS_REASON_SUPERVISOR_DISCONNECTED: ProviderReadinessReason
PROVIDER_READINESS_REASON_SUPERVISOR_LEASE_EXPIRED: ProviderReadinessReason
PROVIDER_READINESS_REASON_DESIRED_STATE_CHANGED: ProviderReadinessReason
PROVIDER_READINESS_REASON_CREDENTIAL_EXPIRED: ProviderReadinessReason
PROVIDER_READINESS_REASON_LOCAL_POLICY: ProviderReadinessReason
PROVIDER_READINESS_REASON_SNAPSHOT_MISMATCH: ProviderReadinessReason
CONFIG_COMPONENT_UNSPECIFIED: ConfigComponent
CONFIG_COMPONENT_SANDBOX_CONFIG: ConfigComponent
CONFIG_COMPONENT_PROVIDER_ENVIRONMENT: ConfigComponent
CONFIG_APPLY_OUTCOME_UNSPECIFIED: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_APPLIED: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_IGNORED_DUPLICATE: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_IGNORED_STALE: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_RETAINED_LOCAL_OVERRIDE: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_DEGRADED: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_FAILED_RETAINED_LAST_KNOWN_GOOD: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_FAILED_CLOSED: ConfigApplyOutcome
CONFIG_APPLY_OUTCOME_UNSUPPORTED: ConfigApplyOutcome
CONFIG_UPDATE_OPERATION_STATE_UNSPECIFIED: ConfigUpdateOperationState
CONFIG_UPDATE_OPERATION_STATE_PENDING: ConfigUpdateOperationState
CONFIG_UPDATE_OPERATION_STATE_APPLIED: ConfigUpdateOperationState
CONFIG_UPDATE_OPERATION_STATE_INACTIVE: ConfigUpdateOperationState
CONFIG_UPDATE_OPERATION_STATE_FAILED: ConfigUpdateOperationState
CONFIG_UPDATE_OPERATION_STATE_SUPERSEDED: ConfigUpdateOperationState
CONFIG_UPDATE_OPERATION_STATE_CANCELLED: ConfigUpdateOperationState
PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_UNSPECIFIED: ProviderCredentialTokenGrantType
PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_CLIENT_CREDENTIALS: ProviderCredentialTokenGrantType
PROVIDER_CREDENTIAL_TOKEN_GRANT_TYPE_TOKEN_EXCHANGE: ProviderCredentialTokenGrantType
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_UNSPECIFIED: ProviderCredentialRefreshStrategy
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_STATIC: ProviderCredentialRefreshStrategy
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_EXTERNAL: ProviderCredentialRefreshStrategy
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_OAUTH2_REFRESH_TOKEN: ProviderCredentialRefreshStrategy
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_OAUTH2_CLIENT_CREDENTIALS: ProviderCredentialRefreshStrategy
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_GOOGLE_SERVICE_ACCOUNT_JWT: ProviderCredentialRefreshStrategy
PROVIDER_CREDENTIAL_REFRESH_STRATEGY_AWS_STS_ASSUME_ROLE: ProviderCredentialRefreshStrategy
PROVIDER_PROFILE_CATEGORY_UNSPECIFIED: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_OTHER: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_INFERENCE: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_AGENT: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_SOURCE_CONTROL: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_MESSAGING: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_DATA: ProviderProfileCategory
PROVIDER_PROFILE_CATEGORY_KNOWLEDGE: ProviderProfileCategory
PROVIDER_ENVIRONMENT_VALUE_CLASSIFICATION_UNSPECIFIED: ProviderEnvironmentValueClassification
PROVIDER_ENVIRONMENT_VALUE_CLASSIFICATION_NON_SECRET: ProviderEnvironmentValueClassification
PROVIDER_ENVIRONMENT_VALUE_CLASSIFICATION_STATIC_CREDENTIAL: ProviderEnvironmentValueClassification
CONFIGURATION_ADMISSION_STATE_UNSPECIFIED: ConfigurationAdmissionState
CONFIGURATION_ADMISSION_STATE_PENDING: ConfigurationAdmissionState
CONFIGURATION_ADMISSION_STATE_ACCEPTED: ConfigurationAdmissionState
CONFIGURATION_ADMISSION_STATE_REJECTED: ConfigurationAdmissionState
POLICY_STATUS_UNSPECIFIED: PolicyStatus
POLICY_STATUS_PENDING: PolicyStatus
POLICY_STATUS_LOADED: PolicyStatus
POLICY_STATUS_FAILED: PolicyStatus
POLICY_STATUS_SUPERSEDED: PolicyStatus
SERVICE_STATUS_UNSPECIFIED: ServiceStatus
SERVICE_STATUS_HEALTHY: ServiceStatus
SERVICE_STATUS_DEGRADED: ServiceStatus
SERVICE_STATUS_UNHEALTHY: ServiceStatus
WORKSPACE_ROLE_UNSPECIFIED: WorkspaceRole
WORKSPACE_ROLE_USER: WorkspaceRole
WORKSPACE_ROLE_ADMIN: WorkspaceRole
PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_UNSPECIFIED: ProviderCredentialRefreshRecoveryAction
PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_RETRY: ProviderCredentialRefreshRecoveryAction
PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_REAUTHORIZE: ProviderCredentialRefreshRecoveryAction
PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_FIX_CONFIGURATION: ProviderCredentialRefreshRecoveryAction
PROVIDER_CREDENTIAL_REFRESH_RECOVERY_ACTION_INVESTIGATE: ProviderCredentialRefreshRecoveryAction
SANDBOX_RESTART_POLICY_UNSPECIFIED: SandboxRestartPolicy
SANDBOX_RESTART_POLICY_NEVER: SandboxRestartPolicy
SANDBOX_RESTART_POLICY_ON_FAILURE: SandboxRestartPolicy
SANDBOX_RESTART_POLICY_ALWAYS: SandboxRestartPolicy
DELETION_OUTCOME_UNSPECIFIED: DeletionOutcome
DELETION_OUTCOME_COMPLETED: DeletionOutcome
DELETION_OUTCOME_ACCEPTED: DeletionOutcome
DELETION_OUTCOME_ALREADY_ABSENT: DeletionOutcome
ENDPOINT_RESULT_UNSPECIFIED: EndpointResult
ENDPOINT_RESULT_NO_OBSERVED_EXCHANGE: EndpointResult
ENDPOINT_RESULT_HTTP_RESPONSE_RECEIVED: EndpointResult
ENDPOINT_RESULT_POLICY_DENIED: EndpointResult
ENDPOINT_RESULT_CREDENTIAL_UNAVAILABLE: EndpointResult
ENDPOINT_RESULT_TLS_FAILED: EndpointResult
ENDPOINT_RESULT_TRANSPORT_FAILED: EndpointResult
ENDPOINT_RESULT_UPSTREAM_REJECTED: EndpointResult
SERVICE_AUTHORIZATION_MODE_UNSPECIFIED: ServiceAuthorizationMode
SERVICE_AUTHORIZATION_MODE_STRIP: ServiceAuthorizationMode
SERVICE_AUTHORIZATION_MODE_BEARER_PASSTHROUGH: ServiceAuthorizationMode
ENDPOINT_FAILURE_DOMAIN_UNSPECIFIED: EndpointFailureDomain
ENDPOINT_FAILURE_DOMAIN_TOKEN_GRANT: EndpointFailureDomain

class IssueSandboxTokenRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IssueSandboxTokenResponse(_message.Message):
    __slots__ = ("token", "expiration_time")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    token: str
    expiration_time: _timestamp_pb2.Timestamp
    def __init__(self, token: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RefreshSandboxTokenRequest(_message.Message):
    __slots__ = ("extension_service_names",)
    EXTENSION_SERVICE_NAMES_FIELD_NUMBER: _ClassVar[int]
    extension_service_names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, extension_service_names: _Optional[_Iterable[str]] = ...) -> None: ...

class RefreshSandboxTokenResponse(_message.Message):
    __slots__ = ("token", "expiration_time", "extension_credentials", "sandbox_token", "sandbox_expiration_time", "session_id", "credential_epoch")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_TOKEN_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_EPOCH_FIELD_NUMBER: _ClassVar[int]
    token: str
    expiration_time: _timestamp_pb2.Timestamp
    extension_credentials: _containers.RepeatedCompositeFieldContainer[ExtensionServiceCredential]
    sandbox_token: str
    sandbox_expiration_time: _timestamp_pb2.Timestamp
    session_id: str
    credential_epoch: int
    def __init__(self, token: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., extension_credentials: _Optional[_Iterable[_Union[ExtensionServiceCredential, _Mapping]]] = ..., sandbox_token: _Optional[str] = ..., sandbox_expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., session_id: _Optional[str] = ..., credential_epoch: _Optional[int] = ...) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("status", "version")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    status: ServiceStatus
    version: str
    def __init__(self, status: _Optional[_Union[ServiceStatus, str]] = ..., version: _Optional[str] = ...) -> None: ...

class GetCurrentUserRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetCurrentUserResponse(_message.Message):
    __slots__ = ("subject", "display_name", "roles", "scopes", "identity_provider")
    SUBJECT_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    ROLES_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_PROVIDER_FIELD_NUMBER: _ClassVar[int]
    subject: str
    display_name: str
    roles: _containers.RepeatedScalarFieldContainer[str]
    scopes: _containers.RepeatedScalarFieldContainer[str]
    identity_provider: str
    def __init__(self, subject: _Optional[str] = ..., display_name: _Optional[str] = ..., roles: _Optional[_Iterable[str]] = ..., scopes: _Optional[_Iterable[str]] = ..., identity_provider: _Optional[str] = ...) -> None: ...

class GetGatewayInfoRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetGatewayInfoResponse(_message.Message):
    __slots__ = ("status", "gateway_version", "compute_drivers", "extensions")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_VERSION_FIELD_NUMBER: _ClassVar[int]
    COMPUTE_DRIVERS_FIELD_NUMBER: _ClassVar[int]
    EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    status: ServiceStatus
    gateway_version: str
    compute_drivers: _containers.RepeatedCompositeFieldContainer[ComputeDriverInfo]
    extensions: _containers.RepeatedCompositeFieldContainer[NegotiatedExtensionInfo]
    def __init__(self, status: _Optional[_Union[ServiceStatus, str]] = ..., gateway_version: _Optional[str] = ..., compute_drivers: _Optional[_Iterable[_Union[ComputeDriverInfo, _Mapping]]] = ..., extensions: _Optional[_Iterable[_Union[NegotiatedExtensionInfo, _Mapping]]] = ...) -> None: ...

class NegotiatedExtensionInfo(_message.Message):
    __slots__ = ("kind", "configured_name", "implementation_name", "implementation_version", "protocol_major", "protocol_minor", "supported_capabilities", "required_capabilities")
    KIND_FIELD_NUMBER: _ClassVar[int]
    CONFIGURED_NAME_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_NAME_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTATION_VERSION_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_MAJOR_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_MINOR_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    kind: ExtensionKind
    configured_name: str
    implementation_name: str
    implementation_version: str
    protocol_major: int
    protocol_minor: int
    supported_capabilities: _containers.RepeatedScalarFieldContainer[str]
    required_capabilities: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, kind: _Optional[_Union[ExtensionKind, str]] = ..., configured_name: _Optional[str] = ..., implementation_name: _Optional[str] = ..., implementation_version: _Optional[str] = ..., protocol_major: _Optional[int] = ..., protocol_minor: _Optional[int] = ..., supported_capabilities: _Optional[_Iterable[str]] = ..., required_capabilities: _Optional[_Iterable[str]] = ...) -> None: ...

class ComputeDriverInfo(_message.Message):
    __slots__ = ("name", "capabilities")
    NAME_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    name: str
    capabilities: ComputeDriverCapabilities
    def __init__(self, name: _Optional[str] = ..., capabilities: _Optional[_Union[ComputeDriverCapabilities, _Mapping]] = ...) -> None: ...

class ComputeDriverCapabilities(_message.Message):
    __slots__ = ("driver_name", "driver_version", "resource_capabilities")
    DRIVER_NAME_FIELD_NUMBER: _ClassVar[int]
    DRIVER_VERSION_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    driver_name: str
    driver_version: str
    resource_capabilities: ResourceCapabilities
    def __init__(self, driver_name: _Optional[str] = ..., driver_version: _Optional[str] = ..., resource_capabilities: _Optional[_Union[ResourceCapabilities, _Mapping]] = ...) -> None: ...

class ResourceCapabilities(_message.Message):
    __slots__ = ("cpu", "memory", "gpu")
    CPU_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    GPU_FIELD_NUMBER: _ClassVar[int]
    cpu: CpuResourceCapabilities
    memory: MemoryResourceCapabilities
    gpu: GpuResourceCapabilities
    def __init__(self, cpu: _Optional[_Union[CpuResourceCapabilities, _Mapping]] = ..., memory: _Optional[_Union[MemoryResourceCapabilities, _Mapping]] = ..., gpu: _Optional[_Union[GpuResourceCapabilities, _Mapping]] = ...) -> None: ...

class CpuResourceCapabilities(_message.Message):
    __slots__ = ("limit_supported",)
    LIMIT_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    limit_supported: bool
    def __init__(self, limit_supported: bool = ...) -> None: ...

class MemoryResourceCapabilities(_message.Message):
    __slots__ = ("limit_supported",)
    LIMIT_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    limit_supported: bool
    def __init__(self, limit_supported: bool = ...) -> None: ...

class GpuResourceCapabilities(_message.Message):
    __slots__ = ("default_selection_supported", "count_selection_supported")
    DEFAULT_SELECTION_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    COUNT_SELECTION_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    default_selection_supported: bool
    count_selection_supported: bool
    def __init__(self, default_selection_supported: bool = ..., count_selection_supported: bool = ...) -> None: ...

class Sandbox(_message.Message):
    __slots__ = ("metadata", "spec", "status", "host_key_fingerprint", "created_from_workload_template")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    HOST_KEY_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    CREATED_FROM_WORKLOAD_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    metadata: _datamodel_pb2.ObjectMeta
    spec: SandboxSpec
    status: SandboxStatus
    host_key_fingerprint: str
    created_from_workload_template: SandboxWorkloadTemplateProvenance
    def __init__(self, metadata: _Optional[_Union[_datamodel_pb2.ObjectMeta, _Mapping]] = ..., spec: _Optional[_Union[SandboxSpec, _Mapping]] = ..., status: _Optional[_Union[SandboxStatus, _Mapping]] = ..., host_key_fingerprint: _Optional[str] = ..., created_from_workload_template: _Optional[_Union[SandboxWorkloadTemplateProvenance, _Mapping]] = ...) -> None: ...

class SandboxSpec(_message.Message):
    __slots__ = ("log_level", "environment", "template", "policy", "providers", "resource_requirements", "command", "tty", "provider_attachment_epoch", "restart_policy")
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    LOG_LEVEL_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_REQUIREMENTS_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    TTY_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ATTACHMENT_EPOCH_FIELD_NUMBER: _ClassVar[int]
    RESTART_POLICY_FIELD_NUMBER: _ClassVar[int]
    log_level: str
    environment: _containers.ScalarMap[str, str]
    template: SandboxTemplate
    policy: _sandbox_pb2.SandboxPolicy
    providers: _containers.RepeatedScalarFieldContainer[str]
    resource_requirements: ResourceRequirements
    command: _containers.RepeatedScalarFieldContainer[str]
    tty: bool
    provider_attachment_epoch: str
    restart_policy: SandboxRestartPolicy
    def __init__(self, log_level: _Optional[str] = ..., environment: _Optional[_Mapping[str, str]] = ..., template: _Optional[_Union[SandboxTemplate, _Mapping]] = ..., policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ..., providers: _Optional[_Iterable[str]] = ..., resource_requirements: _Optional[_Union[ResourceRequirements, _Mapping]] = ..., command: _Optional[_Iterable[str]] = ..., tty: bool = ..., provider_attachment_epoch: _Optional[str] = ..., restart_policy: _Optional[_Union[SandboxRestartPolicy, str]] = ...) -> None: ...

class ResourceRequirements(_message.Message):
    __slots__ = ("gpu",)
    GPU_FIELD_NUMBER: _ClassVar[int]
    gpu: GpuResourceRequirements
    def __init__(self, gpu: _Optional[_Union[GpuResourceRequirements, _Mapping]] = ...) -> None: ...

class GpuResourceRequirements(_message.Message):
    __slots__ = ("count",)
    COUNT_FIELD_NUMBER: _ClassVar[int]
    count: int
    def __init__(self, count: _Optional[int] = ...) -> None: ...

class SandboxTemplate(_message.Message):
    __slots__ = ("image", "runtime_class_name", "agent_socket", "labels", "annotations", "environment", "resources", "user_namespaces", "driver_config")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class AnnotationsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    RUNTIME_CLASS_NAME_FIELD_NUMBER: _ClassVar[int]
    AGENT_SOCKET_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    USER_NAMESPACES_FIELD_NUMBER: _ClassVar[int]
    DRIVER_CONFIG_FIELD_NUMBER: _ClassVar[int]
    image: str
    runtime_class_name: str
    agent_socket: str
    labels: _containers.ScalarMap[str, str]
    annotations: _containers.ScalarMap[str, str]
    environment: _containers.ScalarMap[str, str]
    resources: _struct_pb2.Struct
    user_namespaces: bool
    driver_config: _struct_pb2.Struct
    def __init__(self, image: _Optional[str] = ..., runtime_class_name: _Optional[str] = ..., agent_socket: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., annotations: _Optional[_Mapping[str, str]] = ..., environment: _Optional[_Mapping[str, str]] = ..., resources: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., user_namespaces: bool = ..., driver_config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class SandboxWorkloadTemplate(_message.Message):
    __slots__ = ("metadata", "spec")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    metadata: _datamodel_pb2.ObjectMeta
    spec: SandboxWorkloadTemplateSpec
    def __init__(self, metadata: _Optional[_Union[_datamodel_pb2.ObjectMeta, _Mapping]] = ..., spec: _Optional[_Union[SandboxWorkloadTemplateSpec, _Mapping]] = ...) -> None: ...

class SandboxWorkloadTemplateSpec(_message.Message):
    __slots__ = ("workload", "driver_config", "desired_service_level")
    WORKLOAD_FIELD_NUMBER: _ClassVar[int]
    DRIVER_CONFIG_FIELD_NUMBER: _ClassVar[int]
    DESIRED_SERVICE_LEVEL_FIELD_NUMBER: _ClassVar[int]
    workload: SandboxWorkloadConfig
    driver_config: _struct_pb2.Struct
    desired_service_level: SandboxServiceLevel
    def __init__(self, workload: _Optional[_Union[SandboxWorkloadConfig, _Mapping]] = ..., driver_config: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., desired_service_level: _Optional[_Union[SandboxServiceLevel, _Mapping]] = ...) -> None: ...

class SandboxWorkloadConfig(_message.Message):
    __slots__ = ("image", "environment", "resources")
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    image: str
    environment: _containers.ScalarMap[str, str]
    resources: SandboxResources
    def __init__(self, image: _Optional[str] = ..., environment: _Optional[_Mapping[str, str]] = ..., resources: _Optional[_Union[SandboxResources, _Mapping]] = ...) -> None: ...

class SandboxResources(_message.Message):
    __slots__ = ("cpu", "memory", "gpu")
    CPU_FIELD_NUMBER: _ClassVar[int]
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    GPU_FIELD_NUMBER: _ClassVar[int]
    cpu: str
    memory: str
    gpu: GpuResourceRequirements
    def __init__(self, cpu: _Optional[str] = ..., memory: _Optional[str] = ..., gpu: _Optional[_Union[GpuResourceRequirements, _Mapping]] = ...) -> None: ...

class SandboxServiceLevel(_message.Message):
    __slots__ = ("startup",)
    STARTUP_FIELD_NUMBER: _ClassVar[int]
    startup: SandboxStartup
    def __init__(self, startup: _Optional[_Union[SandboxStartup, _Mapping]] = ...) -> None: ...

class SandboxStartup(_message.Message):
    __slots__ = ("ready_within", "max_burst")
    READY_WITHIN_FIELD_NUMBER: _ClassVar[int]
    MAX_BURST_FIELD_NUMBER: _ClassVar[int]
    ready_within: _duration_pb2.Duration
    max_burst: int
    def __init__(self, ready_within: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., max_burst: _Optional[int] = ...) -> None: ...

class SandboxWorkloadTemplateProvenance(_message.Message):
    __slots__ = ("name", "resource_version")
    NAME_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    name: str
    resource_version: str
    def __init__(self, name: _Optional[str] = ..., resource_version: _Optional[str] = ...) -> None: ...

class SandboxStatus(_message.Message):
    __slots__ = ("agent_pod", "agent_fd", "sandbox_fd", "conditions", "phase", "current_policy_version", "main_process_instance_id", "exit_code", "endpoint_statuses", "configuration_admission", "configuration_activated", "provisioning", "restart_count", "next_restart_time", "main_process_started_time", "last_token_grant_failure")
    AGENT_POD_FIELD_NUMBER: _ClassVar[int]
    AGENT_FD_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FD_FIELD_NUMBER: _ClassVar[int]
    CONDITIONS_FIELD_NUMBER: _ClassVar[int]
    PHASE_FIELD_NUMBER: _ClassVar[int]
    CURRENT_POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    MAIN_PROCESS_INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_STATUSES_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ADMISSION_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_ACTIVATED_FIELD_NUMBER: _ClassVar[int]
    PROVISIONING_FIELD_NUMBER: _ClassVar[int]
    RESTART_COUNT_FIELD_NUMBER: _ClassVar[int]
    NEXT_RESTART_TIME_FIELD_NUMBER: _ClassVar[int]
    MAIN_PROCESS_STARTED_TIME_FIELD_NUMBER: _ClassVar[int]
    LAST_TOKEN_GRANT_FAILURE_FIELD_NUMBER: _ClassVar[int]
    agent_pod: str
    agent_fd: str
    sandbox_fd: str
    conditions: _containers.RepeatedCompositeFieldContainer[SandboxCondition]
    phase: SandboxPhase
    current_policy_version: int
    main_process_instance_id: str
    exit_code: int
    endpoint_statuses: _containers.RepeatedCompositeFieldContainer[EndpointStatus]
    configuration_admission: SandboxConfigurationAdmission
    configuration_activated: bool
    provisioning: SandboxProvisioning
    restart_count: int
    next_restart_time: _timestamp_pb2.Timestamp
    main_process_started_time: _timestamp_pb2.Timestamp
    last_token_grant_failure: TokenGrantFailureObservation
    def __init__(self, agent_pod: _Optional[str] = ..., agent_fd: _Optional[str] = ..., sandbox_fd: _Optional[str] = ..., conditions: _Optional[_Iterable[_Union[SandboxCondition, _Mapping]]] = ..., phase: _Optional[_Union[SandboxPhase, str]] = ..., current_policy_version: _Optional[int] = ..., main_process_instance_id: _Optional[str] = ..., exit_code: _Optional[int] = ..., endpoint_statuses: _Optional[_Iterable[_Union[EndpointStatus, _Mapping]]] = ..., configuration_admission: _Optional[_Union[SandboxConfigurationAdmission, _Mapping]] = ..., configuration_activated: bool = ..., provisioning: _Optional[_Union[SandboxProvisioning, _Mapping]] = ..., restart_count: _Optional[int] = ..., next_restart_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., main_process_started_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_token_grant_failure: _Optional[_Union[TokenGrantFailureObservation, _Mapping]] = ...) -> None: ...

class SandboxCondition(_message.Message):
    __slots__ = ("type", "status", "reason", "message", "transition_time")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TRANSITION_TIME_FIELD_NUMBER: _ClassVar[int]
    type: str
    status: str
    reason: str
    message: str
    transition_time: _timestamp_pb2.Timestamp
    def __init__(self, type: _Optional[str] = ..., status: _Optional[str] = ..., reason: _Optional[str] = ..., message: _Optional[str] = ..., transition_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class PlatformEvent(_message.Message):
    __slots__ = ("event_time", "source", "type", "reason", "message", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    event_time: _timestamp_pb2.Timestamp
    source: str
    type: str
    reason: str
    message: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, event_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., source: _Optional[str] = ..., type: _Optional[str] = ..., reason: _Optional[str] = ..., message: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class CreateSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "spec", "name", "labels", "annotations", "await_main_process_attachment", "workload_template", "request_id", "service_exposures")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class AnnotationsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SPEC_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    AWAIT_MAIN_PROCESS_ATTACHMENT_FIELD_NUMBER: _ClassVar[int]
    WORKLOAD_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SERVICE_EXPOSURES_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    spec: SandboxSpec
    name: str
    labels: _containers.ScalarMap[str, str]
    annotations: _containers.ScalarMap[str, str]
    await_main_process_attachment: bool
    workload_template: str
    request_id: str
    service_exposures: _containers.RepeatedCompositeFieldContainer[SandboxServiceExposure]
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., spec: _Optional[_Union[SandboxSpec, _Mapping]] = ..., name: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., annotations: _Optional[_Mapping[str, str]] = ..., await_main_process_attachment: bool = ..., workload_template: _Optional[str] = ..., request_id: _Optional[str] = ..., service_exposures: _Optional[_Iterable[_Union[SandboxServiceExposure, _Mapping]]] = ...) -> None: ...

class UpdateSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "image", "command", "environment", "labels", "annotations", "expected_resource_version", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    image: str
    command: SandboxCommandUpdate
    environment: SandboxMapUpdate
    labels: SandboxMapUpdate
    annotations: SandboxMapUpdate
    expected_resource_version: int
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., image: _Optional[str] = ..., command: _Optional[_Union[SandboxCommandUpdate, _Mapping]] = ..., environment: _Optional[_Union[SandboxMapUpdate, _Mapping]] = ..., labels: _Optional[_Union[SandboxMapUpdate, _Mapping]] = ..., annotations: _Optional[_Union[SandboxMapUpdate, _Mapping]] = ..., expected_resource_version: _Optional[int] = ..., request_id: _Optional[str] = ...) -> None: ...

class SandboxCommandUpdate(_message.Message):
    __slots__ = ("argv",)
    ARGV_FIELD_NUMBER: _ClassVar[int]
    argv: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, argv: _Optional[_Iterable[str]] = ...) -> None: ...

class SandboxMapUpdate(_message.Message):
    __slots__ = ("values",)
    class ValuesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.ScalarMap[str, str]
    def __init__(self, values: _Optional[_Mapping[str, str]] = ...) -> None: ...

class CreateSandboxTemplateRequest(_message.Message):
    __slots__ = ("workspace_scope", "template", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    template: SandboxWorkloadTemplate
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., template: _Optional[_Union[SandboxWorkloadTemplate, _Mapping]] = ..., request_id: _Optional[str] = ...) -> None: ...

class GetSandboxTemplateRequest(_message.Message):
    __slots__ = ("workspace_scope", "name")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ...) -> None: ...

class ListSandboxTemplatesRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token", "label_selector")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LABEL_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    label_selector: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., label_selector: _Optional[str] = ...) -> None: ...

class DeleteSandboxTemplateRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class SandboxTemplateResponse(_message.Message):
    __slots__ = ("template",)
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    template: SandboxWorkloadTemplate
    def __init__(self, template: _Optional[_Union[SandboxWorkloadTemplate, _Mapping]] = ...) -> None: ...

class ListSandboxTemplatesResponse(_message.Message):
    __slots__ = ("templates", "next_page_token")
    TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    templates: _containers.RepeatedCompositeFieldContainer[SandboxWorkloadTemplate]
    next_page_token: str
    def __init__(self, templates: _Optional[_Iterable[_Union[SandboxWorkloadTemplate, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class DeleteSandboxTemplateResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class BeginRootfsTarStagingRequest(_message.Message):
    __slots__ = ("workspace_scope", "file_name", "size_bytes")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    file_name: str
    size_bytes: int
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., file_name: _Optional[str] = ..., size_bytes: _Optional[int] = ...) -> None: ...

class BeginRootfsTarStagingResponse(_message.Message):
    __slots__ = ("staging_token", "upload_path", "max_bytes", "expiration_time")
    STAGING_TOKEN_FIELD_NUMBER: _ClassVar[int]
    UPLOAD_PATH_FIELD_NUMBER: _ClassVar[int]
    MAX_BYTES_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    staging_token: str
    upload_path: str
    max_bytes: int
    expiration_time: _timestamp_pb2.Timestamp
    def __init__(self, staging_token: _Optional[str] = ..., upload_path: _Optional[str] = ..., max_bytes: _Optional[int] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class GetSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ...) -> None: ...

class ListSandboxesRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token", "label_selector")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LABEL_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    label_selector: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., label_selector: _Optional[str] = ...) -> None: ...

class ListSandboxProvidersRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "page_size", "page_token")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    page_size: int
    page_token: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class AttachSandboxProviderRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "provider", "expected_resource_version", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    provider: str
    expected_resource_version: int
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., provider: _Optional[str] = ..., expected_resource_version: _Optional[int] = ..., request_id: _Optional[str] = ...) -> None: ...

class DetachSandboxProviderRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "provider", "expected_resource_version", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    provider: str
    expected_resource_version: int
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., provider: _Optional[str] = ..., expected_resource_version: _Optional[int] = ..., request_id: _Optional[str] = ...) -> None: ...

class DeleteSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class StopSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class StartSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class SuspendSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ...) -> None: ...

class ResumeSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "restart_if_restore_fails")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    RESTART_IF_RESTORE_FAILS_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    restart_if_restore_fails: bool
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., restart_if_restore_fails: bool = ...) -> None: ...

class SandboxResponse(_message.Message):
    __slots__ = ("sandbox", "service_urls")
    class ServiceUrlsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    SERVICE_URLS_FIELD_NUMBER: _ClassVar[int]
    sandbox: Sandbox
    service_urls: _containers.ScalarMap[str, str]
    def __init__(self, sandbox: _Optional[_Union[Sandbox, _Mapping]] = ..., service_urls: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ListSandboxesResponse(_message.Message):
    __slots__ = ("sandboxes", "next_page_token")
    SANDBOXES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    sandboxes: _containers.RepeatedCompositeFieldContainer[Sandbox]
    next_page_token: str
    def __init__(self, sandboxes: _Optional[_Iterable[_Union[Sandbox, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class ListSandboxProvidersResponse(_message.Message):
    __slots__ = ("providers", "next_page_token")
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    providers: _containers.RepeatedCompositeFieldContainer[_datamodel_pb2.Provider]
    next_page_token: str
    def __init__(self, providers: _Optional[_Iterable[_Union[_datamodel_pb2.Provider, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class AttachSandboxProviderResponse(_message.Message):
    __slots__ = ("sandbox", "attached", "receipt")
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    ATTACHED_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_FIELD_NUMBER: _ClassVar[int]
    sandbox: Sandbox
    attached: bool
    receipt: ProviderMutationReceipt
    def __init__(self, sandbox: _Optional[_Union[Sandbox, _Mapping]] = ..., attached: bool = ..., receipt: _Optional[_Union[ProviderMutationReceipt, _Mapping]] = ...) -> None: ...

class DetachSandboxProviderResponse(_message.Message):
    __slots__ = ("sandbox", "detached", "receipt")
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    DETACHED_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_FIELD_NUMBER: _ClassVar[int]
    sandbox: Sandbox
    detached: bool
    receipt: ProviderMutationReceipt
    def __init__(self, sandbox: _Optional[_Union[Sandbox, _Mapping]] = ..., detached: bool = ..., receipt: _Optional[_Union[ProviderMutationReceipt, _Mapping]] = ...) -> None: ...

class ProviderDesiredIdentity(_message.Message):
    __slots__ = ("sandbox_id", "sandbox", "attachment_epoch", "provider_id", "provider_resource_version", "provider_env_revision", "config_revision", "policy_hash")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_EPOCH_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENV_REVISION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_REVISION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    sandbox: str
    attachment_epoch: str
    provider_id: str
    provider_resource_version: int
    provider_env_revision: int
    config_revision: int
    policy_hash: str
    def __init__(self, sandbox_id: _Optional[str] = ..., sandbox: _Optional[str] = ..., attachment_epoch: _Optional[str] = ..., provider_id: _Optional[str] = ..., provider_resource_version: _Optional[int] = ..., provider_env_revision: _Optional[int] = ..., config_revision: _Optional[int] = ..., policy_hash: _Optional[str] = ...) -> None: ...

class ConfigSnapshotRevision(_message.Message):
    __slots__ = ("sandbox_config", "provider_environment", "provider_target")
    SANDBOX_CONFIG_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_TARGET_FIELD_NUMBER: _ClassVar[int]
    sandbox_config: SandboxConfigRevision
    provider_environment: int
    provider_target: ProviderDesiredIdentity
    def __init__(self, sandbox_config: _Optional[_Union[SandboxConfigRevision, _Mapping]] = ..., provider_environment: _Optional[int] = ..., provider_target: _Optional[_Union[ProviderDesiredIdentity, _Mapping]] = ...) -> None: ...

class SandboxConfigRevision(_message.Message):
    __slots__ = ("config_revision", "policy_version", "policy_source", "global_policy_version", "settings_revision")
    CONFIG_REVISION_FIELD_NUMBER: _ClassVar[int]
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_SOURCE_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_REVISION_FIELD_NUMBER: _ClassVar[int]
    config_revision: int
    policy_version: int
    policy_source: _sandbox_pb2.PolicySource
    global_policy_version: int
    settings_revision: int
    def __init__(self, config_revision: _Optional[int] = ..., policy_version: _Optional[int] = ..., policy_source: _Optional[_Union[_sandbox_pb2.PolicySource, str]] = ..., global_policy_version: _Optional[int] = ..., settings_revision: _Optional[int] = ...) -> None: ...

class ConfigUpdateOperation(_message.Message):
    __slots__ = ("operation_id", "sandbox_id", "component", "target_revision", "state", "outcome", "sanitized_error", "created_time", "updated_time", "completed_time")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    COMPONENT_FIELD_NUMBER: _ClassVar[int]
    TARGET_REVISION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    SANITIZED_ERROR_FIELD_NUMBER: _ClassVar[int]
    CREATED_TIME_FIELD_NUMBER: _ClassVar[int]
    UPDATED_TIME_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_TIME_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    sandbox_id: str
    component: ConfigComponent
    target_revision: ConfigSnapshotRevision
    state: ConfigUpdateOperationState
    outcome: ConfigApplyOutcome
    sanitized_error: str
    created_time: _timestamp_pb2.Timestamp
    updated_time: _timestamp_pb2.Timestamp
    completed_time: _timestamp_pb2.Timestamp
    def __init__(self, operation_id: _Optional[str] = ..., sandbox_id: _Optional[str] = ..., component: _Optional[_Union[ConfigComponent, str]] = ..., target_revision: _Optional[_Union[ConfigSnapshotRevision, _Mapping]] = ..., state: _Optional[_Union[ConfigUpdateOperationState, str]] = ..., outcome: _Optional[_Union[ConfigApplyOutcome, str]] = ..., sanitized_error: _Optional[str] = ..., created_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., completed_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ProviderMutationReceipt(_message.Message):
    __slots__ = ("receipt_id", "mutation_id", "provider", "workspace", "kind", "desired", "persisted_time")
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    MUTATION_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    DESIRED_FIELD_NUMBER: _ClassVar[int]
    PERSISTED_TIME_FIELD_NUMBER: _ClassVar[int]
    receipt_id: str
    mutation_id: str
    provider: str
    workspace: str
    kind: ProviderMutationKind
    desired: ProviderDesiredIdentity
    persisted_time: _timestamp_pb2.Timestamp
    def __init__(self, receipt_id: _Optional[str] = ..., mutation_id: _Optional[str] = ..., provider: _Optional[str] = ..., workspace: _Optional[str] = ..., kind: _Optional[_Union[ProviderMutationKind, str]] = ..., desired: _Optional[_Union[ProviderDesiredIdentity, _Mapping]] = ..., persisted_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ProviderReadinessObservation(_message.Message):
    __slots__ = ("session_id", "sequence", "attachment_epoch", "provider_env_revision", "config_revision", "policy_hash", "credentials_installed", "policy_active", "launch_environment_installed", "process_instance_id", "reason")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_EPOCH_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENV_REVISION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_REVISION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_INSTALLED_FIELD_NUMBER: _ClassVar[int]
    POLICY_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    LAUNCH_ENVIRONMENT_INSTALLED_FIELD_NUMBER: _ClassVar[int]
    PROCESS_INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    sequence: int
    attachment_epoch: str
    provider_env_revision: int
    config_revision: int
    policy_hash: str
    credentials_installed: bool
    policy_active: bool
    launch_environment_installed: bool
    process_instance_id: str
    reason: ProviderReadinessReason
    def __init__(self, session_id: _Optional[str] = ..., sequence: _Optional[int] = ..., attachment_epoch: _Optional[str] = ..., provider_env_revision: _Optional[int] = ..., config_revision: _Optional[int] = ..., policy_hash: _Optional[str] = ..., credentials_installed: bool = ..., policy_active: bool = ..., launch_environment_installed: bool = ..., process_instance_id: _Optional[str] = ..., reason: _Optional[_Union[ProviderReadinessReason, str]] = ...) -> None: ...

class ProviderReadinessStatus(_message.Message):
    __slots__ = ("receipt", "state", "reason", "observed", "network_instance_id", "observed_time", "evaluated_time", "operation")
    RECEIPT_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_FIELD_NUMBER: _ClassVar[int]
    NETWORK_INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_TIME_FIELD_NUMBER: _ClassVar[int]
    EVALUATED_TIME_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    receipt: ProviderMutationReceipt
    state: ProviderReadinessState
    reason: ProviderReadinessReason
    observed: ProviderReadinessObservation
    network_instance_id: str
    observed_time: _timestamp_pb2.Timestamp
    evaluated_time: _timestamp_pb2.Timestamp
    operation: ConfigUpdateOperation
    def __init__(self, receipt: _Optional[_Union[ProviderMutationReceipt, _Mapping]] = ..., state: _Optional[_Union[ProviderReadinessState, str]] = ..., reason: _Optional[_Union[ProviderReadinessReason, str]] = ..., observed: _Optional[_Union[ProviderReadinessObservation, _Mapping]] = ..., network_instance_id: _Optional[str] = ..., observed_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., evaluated_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., operation: _Optional[_Union[ConfigUpdateOperation, _Mapping]] = ...) -> None: ...

class GetSandboxProviderStatusRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "provider", "receipt_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    RECEIPT_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    provider: str
    receipt_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., provider: _Optional[str] = ..., receipt_id: _Optional[str] = ...) -> None: ...

class GetSandboxProviderStatusResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: ProviderReadinessStatus
    def __init__(self, status: _Optional[_Union[ProviderReadinessStatus, _Mapping]] = ...) -> None: ...

class ReportProviderReadinessRequest(_message.Message):
    __slots__ = ("sandbox_id", "observation")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    observation: ProviderReadinessObservation
    def __init__(self, sandbox_id: _Optional[str] = ..., observation: _Optional[_Union[ProviderReadinessObservation, _Mapping]] = ...) -> None: ...

class ReportProviderReadinessResponse(_message.Message):
    __slots__ = ("accepted_sequence", "report_interval", "observation_ttl")
    ACCEPTED_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    REPORT_INTERVAL_FIELD_NUMBER: _ClassVar[int]
    OBSERVATION_TTL_FIELD_NUMBER: _ClassVar[int]
    accepted_sequence: int
    report_interval: _duration_pb2.Duration
    observation_ttl: _duration_pb2.Duration
    def __init__(self, accepted_sequence: _Optional[int] = ..., report_interval: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., observation_ttl: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ...) -> None: ...

class DeleteSandboxResponse(_message.Message):
    __slots__ = ("outcome", "sandbox_id")
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    sandbox_id: str
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ..., sandbox_id: _Optional[str] = ...) -> None: ...

class CreateSshSessionRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ...) -> None: ...

class CreateSshSessionResponse(_message.Message):
    __slots__ = ("sandbox_id", "token", "gateway_host", "gateway_port", "gateway_scheme", "host_key_fingerprint", "expiration_time")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_HOST_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_PORT_FIELD_NUMBER: _ClassVar[int]
    GATEWAY_SCHEME_FIELD_NUMBER: _ClassVar[int]
    HOST_KEY_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    token: str
    gateway_host: str
    gateway_port: int
    gateway_scheme: str
    host_key_fingerprint: str
    expiration_time: _timestamp_pb2.Timestamp
    def __init__(self, sandbox_id: _Optional[str] = ..., token: _Optional[str] = ..., gateway_host: _Optional[str] = ..., gateway_port: _Optional[int] = ..., gateway_scheme: _Optional[str] = ..., host_key_fingerprint: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ExposeServiceRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "target_port", "domain", "sandbox", "request_id", "authorization_mode")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TARGET_PORT_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZATION_MODE_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    target_port: int
    domain: bool
    sandbox: str
    request_id: str
    authorization_mode: ServiceAuthorizationMode
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., target_port: _Optional[int] = ..., domain: bool = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ..., authorization_mode: _Optional[_Union[ServiceAuthorizationMode, str]] = ...) -> None: ...

class GetServiceRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., sandbox: _Optional[str] = ...) -> None: ...

class ListServicesRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., sandbox: _Optional[str] = ...) -> None: ...

class ListServicesResponse(_message.Message):
    __slots__ = ("services", "next_page_token")
    SERVICES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    services: _containers.RepeatedCompositeFieldContainer[ServiceEndpointResponse]
    next_page_token: str
    def __init__(self, services: _Optional[_Iterable[_Union[ServiceEndpointResponse, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class DeleteServiceRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "sandbox", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    sandbox: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., sandbox: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class DeleteServiceResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class ServiceEndpoint(_message.Message):
    __slots__ = ("metadata", "sandbox_id", "sandbox", "name", "target_port", "domain", "authorization_mode")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TARGET_PORT_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZATION_MODE_FIELD_NUMBER: _ClassVar[int]
    metadata: _datamodel_pb2.ObjectMeta
    sandbox_id: str
    sandbox: str
    name: str
    target_port: int
    domain: bool
    authorization_mode: ServiceAuthorizationMode
    def __init__(self, metadata: _Optional[_Union[_datamodel_pb2.ObjectMeta, _Mapping]] = ..., sandbox_id: _Optional[str] = ..., sandbox: _Optional[str] = ..., name: _Optional[str] = ..., target_port: _Optional[int] = ..., domain: bool = ..., authorization_mode: _Optional[_Union[ServiceAuthorizationMode, str]] = ...) -> None: ...

class ServiceEndpointResponse(_message.Message):
    __slots__ = ("endpoint", "url")
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    endpoint: ServiceEndpoint
    url: str
    def __init__(self, endpoint: _Optional[_Union[ServiceEndpoint, _Mapping]] = ..., url: _Optional[str] = ...) -> None: ...

class RevokeSshSessionRequest(_message.Message):
    __slots__ = ("token", "allow_missing")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    token: str
    allow_missing: bool
    def __init__(self, token: _Optional[str] = ..., allow_missing: bool = ...) -> None: ...

class RevokeSshSessionResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class ExecSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "command", "workdir", "environment", "execution_timeout", "stdin", "tty", "cols", "rows", "no_login_shell", "request_id")
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    WORKDIR_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    STDIN_FIELD_NUMBER: _ClassVar[int]
    TTY_FIELD_NUMBER: _ClassVar[int]
    COLS_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    NO_LOGIN_SHELL_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    command: _containers.RepeatedScalarFieldContainer[str]
    workdir: str
    environment: _containers.ScalarMap[str, str]
    execution_timeout: _duration_pb2.Duration
    stdin: bytes
    tty: bool
    cols: int
    rows: int
    no_login_shell: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., command: _Optional[_Iterable[str]] = ..., workdir: _Optional[str] = ..., environment: _Optional[_Mapping[str, str]] = ..., execution_timeout: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., stdin: _Optional[bytes] = ..., tty: bool = ..., cols: _Optional[int] = ..., rows: _Optional[int] = ..., no_login_shell: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class ExecSandboxStdout(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    def __init__(self, data: _Optional[bytes] = ...) -> None: ...

class ExecSandboxStderr(_message.Message):
    __slots__ = ("data",)
    DATA_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    def __init__(self, data: _Optional[bytes] = ...) -> None: ...

class ExecSandboxExit(_message.Message):
    __slots__ = ("exit_code",)
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    exit_code: int
    def __init__(self, exit_code: _Optional[int] = ...) -> None: ...

class ExecSandboxEvent(_message.Message):
    __slots__ = ("stdout", "stderr", "exit")
    STDOUT_FIELD_NUMBER: _ClassVar[int]
    STDERR_FIELD_NUMBER: _ClassVar[int]
    EXIT_FIELD_NUMBER: _ClassVar[int]
    stdout: ExecSandboxStdout
    stderr: ExecSandboxStderr
    exit: ExecSandboxExit
    def __init__(self, stdout: _Optional[_Union[ExecSandboxStdout, _Mapping]] = ..., stderr: _Optional[_Union[ExecSandboxStderr, _Mapping]] = ..., exit: _Optional[_Union[ExecSandboxExit, _Mapping]] = ...) -> None: ...

class TcpForwardInit(_message.Message):
    __slots__ = ("sandbox", "workspace", "service_id", "ssh", "tcp", "authorization_token")
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    SERVICE_ID_FIELD_NUMBER: _ClassVar[int]
    SSH_FIELD_NUMBER: _ClassVar[int]
    TCP_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZATION_TOKEN_FIELD_NUMBER: _ClassVar[int]
    sandbox: str
    workspace: str
    service_id: str
    ssh: SshRelayTarget
    tcp: TcpRelayTarget
    authorization_token: str
    def __init__(self, sandbox: _Optional[str] = ..., workspace: _Optional[str] = ..., service_id: _Optional[str] = ..., ssh: _Optional[_Union[SshRelayTarget, _Mapping]] = ..., tcp: _Optional[_Union[TcpRelayTarget, _Mapping]] = ..., authorization_token: _Optional[str] = ...) -> None: ...

class TcpForwardFrame(_message.Message):
    __slots__ = ("init", "data")
    INIT_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    init: TcpForwardInit
    data: bytes
    def __init__(self, init: _Optional[_Union[TcpForwardInit, _Mapping]] = ..., data: _Optional[bytes] = ...) -> None: ...

class ExecSandboxInput(_message.Message):
    __slots__ = ("start", "stdin", "resize")
    START_FIELD_NUMBER: _ClassVar[int]
    STDIN_FIELD_NUMBER: _ClassVar[int]
    RESIZE_FIELD_NUMBER: _ClassVar[int]
    start: ExecSandboxRequest
    stdin: bytes
    resize: ExecSandboxWindowResize
    def __init__(self, start: _Optional[_Union[ExecSandboxRequest, _Mapping]] = ..., stdin: _Optional[bytes] = ..., resize: _Optional[_Union[ExecSandboxWindowResize, _Mapping]] = ...) -> None: ...

class ExecSandboxWindowResize(_message.Message):
    __slots__ = ("cols", "rows")
    COLS_FIELD_NUMBER: _ClassVar[int]
    ROWS_FIELD_NUMBER: _ClassVar[int]
    cols: int
    rows: int
    def __init__(self, cols: _Optional[int] = ..., rows: _Optional[int] = ...) -> None: ...

class SshSession(_message.Message):
    __slots__ = ("metadata", "sandbox_id", "token", "expiration_time", "revoked")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    REVOKED_FIELD_NUMBER: _ClassVar[int]
    metadata: _datamodel_pb2.ObjectMeta
    sandbox_id: str
    token: str
    expiration_time: _timestamp_pb2.Timestamp
    revoked: bool
    def __init__(self, metadata: _Optional[_Union[_datamodel_pb2.ObjectMeta, _Mapping]] = ..., sandbox_id: _Optional[str] = ..., token: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., revoked: bool = ...) -> None: ...

class WatchSandboxRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "follow_status", "follow_logs", "follow_events", "log_tail_lines", "event_tail", "stop_on_terminal", "since_time", "log_sources", "log_min_level", "resume_after_cursor")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_STATUS_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_LOGS_FIELD_NUMBER: _ClassVar[int]
    FOLLOW_EVENTS_FIELD_NUMBER: _ClassVar[int]
    LOG_TAIL_LINES_FIELD_NUMBER: _ClassVar[int]
    EVENT_TAIL_FIELD_NUMBER: _ClassVar[int]
    STOP_ON_TERMINAL_FIELD_NUMBER: _ClassVar[int]
    SINCE_TIME_FIELD_NUMBER: _ClassVar[int]
    LOG_SOURCES_FIELD_NUMBER: _ClassVar[int]
    LOG_MIN_LEVEL_FIELD_NUMBER: _ClassVar[int]
    RESUME_AFTER_CURSOR_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    follow_status: bool
    follow_logs: bool
    follow_events: bool
    log_tail_lines: int
    event_tail: int
    stop_on_terminal: bool
    since_time: _timestamp_pb2.Timestamp
    log_sources: _containers.RepeatedScalarFieldContainer[str]
    log_min_level: str
    resume_after_cursor: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., follow_status: bool = ..., follow_logs: bool = ..., follow_events: bool = ..., log_tail_lines: _Optional[int] = ..., event_tail: _Optional[int] = ..., stop_on_terminal: bool = ..., since_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., log_sources: _Optional[_Iterable[str]] = ..., log_min_level: _Optional[str] = ..., resume_after_cursor: _Optional[str] = ...) -> None: ...

class SandboxStreamEvent(_message.Message):
    __slots__ = ("sandbox", "log", "event", "warning", "draft_policy_update", "cursor")
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    WARNING_FIELD_NUMBER: _ClassVar[int]
    DRAFT_POLICY_UPDATE_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    sandbox: Sandbox
    log: SandboxLogLine
    event: PlatformEvent
    warning: SandboxStreamWarning
    draft_policy_update: DraftPolicyUpdate
    cursor: str
    def __init__(self, sandbox: _Optional[_Union[Sandbox, _Mapping]] = ..., log: _Optional[_Union[SandboxLogLine, _Mapping]] = ..., event: _Optional[_Union[PlatformEvent, _Mapping]] = ..., warning: _Optional[_Union[SandboxStreamWarning, _Mapping]] = ..., draft_policy_update: _Optional[_Union[DraftPolicyUpdate, _Mapping]] = ..., cursor: _Optional[str] = ...) -> None: ...

class SandboxLogLine(_message.Message):
    __slots__ = ("sandbox_id", "event_time", "level", "target", "message", "source", "fields")
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    event_time: _timestamp_pb2.Timestamp
    level: str
    target: str
    message: str
    source: str
    fields: _containers.ScalarMap[str, str]
    def __init__(self, sandbox_id: _Optional[str] = ..., event_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., level: _Optional[str] = ..., target: _Optional[str] = ..., message: _Optional[str] = ..., source: _Optional[str] = ..., fields: _Optional[_Mapping[str, str]] = ...) -> None: ...

class SandboxStreamWarning(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class CreateProviderRequest(_message.Message):
    __slots__ = ("workspace_scope", "provider", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    provider: _datamodel_pb2.Provider
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., provider: _Optional[_Union[_datamodel_pb2.Provider, _Mapping]] = ..., request_id: _Optional[str] = ...) -> None: ...

class GetProviderRequest(_message.Message):
    __slots__ = ("workspace_scope", "name")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ...) -> None: ...

class GetProviderCredentialsRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "credential_keys", "minimum_remaining_lifetime")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEYS_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_REMAINING_LIFETIME_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    credential_keys: _containers.RepeatedScalarFieldContainer[str]
    minimum_remaining_lifetime: _duration_pb2.Duration
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., credential_keys: _Optional[_Iterable[str]] = ..., minimum_remaining_lifetime: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ...) -> None: ...

class ProviderCredentialValue(_message.Message):
    __slots__ = ("value", "expiration_time")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    value: str
    expiration_time: _timestamp_pb2.Timestamp
    def __init__(self, value: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class GetProviderCredentialsResponse(_message.Message):
    __slots__ = ("credentials",)
    class CredentialsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: ProviderCredentialValue
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ProviderCredentialValue, _Mapping]] = ...) -> None: ...
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.MessageMap[str, ProviderCredentialValue]
    def __init__(self, credentials: _Optional[_Mapping[str, ProviderCredentialValue]] = ...) -> None: ...

class ListProvidersRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token", "label_selector")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LABEL_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    label_selector: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., label_selector: _Optional[str] = ...) -> None: ...

class UpdateProviderRequest(_message.Message):
    __slots__ = ("workspace_scope", "provider", "credential_expiration_times", "clear_credential_expiration_keys", "request_id")
    class CredentialExpirationTimesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _timestamp_pb2.Timestamp
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_EXPIRATION_TIMES_FIELD_NUMBER: _ClassVar[int]
    CLEAR_CREDENTIAL_EXPIRATION_KEYS_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    provider: _datamodel_pb2.Provider
    credential_expiration_times: _containers.MessageMap[str, _timestamp_pb2.Timestamp]
    clear_credential_expiration_keys: _containers.RepeatedScalarFieldContainer[str]
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., provider: _Optional[_Union[_datamodel_pb2.Provider, _Mapping]] = ..., credential_expiration_times: _Optional[_Mapping[str, _timestamp_pb2.Timestamp]] = ..., clear_credential_expiration_keys: _Optional[_Iterable[str]] = ..., request_id: _Optional[str] = ...) -> None: ...

class DeleteProviderRequest(_message.Message):
    __slots__ = ("workspace_scope", "name", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    name: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., name: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class ProviderResponse(_message.Message):
    __slots__ = ("provider", "target_receipts", "mutation_id")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    TARGET_RECEIPTS_FIELD_NUMBER: _ClassVar[int]
    MUTATION_ID_FIELD_NUMBER: _ClassVar[int]
    provider: _datamodel_pb2.Provider
    target_receipts: _containers.RepeatedCompositeFieldContainer[ProviderMutationReceipt]
    mutation_id: str
    def __init__(self, provider: _Optional[_Union[_datamodel_pb2.Provider, _Mapping]] = ..., target_receipts: _Optional[_Iterable[_Union[ProviderMutationReceipt, _Mapping]]] = ..., mutation_id: _Optional[str] = ...) -> None: ...

class ListProvidersResponse(_message.Message):
    __slots__ = ("providers", "next_page_token")
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    providers: _containers.RepeatedCompositeFieldContainer[_datamodel_pb2.Provider]
    next_page_token: str
    def __init__(self, providers: _Optional[_Iterable[_Union[_datamodel_pb2.Provider, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class ListProviderProfilesRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class GetProviderProfileRequest(_message.Message):
    __slots__ = ("workspace_scope", "id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., id: _Optional[str] = ...) -> None: ...

class ProviderProfileImportItem(_message.Message):
    __slots__ = ("profile", "source")
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    profile: ProviderProfile
    source: str
    def __init__(self, profile: _Optional[_Union[ProviderProfile, _Mapping]] = ..., source: _Optional[str] = ...) -> None: ...

class ProviderProfileDiagnostic(_message.Message):
    __slots__ = ("source", "profile_id", "field", "message", "severity")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    PROFILE_ID_FIELD_NUMBER: _ClassVar[int]
    FIELD_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    source: str
    profile_id: str
    field: str
    message: str
    severity: str
    def __init__(self, source: _Optional[str] = ..., profile_id: _Optional[str] = ..., field: _Optional[str] = ..., message: _Optional[str] = ..., severity: _Optional[str] = ...) -> None: ...

class ProviderCredentialTokenGrantAudienceOverride(_message.Message):
    __slots__ = ("host", "port", "path", "audience", "scopes")
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    host: str
    port: int
    path: str
    audience: str
    scopes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, host: _Optional[str] = ..., port: _Optional[int] = ..., path: _Optional[str] = ..., audience: _Optional[str] = ..., scopes: _Optional[_Iterable[str]] = ...) -> None: ...

class ProviderCredentialTokenGrantSubjectToken(_message.Message):
    __slots__ = ("source", "credential", "subject_token_type")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_TOKEN_TYPE_FIELD_NUMBER: _ClassVar[int]
    source: str
    credential: str
    subject_token_type: str
    def __init__(self, source: _Optional[str] = ..., credential: _Optional[str] = ..., subject_token_type: _Optional[str] = ...) -> None: ...

class ProviderCredentialTokenGrant(_message.Message):
    __slots__ = ("token_endpoint", "audience", "jwt_svid_audience", "scopes", "cache_ttl", "audience_overrides", "client_assertion_type", "grant_type", "subject_token", "requested_token_type")
    TOKEN_ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    JWT_SVID_AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    CACHE_TTL_FIELD_NUMBER: _ClassVar[int]
    AUDIENCE_OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ASSERTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    GRANT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBJECT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_TOKEN_TYPE_FIELD_NUMBER: _ClassVar[int]
    token_endpoint: str
    audience: str
    jwt_svid_audience: str
    scopes: _containers.RepeatedScalarFieldContainer[str]
    cache_ttl: _duration_pb2.Duration
    audience_overrides: _containers.RepeatedCompositeFieldContainer[ProviderCredentialTokenGrantAudienceOverride]
    client_assertion_type: str
    grant_type: ProviderCredentialTokenGrantType
    subject_token: ProviderCredentialTokenGrantSubjectToken
    requested_token_type: str
    def __init__(self, token_endpoint: _Optional[str] = ..., audience: _Optional[str] = ..., jwt_svid_audience: _Optional[str] = ..., scopes: _Optional[_Iterable[str]] = ..., cache_ttl: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., audience_overrides: _Optional[_Iterable[_Union[ProviderCredentialTokenGrantAudienceOverride, _Mapping]]] = ..., client_assertion_type: _Optional[str] = ..., grant_type: _Optional[_Union[ProviderCredentialTokenGrantType, str]] = ..., subject_token: _Optional[_Union[ProviderCredentialTokenGrantSubjectToken, _Mapping]] = ..., requested_token_type: _Optional[str] = ...) -> None: ...

class ProviderProfileCredential(_message.Message):
    __slots__ = ("name", "description", "env_vars", "required", "auth_style", "header_name", "query_param", "refresh", "path_template", "token_grant", "token_grant_owners", "stable_placeholder")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    ENV_VARS_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    AUTH_STYLE_FIELD_NUMBER: _ClassVar[int]
    HEADER_NAME_FIELD_NUMBER: _ClassVar[int]
    QUERY_PARAM_FIELD_NUMBER: _ClassVar[int]
    REFRESH_FIELD_NUMBER: _ClassVar[int]
    PATH_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    TOKEN_GRANT_FIELD_NUMBER: _ClassVar[int]
    TOKEN_GRANT_OWNERS_FIELD_NUMBER: _ClassVar[int]
    STABLE_PLACEHOLDER_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    env_vars: _containers.RepeatedScalarFieldContainer[str]
    required: bool
    auth_style: str
    header_name: str
    query_param: str
    refresh: ProviderCredentialRefresh
    path_template: str
    token_grant: ProviderCredentialTokenGrant
    token_grant_owners: _containers.RepeatedScalarFieldContainer[str]
    stable_placeholder: bool
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., env_vars: _Optional[_Iterable[str]] = ..., required: bool = ..., auth_style: _Optional[str] = ..., header_name: _Optional[str] = ..., query_param: _Optional[str] = ..., refresh: _Optional[_Union[ProviderCredentialRefresh, _Mapping]] = ..., path_template: _Optional[str] = ..., token_grant: _Optional[_Union[ProviderCredentialTokenGrant, _Mapping]] = ..., token_grant_owners: _Optional[_Iterable[str]] = ..., stable_placeholder: bool = ...) -> None: ...

class ProviderCredentialRefreshMaterial(_message.Message):
    __slots__ = ("name", "description", "required", "secret")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_FIELD_NUMBER: _ClassVar[int]
    SECRET_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    required: bool
    secret: bool
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., required: bool = ..., secret: bool = ...) -> None: ...

class ProviderCredentialRefreshOutput(_message.Message):
    __slots__ = ("output", "credential")
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_FIELD_NUMBER: _ClassVar[int]
    output: str
    credential: str
    def __init__(self, output: _Optional[str] = ..., credential: _Optional[str] = ...) -> None: ...

class ProviderCredentialRefresh(_message.Message):
    __slots__ = ("strategy", "token_url", "scopes", "refresh_before", "max_lifetime", "material", "additional_outputs")
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    TOKEN_URL_FIELD_NUMBER: _ClassVar[int]
    SCOPES_FIELD_NUMBER: _ClassVar[int]
    REFRESH_BEFORE_FIELD_NUMBER: _ClassVar[int]
    MAX_LIFETIME_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_FIELD_NUMBER: _ClassVar[int]
    ADDITIONAL_OUTPUTS_FIELD_NUMBER: _ClassVar[int]
    strategy: ProviderCredentialRefreshStrategy
    token_url: str
    scopes: _containers.RepeatedScalarFieldContainer[str]
    refresh_before: _duration_pb2.Duration
    max_lifetime: _duration_pb2.Duration
    material: _containers.RepeatedCompositeFieldContainer[ProviderCredentialRefreshMaterial]
    additional_outputs: _containers.RepeatedCompositeFieldContainer[ProviderCredentialRefreshOutput]
    def __init__(self, strategy: _Optional[_Union[ProviderCredentialRefreshStrategy, str]] = ..., token_url: _Optional[str] = ..., scopes: _Optional[_Iterable[str]] = ..., refresh_before: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., max_lifetime: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., material: _Optional[_Iterable[_Union[ProviderCredentialRefreshMaterial, _Mapping]]] = ..., additional_outputs: _Optional[_Iterable[_Union[ProviderCredentialRefreshOutput, _Mapping]]] = ...) -> None: ...

class ProviderCredentialRefreshStatus(_message.Message):
    __slots__ = ("provider", "provider_id", "credential_key", "strategy", "status", "expiration_time", "next_refresh_time", "last_refresh_time", "last_error", "recovery_action", "failure_code", "provider_error_subtype", "last_error_time")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    NEXT_REFRESH_TIME_FIELD_NUMBER: _ClassVar[int]
    LAST_REFRESH_TIME_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_FIELD_NUMBER: _ClassVar[int]
    RECOVERY_ACTION_FIELD_NUMBER: _ClassVar[int]
    FAILURE_CODE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ERROR_SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_TIME_FIELD_NUMBER: _ClassVar[int]
    provider: str
    provider_id: str
    credential_key: str
    strategy: ProviderCredentialRefreshStrategy
    status: str
    expiration_time: _timestamp_pb2.Timestamp
    next_refresh_time: _timestamp_pb2.Timestamp
    last_refresh_time: _timestamp_pb2.Timestamp
    last_error: str
    recovery_action: ProviderCredentialRefreshRecoveryAction
    failure_code: str
    provider_error_subtype: str
    last_error_time: _timestamp_pb2.Timestamp
    def __init__(self, provider: _Optional[str] = ..., provider_id: _Optional[str] = ..., credential_key: _Optional[str] = ..., strategy: _Optional[_Union[ProviderCredentialRefreshStrategy, str]] = ..., status: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., next_refresh_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_refresh_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_error: _Optional[str] = ..., recovery_action: _Optional[_Union[ProviderCredentialRefreshRecoveryAction, str]] = ..., failure_code: _Optional[str] = ..., provider_error_subtype: _Optional[str] = ..., last_error_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ProviderProfileDiscovery(_message.Message):
    __slots__ = ("credentials",)
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, credentials: _Optional[_Iterable[str]] = ...) -> None: ...

class GetProviderRefreshStatusRequest(_message.Message):
    __slots__ = ("workspace_scope", "provider", "credential_key")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    provider: str
    credential_key: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ...) -> None: ...

class GetProviderRefreshStatusResponse(_message.Message):
    __slots__ = ("credentials",)
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.RepeatedCompositeFieldContainer[ProviderCredentialRefreshStatus]
    def __init__(self, credentials: _Optional[_Iterable[_Union[ProviderCredentialRefreshStatus, _Mapping]]] = ...) -> None: ...

class ConfigureProviderRefreshRequest(_message.Message):
    __slots__ = ("workspace_scope", "provider", "credential_key", "strategy", "material", "secret_material_keys", "expiration_time", "request_id")
    class MaterialEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    STRATEGY_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_FIELD_NUMBER: _ClassVar[int]
    SECRET_MATERIAL_KEYS_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    provider: str
    credential_key: str
    strategy: ProviderCredentialRefreshStrategy
    material: _containers.ScalarMap[str, str]
    secret_material_keys: _containers.RepeatedScalarFieldContainer[str]
    expiration_time: _timestamp_pb2.Timestamp
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., strategy: _Optional[_Union[ProviderCredentialRefreshStrategy, str]] = ..., material: _Optional[_Mapping[str, str]] = ..., secret_material_keys: _Optional[_Iterable[str]] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., request_id: _Optional[str] = ...) -> None: ...

class ConfigureProviderRefreshResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: ProviderCredentialRefreshStatus
    def __init__(self, status: _Optional[_Union[ProviderCredentialRefreshStatus, _Mapping]] = ...) -> None: ...

class RotateProviderCredentialRequest(_message.Message):
    __slots__ = ("workspace_scope", "provider", "credential_key", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    provider: str
    credential_key: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class RotateProviderCredentialResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: ProviderCredentialRefreshStatus
    def __init__(self, status: _Optional[_Union[ProviderCredentialRefreshStatus, _Mapping]] = ...) -> None: ...

class DeleteProviderRefreshRequest(_message.Message):
    __slots__ = ("workspace_scope", "provider", "credential_key", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    provider: str
    credential_key: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class DeleteProviderRefreshResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class ProviderProfile(_message.Message):
    __slots__ = ("id", "display_name", "description", "category", "credentials", "endpoints", "binaries", "inference_capable", "discovery", "resource_version", "annotations", "source", "scope", "files")
    class AnnotationsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    ENDPOINTS_FIELD_NUMBER: _ClassVar[int]
    BINARIES_FIELD_NUMBER: _ClassVar[int]
    INFERENCE_CAPABLE_FIELD_NUMBER: _ClassVar[int]
    DISCOVERY_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    id: str
    display_name: str
    description: str
    category: ProviderProfileCategory
    credentials: _containers.RepeatedCompositeFieldContainer[ProviderProfileCredential]
    endpoints: _containers.RepeatedCompositeFieldContainer[_sandbox_pb2.NetworkEndpoint]
    binaries: _containers.RepeatedCompositeFieldContainer[_sandbox_pb2.NetworkBinary]
    inference_capable: bool
    discovery: ProviderProfileDiscovery
    resource_version: int
    annotations: _containers.ScalarMap[str, str]
    source: str
    scope: str
    files: _containers.RepeatedCompositeFieldContainer[ProviderProfileFile]
    def __init__(self, id: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., category: _Optional[_Union[ProviderProfileCategory, str]] = ..., credentials: _Optional[_Iterable[_Union[ProviderProfileCredential, _Mapping]]] = ..., endpoints: _Optional[_Iterable[_Union[_sandbox_pb2.NetworkEndpoint, _Mapping]]] = ..., binaries: _Optional[_Iterable[_Union[_sandbox_pb2.NetworkBinary, _Mapping]]] = ..., inference_capable: bool = ..., discovery: _Optional[_Union[ProviderProfileDiscovery, _Mapping]] = ..., resource_version: _Optional[int] = ..., annotations: _Optional[_Mapping[str, str]] = ..., source: _Optional[str] = ..., scope: _Optional[str] = ..., files: _Optional[_Iterable[_Union[ProviderProfileFile, _Mapping]]] = ...) -> None: ...

class ProviderProfileFile(_message.Message):
    __slots__ = ("path", "content", "env_var")
    PATH_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    ENV_VAR_FIELD_NUMBER: _ClassVar[int]
    path: str
    content: str
    env_var: str
    def __init__(self, path: _Optional[str] = ..., content: _Optional[str] = ..., env_var: _Optional[str] = ...) -> None: ...

class ProviderProfileResponse(_message.Message):
    __slots__ = ("profile",)
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    profile: ProviderProfile
    def __init__(self, profile: _Optional[_Union[ProviderProfile, _Mapping]] = ...) -> None: ...

class ListProviderProfilesResponse(_message.Message):
    __slots__ = ("profiles", "next_page_token")
    PROFILES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    profiles: _containers.RepeatedCompositeFieldContainer[ProviderProfile]
    next_page_token: str
    def __init__(self, profiles: _Optional[_Iterable[_Union[ProviderProfile, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class ImportProviderProfilesRequest(_message.Message):
    __slots__ = ("workspace_scope", "profiles", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROFILES_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    profiles: _containers.RepeatedCompositeFieldContainer[ProviderProfileImportItem]
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., profiles: _Optional[_Iterable[_Union[ProviderProfileImportItem, _Mapping]]] = ..., request_id: _Optional[str] = ...) -> None: ...

class ImportProviderProfilesResponse(_message.Message):
    __slots__ = ("diagnostics", "profiles", "imported")
    DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    PROFILES_FIELD_NUMBER: _ClassVar[int]
    IMPORTED_FIELD_NUMBER: _ClassVar[int]
    diagnostics: _containers.RepeatedCompositeFieldContainer[ProviderProfileDiagnostic]
    profiles: _containers.RepeatedCompositeFieldContainer[ProviderProfile]
    imported: bool
    def __init__(self, diagnostics: _Optional[_Iterable[_Union[ProviderProfileDiagnostic, _Mapping]]] = ..., profiles: _Optional[_Iterable[_Union[ProviderProfile, _Mapping]]] = ..., imported: bool = ...) -> None: ...

class UpdateProviderProfilesRequest(_message.Message):
    __slots__ = ("workspace_scope", "profile", "expected_resource_version", "id", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    profile: ProviderProfileImportItem
    expected_resource_version: int
    id: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., profile: _Optional[_Union[ProviderProfileImportItem, _Mapping]] = ..., expected_resource_version: _Optional[int] = ..., id: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class UpdateProviderProfilesResponse(_message.Message):
    __slots__ = ("diagnostics", "profile", "updated")
    DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    PROFILE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_FIELD_NUMBER: _ClassVar[int]
    diagnostics: _containers.RepeatedCompositeFieldContainer[ProviderProfileDiagnostic]
    profile: ProviderProfile
    updated: bool
    def __init__(self, diagnostics: _Optional[_Iterable[_Union[ProviderProfileDiagnostic, _Mapping]]] = ..., profile: _Optional[_Union[ProviderProfile, _Mapping]] = ..., updated: bool = ...) -> None: ...

class LintProviderProfilesRequest(_message.Message):
    __slots__ = ("workspace_scope", "profiles")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PROFILES_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    profiles: _containers.RepeatedCompositeFieldContainer[ProviderProfileImportItem]
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., profiles: _Optional[_Iterable[_Union[ProviderProfileImportItem, _Mapping]]] = ...) -> None: ...

class LintProviderProfilesResponse(_message.Message):
    __slots__ = ("diagnostics", "valid", "supports_stable_placeholder")
    DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    VALID_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_STABLE_PLACEHOLDER_FIELD_NUMBER: _ClassVar[int]
    diagnostics: _containers.RepeatedCompositeFieldContainer[ProviderProfileDiagnostic]
    valid: bool
    supports_stable_placeholder: bool
    def __init__(self, diagnostics: _Optional[_Iterable[_Union[ProviderProfileDiagnostic, _Mapping]]] = ..., valid: bool = ..., supports_stable_placeholder: bool = ...) -> None: ...

class DeleteProviderResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class DeleteProviderProfileRequest(_message.Message):
    __slots__ = ("workspace_scope", "id", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    id: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., id: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class DeleteProviderProfileResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class GetSandboxProviderEnvironmentRequest(_message.Message):
    __slots__ = ("sandbox_id", "supports_static_credential_bindings", "supports_stable_placeholder_environment_keys")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_STATIC_CREDENTIAL_BINDINGS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_STABLE_PLACEHOLDER_ENVIRONMENT_KEYS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    supports_static_credential_bindings: bool
    supports_stable_placeholder_environment_keys: bool
    def __init__(self, sandbox_id: _Optional[str] = ..., supports_static_credential_bindings: bool = ..., supports_stable_placeholder_environment_keys: bool = ...) -> None: ...

class StaticCredentialEndpointBinding(_message.Message):
    __slots__ = ("host", "port", "path")
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    host: str
    port: int
    path: str
    def __init__(self, host: _Optional[str] = ..., port: _Optional[int] = ..., path: _Optional[str] = ...) -> None: ...

class StaticCredentialBinding(_message.Message):
    __slots__ = ("endpoints", "credential_identity", "workload_credential_handle")
    ENDPOINTS_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_IDENTITY_FIELD_NUMBER: _ClassVar[int]
    WORKLOAD_CREDENTIAL_HANDLE_FIELD_NUMBER: _ClassVar[int]
    endpoints: _containers.RepeatedCompositeFieldContainer[StaticCredentialEndpointBinding]
    credential_identity: str
    workload_credential_handle: str
    def __init__(self, endpoints: _Optional[_Iterable[_Union[StaticCredentialEndpointBinding, _Mapping]]] = ..., credential_identity: _Optional[str] = ..., workload_credential_handle: _Optional[str] = ...) -> None: ...

class GetSandboxProviderEnvironmentResponse(_message.Message):
    __slots__ = ("environment", "provider_env_revision", "credential_expiration_times", "dynamic_credentials", "static_credential_bindings", "non_secret_environment_keys", "provider_attachment_epoch", "policy_hash", "readiness_reason", "files", "stable_placeholder_environment_keys", "supports_stable_placeholder_environment_keys")
    class EnvironmentEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class CredentialExpirationTimesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: _timestamp_pb2.Timestamp
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...
    class DynamicCredentialsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: ProviderProfileCredential
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ProviderProfileCredential, _Mapping]] = ...) -> None: ...
    class StaticCredentialBindingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: StaticCredentialBinding
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[StaticCredentialBinding, _Mapping]] = ...) -> None: ...
    class FilesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENV_REVISION_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_EXPIRATION_TIMES_FIELD_NUMBER: _ClassVar[int]
    DYNAMIC_CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    STATIC_CREDENTIAL_BINDINGS_FIELD_NUMBER: _ClassVar[int]
    NON_SECRET_ENVIRONMENT_KEYS_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ATTACHMENT_EPOCH_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    READINESS_REASON_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    STABLE_PLACEHOLDER_ENVIRONMENT_KEYS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_STABLE_PLACEHOLDER_ENVIRONMENT_KEYS_FIELD_NUMBER: _ClassVar[int]
    environment: _containers.ScalarMap[str, str]
    provider_env_revision: int
    credential_expiration_times: _containers.MessageMap[str, _timestamp_pb2.Timestamp]
    dynamic_credentials: _containers.MessageMap[str, ProviderProfileCredential]
    static_credential_bindings: _containers.MessageMap[str, StaticCredentialBinding]
    non_secret_environment_keys: _containers.RepeatedScalarFieldContainer[str]
    provider_attachment_epoch: str
    policy_hash: str
    readiness_reason: ProviderReadinessReason
    files: _containers.ScalarMap[str, str]
    stable_placeholder_environment_keys: _containers.RepeatedScalarFieldContainer[str]
    supports_stable_placeholder_environment_keys: bool
    def __init__(self, environment: _Optional[_Mapping[str, str]] = ..., provider_env_revision: _Optional[int] = ..., credential_expiration_times: _Optional[_Mapping[str, _timestamp_pb2.Timestamp]] = ..., dynamic_credentials: _Optional[_Mapping[str, ProviderProfileCredential]] = ..., static_credential_bindings: _Optional[_Mapping[str, StaticCredentialBinding]] = ..., non_secret_environment_keys: _Optional[_Iterable[str]] = ..., provider_attachment_epoch: _Optional[str] = ..., policy_hash: _Optional[str] = ..., readiness_reason: _Optional[_Union[ProviderReadinessReason, str]] = ..., files: _Optional[_Mapping[str, str]] = ..., stable_placeholder_environment_keys: _Optional[_Iterable[str]] = ..., supports_stable_placeholder_environment_keys: bool = ...) -> None: ...

class ProviderEnvironmentValue(_message.Message):
    __slots__ = ("name", "value", "classification", "static_credential_binding", "expiration_time", "stable_placeholder")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CLASSIFICATION_FIELD_NUMBER: _ClassVar[int]
    STATIC_CREDENTIAL_BINDING_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    STABLE_PLACEHOLDER_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    classification: ProviderEnvironmentValueClassification
    static_credential_binding: StaticCredentialBinding
    expiration_time: _timestamp_pb2.Timestamp
    stable_placeholder: bool
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ..., classification: _Optional[_Union[ProviderEnvironmentValueClassification, str]] = ..., static_credential_binding: _Optional[_Union[StaticCredentialBinding, _Mapping]] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., stable_placeholder: bool = ...) -> None: ...

class ProviderEnvironmentSnapshot(_message.Message):
    __slots__ = ("provider_env_revision", "values", "dynamic_credentials", "provider_attachment_epoch", "policy_hash", "readiness_reason", "files")
    class DynamicCredentialsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: ProviderProfileCredential
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ProviderProfileCredential, _Mapping]] = ...) -> None: ...
    class FilesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    PROVIDER_ENV_REVISION_FIELD_NUMBER: _ClassVar[int]
    VALUES_FIELD_NUMBER: _ClassVar[int]
    DYNAMIC_CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ATTACHMENT_EPOCH_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    READINESS_REASON_FIELD_NUMBER: _ClassVar[int]
    FILES_FIELD_NUMBER: _ClassVar[int]
    provider_env_revision: int
    values: _containers.RepeatedCompositeFieldContainer[ProviderEnvironmentValue]
    dynamic_credentials: _containers.MessageMap[str, ProviderProfileCredential]
    provider_attachment_epoch: str
    policy_hash: str
    readiness_reason: ProviderReadinessReason
    files: _containers.ScalarMap[str, str]
    def __init__(self, provider_env_revision: _Optional[int] = ..., values: _Optional[_Iterable[_Union[ProviderEnvironmentValue, _Mapping]]] = ..., dynamic_credentials: _Optional[_Mapping[str, ProviderProfileCredential]] = ..., provider_attachment_epoch: _Optional[str] = ..., policy_hash: _Optional[str] = ..., readiness_reason: _Optional[_Union[ProviderReadinessReason, str]] = ..., files: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ExchangeProviderSubjectTokenRequest(_message.Message):
    __slots__ = ("sandbox_id", "provider", "credential_key", "supervisor_jwt_svid")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    SUPERVISOR_JWT_SVID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    provider: str
    credential_key: str
    supervisor_jwt_svid: str
    def __init__(self, sandbox_id: _Optional[str] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., supervisor_jwt_svid: _Optional[str] = ...) -> None: ...

class ExchangeProviderSubjectTokenResponse(_message.Message):
    __slots__ = ("access_token", "expires_after", "token_type")
    ACCESS_TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AFTER_FIELD_NUMBER: _ClassVar[int]
    TOKEN_TYPE_FIELD_NUMBER: _ClassVar[int]
    access_token: str
    expires_after: _duration_pb2.Duration
    token_type: str
    def __init__(self, access_token: _Optional[str] = ..., expires_after: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., token_type: _Optional[str] = ...) -> None: ...

class UpdateConfigRequest(_message.Message):
    __slots__ = ("workspace_scope", "policy", "setting_key", "setting_value", "delete_setting", "merge_operations", "expected_resource_version", "annotations", "sandbox", "request_id")
    class AnnotationsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    SETTING_KEY_FIELD_NUMBER: _ClassVar[int]
    SETTING_VALUE_FIELD_NUMBER: _ClassVar[int]
    DELETE_SETTING_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_FIELD_NUMBER: _ClassVar[int]
    MERGE_OPERATIONS_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    policy: _sandbox_pb2.SandboxPolicy
    setting_key: str
    setting_value: _sandbox_pb2.SettingValue
    delete_setting: bool
    merge_operations: _containers.RepeatedCompositeFieldContainer[PolicyMergeOperation]
    expected_resource_version: int
    annotations: _containers.ScalarMap[str, str]
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ..., setting_key: _Optional[str] = ..., setting_value: _Optional[_Union[_sandbox_pb2.SettingValue, _Mapping]] = ..., delete_setting: bool = ..., merge_operations: _Optional[_Iterable[_Union[PolicyMergeOperation, _Mapping]]] = ..., expected_resource_version: _Optional[int] = ..., annotations: _Optional[_Mapping[str, str]] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ..., **kwargs) -> None: ...

class PolicyMergeOperation(_message.Message):
    __slots__ = ("add_rule", "remove_endpoint", "remove_rule", "add_deny_rules", "add_allow_rules", "remove_binary")
    ADD_RULE_FIELD_NUMBER: _ClassVar[int]
    REMOVE_ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    REMOVE_RULE_FIELD_NUMBER: _ClassVar[int]
    ADD_DENY_RULES_FIELD_NUMBER: _ClassVar[int]
    ADD_ALLOW_RULES_FIELD_NUMBER: _ClassVar[int]
    REMOVE_BINARY_FIELD_NUMBER: _ClassVar[int]
    add_rule: AddNetworkRule
    remove_endpoint: RemoveNetworkEndpoint
    remove_rule: RemoveNetworkRule
    add_deny_rules: AddDenyRules
    add_allow_rules: AddAllowRules
    remove_binary: RemoveNetworkBinary
    def __init__(self, add_rule: _Optional[_Union[AddNetworkRule, _Mapping]] = ..., remove_endpoint: _Optional[_Union[RemoveNetworkEndpoint, _Mapping]] = ..., remove_rule: _Optional[_Union[RemoveNetworkRule, _Mapping]] = ..., add_deny_rules: _Optional[_Union[AddDenyRules, _Mapping]] = ..., add_allow_rules: _Optional[_Union[AddAllowRules, _Mapping]] = ..., remove_binary: _Optional[_Union[RemoveNetworkBinary, _Mapping]] = ...) -> None: ...

class AddNetworkRule(_message.Message):
    __slots__ = ("rule_name", "rule")
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    RULE_FIELD_NUMBER: _ClassVar[int]
    rule_name: str
    rule: _sandbox_pb2.NetworkPolicyRule
    def __init__(self, rule_name: _Optional[str] = ..., rule: _Optional[_Union[_sandbox_pb2.NetworkPolicyRule, _Mapping]] = ...) -> None: ...

class RemoveNetworkEndpoint(_message.Message):
    __slots__ = ("rule_name", "host", "port")
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    rule_name: str
    host: str
    port: int
    def __init__(self, rule_name: _Optional[str] = ..., host: _Optional[str] = ..., port: _Optional[int] = ...) -> None: ...

class RemoveNetworkRule(_message.Message):
    __slots__ = ("rule_name",)
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    rule_name: str
    def __init__(self, rule_name: _Optional[str] = ...) -> None: ...

class L7RuleTarget(_message.Message):
    __slots__ = ("rule_name", "host", "ports", "path", "binaries", "any_binary")
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    BINARIES_FIELD_NUMBER: _ClassVar[int]
    ANY_BINARY_FIELD_NUMBER: _ClassVar[int]
    rule_name: str
    host: str
    ports: _containers.RepeatedScalarFieldContainer[int]
    path: str
    binaries: _containers.RepeatedCompositeFieldContainer[_sandbox_pb2.NetworkBinary]
    any_binary: bool
    def __init__(self, rule_name: _Optional[str] = ..., host: _Optional[str] = ..., ports: _Optional[_Iterable[int]] = ..., path: _Optional[str] = ..., binaries: _Optional[_Iterable[_Union[_sandbox_pb2.NetworkBinary, _Mapping]]] = ..., any_binary: bool = ...) -> None: ...

class AddDenyRules(_message.Message):
    __slots__ = ("deny_rules", "target")
    DENY_RULES_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    deny_rules: _containers.RepeatedCompositeFieldContainer[_sandbox_pb2.L7DenyRule]
    target: L7RuleTarget
    def __init__(self, deny_rules: _Optional[_Iterable[_Union[_sandbox_pb2.L7DenyRule, _Mapping]]] = ..., target: _Optional[_Union[L7RuleTarget, _Mapping]] = ...) -> None: ...

class AddAllowRules(_message.Message):
    __slots__ = ("rules", "target")
    RULES_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    rules: _containers.RepeatedCompositeFieldContainer[_sandbox_pb2.L7Rule]
    target: L7RuleTarget
    def __init__(self, rules: _Optional[_Iterable[_Union[_sandbox_pb2.L7Rule, _Mapping]]] = ..., target: _Optional[_Union[L7RuleTarget, _Mapping]] = ...) -> None: ...

class RemoveNetworkBinary(_message.Message):
    __slots__ = ("rule_name", "binary_path")
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    BINARY_PATH_FIELD_NUMBER: _ClassVar[int]
    rule_name: str
    binary_path: str
    def __init__(self, rule_name: _Optional[str] = ..., binary_path: _Optional[str] = ...) -> None: ...

class UpdateConfigResponse(_message.Message):
    __slots__ = ("version", "policy_hash", "settings_revision", "deleted", "annotations")
    class AnnotationsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    SETTINGS_REVISION_FIELD_NUMBER: _ClassVar[int]
    DELETED_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    version: int
    policy_hash: str
    settings_revision: int
    deleted: bool
    annotations: _containers.ScalarMap[str, str]
    def __init__(self, version: _Optional[int] = ..., policy_hash: _Optional[str] = ..., settings_revision: _Optional[int] = ..., deleted: bool = ..., annotations: _Optional[_Mapping[str, str]] = ...) -> None: ...

class GetSandboxPolicyStatusRequest(_message.Message):
    __slots__ = ("workspace_scope", "version", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    version: int
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., version: _Optional[int] = ..., sandbox: _Optional[str] = ..., **kwargs) -> None: ...

class GetSandboxPolicyStatusResponse(_message.Message):
    __slots__ = ("revision", "active_version")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_VERSION_FIELD_NUMBER: _ClassVar[int]
    revision: SandboxPolicyRevision
    active_version: int
    def __init__(self, revision: _Optional[_Union[SandboxPolicyRevision, _Mapping]] = ..., active_version: _Optional[int] = ...) -> None: ...

class ListSandboxPoliciesRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    GLOBAL_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., sandbox: _Optional[str] = ..., **kwargs) -> None: ...

class ListSandboxPoliciesResponse(_message.Message):
    __slots__ = ("revisions", "next_page_token")
    REVISIONS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    revisions: _containers.RepeatedCompositeFieldContainer[SandboxPolicyRevision]
    next_page_token: str
    def __init__(self, revisions: _Optional[_Iterable[_Union[SandboxPolicyRevision, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class ReportPolicyStatusRequest(_message.Message):
    __slots__ = ("sandbox_id", "version", "status", "load_error")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LOAD_ERROR_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    version: int
    status: PolicyStatus
    load_error: str
    def __init__(self, sandbox_id: _Optional[str] = ..., version: _Optional[int] = ..., status: _Optional[_Union[PolicyStatus, str]] = ..., load_error: _Optional[str] = ...) -> None: ...

class ReportPolicyStatusResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SandboxConfigurationAdmission(_message.Message):
    __slots__ = ("instance_id", "state", "policy_version", "policy_hash", "config_revision", "provider_env_revision", "error")
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    CONFIG_REVISION_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENV_REVISION_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    state: ConfigurationAdmissionState
    policy_version: int
    policy_hash: str
    config_revision: int
    provider_env_revision: int
    error: str
    def __init__(self, instance_id: _Optional[str] = ..., state: _Optional[_Union[ConfigurationAdmissionState, str]] = ..., policy_version: _Optional[int] = ..., policy_hash: _Optional[str] = ..., config_revision: _Optional[int] = ..., provider_env_revision: _Optional[int] = ..., error: _Optional[str] = ...) -> None: ...

class ReportSandboxConfigurationRequest(_message.Message):
    __slots__ = ("sandbox_id", "admission", "expected_instance_id")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    ADMISSION_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    admission: SandboxConfigurationAdmission
    expected_instance_id: str
    def __init__(self, sandbox_id: _Optional[str] = ..., admission: _Optional[_Union[SandboxConfigurationAdmission, _Mapping]] = ..., expected_instance_id: _Optional[str] = ...) -> None: ...

class ReportSandboxConfigurationResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SandboxPolicyRevision(_message.Message):
    __slots__ = ("version", "policy_hash", "status", "load_error", "created_time", "loaded_time", "policy", "provenance")
    class ProvenanceEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LOAD_ERROR_FIELD_NUMBER: _ClassVar[int]
    CREATED_TIME_FIELD_NUMBER: _ClassVar[int]
    LOADED_TIME_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    version: int
    policy_hash: str
    status: PolicyStatus
    load_error: str
    created_time: _timestamp_pb2.Timestamp
    loaded_time: _timestamp_pb2.Timestamp
    policy: _sandbox_pb2.SandboxPolicy
    provenance: _containers.ScalarMap[str, str]
    def __init__(self, version: _Optional[int] = ..., policy_hash: _Optional[str] = ..., status: _Optional[_Union[PolicyStatus, str]] = ..., load_error: _Optional[str] = ..., created_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., loaded_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ..., provenance: _Optional[_Mapping[str, str]] = ...) -> None: ...

class GetSandboxLogsRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "lines", "since_time", "sources", "min_level")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    LINES_FIELD_NUMBER: _ClassVar[int]
    SINCE_TIME_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    MIN_LEVEL_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    lines: int
    since_time: _timestamp_pb2.Timestamp
    sources: _containers.RepeatedScalarFieldContainer[str]
    min_level: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., lines: _Optional[int] = ..., since_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., sources: _Optional[_Iterable[str]] = ..., min_level: _Optional[str] = ...) -> None: ...

class PushSandboxLogsRequest(_message.Message):
    __slots__ = ("sandbox_id", "logs")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    LOGS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    logs: _containers.RepeatedCompositeFieldContainer[SandboxLogLine]
    def __init__(self, sandbox_id: _Optional[str] = ..., logs: _Optional[_Iterable[_Union[SandboxLogLine, _Mapping]]] = ...) -> None: ...

class PushSandboxLogsResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetSandboxLogsResponse(_message.Message):
    __slots__ = ("logs", "buffer_total")
    LOGS_FIELD_NUMBER: _ClassVar[int]
    BUFFER_TOTAL_FIELD_NUMBER: _ClassVar[int]
    logs: _containers.RepeatedCompositeFieldContainer[SandboxLogLine]
    buffer_total: int
    def __init__(self, logs: _Optional[_Iterable[_Union[SandboxLogLine, _Mapping]]] = ..., buffer_total: _Optional[int] = ...) -> None: ...

class SupervisorMessage(_message.Message):
    __slots__ = ("hello", "heartbeat", "relay_open_result", "relay_close", "config_update_result")
    HELLO_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    RELAY_OPEN_RESULT_FIELD_NUMBER: _ClassVar[int]
    RELAY_CLOSE_FIELD_NUMBER: _ClassVar[int]
    CONFIG_UPDATE_RESULT_FIELD_NUMBER: _ClassVar[int]
    hello: SupervisorHello
    heartbeat: SupervisorHeartbeat
    relay_open_result: RelayOpenResult
    relay_close: RelayClose
    config_update_result: ConfigUpdateResult
    def __init__(self, hello: _Optional[_Union[SupervisorHello, _Mapping]] = ..., heartbeat: _Optional[_Union[SupervisorHeartbeat, _Mapping]] = ..., relay_open_result: _Optional[_Union[RelayOpenResult, _Mapping]] = ..., relay_close: _Optional[_Union[RelayClose, _Mapping]] = ..., config_update_result: _Optional[_Union[ConfigUpdateResult, _Mapping]] = ...) -> None: ...

class GatewayMessage(_message.Message):
    __slots__ = ("session_accepted", "session_rejected", "heartbeat", "relay_open", "relay_close", "session_redirect", "config_update")
    SESSION_ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    SESSION_REJECTED_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    RELAY_OPEN_FIELD_NUMBER: _ClassVar[int]
    RELAY_CLOSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_REDIRECT_FIELD_NUMBER: _ClassVar[int]
    CONFIG_UPDATE_FIELD_NUMBER: _ClassVar[int]
    session_accepted: SessionAccepted
    session_rejected: SessionRejected
    heartbeat: GatewayHeartbeat
    relay_open: RelayOpen
    relay_close: RelayClose
    session_redirect: SessionRedirect
    config_update: ConfigUpdate
    def __init__(self, session_accepted: _Optional[_Union[SessionAccepted, _Mapping]] = ..., session_rejected: _Optional[_Union[SessionRejected, _Mapping]] = ..., heartbeat: _Optional[_Union[GatewayHeartbeat, _Mapping]] = ..., relay_open: _Optional[_Union[RelayOpen, _Mapping]] = ..., relay_close: _Optional[_Union[RelayClose, _Mapping]] = ..., session_redirect: _Optional[_Union[SessionRedirect, _Mapping]] = ..., config_update: _Optional[_Union[ConfigUpdate, _Mapping]] = ...) -> None: ...

class SupervisorHello(_message.Message):
    __slots__ = ("sandbox_id", "instance_id", "connection_epoch", "supports_provider_readiness", "redirected", "supports_session_redirect", "supports_config_push")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_EPOCH_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_PROVIDER_READINESS_FIELD_NUMBER: _ClassVar[int]
    REDIRECTED_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_SESSION_REDIRECT_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_CONFIG_PUSH_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    instance_id: str
    connection_epoch: int
    supports_provider_readiness: bool
    redirected: bool
    supports_session_redirect: bool
    supports_config_push: bool
    def __init__(self, sandbox_id: _Optional[str] = ..., instance_id: _Optional[str] = ..., connection_epoch: _Optional[int] = ..., supports_provider_readiness: bool = ..., redirected: bool = ..., supports_session_redirect: bool = ..., supports_config_push: bool = ...) -> None: ...

class SessionAccepted(_message.Message):
    __slots__ = ("session_id", "bootstrap", "config_push_enabled", "heartbeat_interval")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    BOOTSTRAP_FIELD_NUMBER: _ClassVar[int]
    CONFIG_PUSH_ENABLED_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_INTERVAL_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    bootstrap: ConfigUpdate
    config_push_enabled: bool
    heartbeat_interval: _duration_pb2.Duration
    def __init__(self, session_id: _Optional[str] = ..., bootstrap: _Optional[_Union[ConfigUpdate, _Mapping]] = ..., config_push_enabled: bool = ..., heartbeat_interval: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ...) -> None: ...

class ConfigUpdate(_message.Message):
    __slots__ = ("update_id", "sandbox_config", "provider_environment")
    UPDATE_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_CONFIG_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    update_id: str
    sandbox_config: _sandbox_pb2.SandboxConfigSnapshot
    provider_environment: ProviderEnvironmentSnapshot
    def __init__(self, update_id: _Optional[str] = ..., sandbox_config: _Optional[_Union[_sandbox_pb2.SandboxConfigSnapshot, _Mapping]] = ..., provider_environment: _Optional[_Union[ProviderEnvironmentSnapshot, _Mapping]] = ...) -> None: ...

class ConfigApplyFailure(_message.Message):
    __slots__ = ("code", "message", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: bool = ...) -> None: ...

class ConfigUpdateResult(_message.Message):
    __slots__ = ("update_id", "outcome", "failure", "admission", "policy_load")
    UPDATE_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    ADMISSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_LOAD_FIELD_NUMBER: _ClassVar[int]
    update_id: str
    outcome: ConfigApplyOutcome
    failure: ConfigApplyFailure
    admission: SandboxConfigurationAdmission
    policy_load: PolicyLoadResult
    def __init__(self, update_id: _Optional[str] = ..., outcome: _Optional[_Union[ConfigApplyOutcome, str]] = ..., failure: _Optional[_Union[ConfigApplyFailure, _Mapping]] = ..., admission: _Optional[_Union[SandboxConfigurationAdmission, _Mapping]] = ..., policy_load: _Optional[_Union[PolicyLoadResult, _Mapping]] = ...) -> None: ...

class PolicyLoadResult(_message.Message):
    __slots__ = ("version", "loaded", "error")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    LOADED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    version: int
    loaded: bool
    error: str
    def __init__(self, version: _Optional[int] = ..., loaded: bool = ..., error: _Optional[str] = ...) -> None: ...

class SessionRejected(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class SessionRedirect(_message.Message):
    __slots__ = ("peer_endpoint", "owner_replica_id")
    PEER_ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    OWNER_REPLICA_ID_FIELD_NUMBER: _ClassVar[int]
    peer_endpoint: str
    owner_replica_id: str
    def __init__(self, peer_endpoint: _Optional[str] = ..., owner_replica_id: _Optional[str] = ...) -> None: ...

class SupervisorHeartbeat(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GatewayHeartbeat(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ReportMainProcessExitRequest(_message.Message):
    __slots__ = ("sandbox_id", "instance_id", "exit_code")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    instance_id: str
    exit_code: int
    def __init__(self, sandbox_id: _Optional[str] = ..., instance_id: _Optional[str] = ..., exit_code: _Optional[int] = ...) -> None: ...

class ReportMainProcessExitResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class FinalizeMainProcessExitRequest(_message.Message):
    __slots__ = ("sandbox_id", "instance_id")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    instance_id: str
    def __init__(self, sandbox_id: _Optional[str] = ..., instance_id: _Optional[str] = ...) -> None: ...

class FinalizeMainProcessExitResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class RelayOpen(_message.Message):
    __slots__ = ("channel_id", "ssh", "tcp", "service_id")
    CHANNEL_ID_FIELD_NUMBER: _ClassVar[int]
    SSH_FIELD_NUMBER: _ClassVar[int]
    TCP_FIELD_NUMBER: _ClassVar[int]
    SERVICE_ID_FIELD_NUMBER: _ClassVar[int]
    channel_id: str
    ssh: SshRelayTarget
    tcp: TcpRelayTarget
    service_id: str
    def __init__(self, channel_id: _Optional[str] = ..., ssh: _Optional[_Union[SshRelayTarget, _Mapping]] = ..., tcp: _Optional[_Union[TcpRelayTarget, _Mapping]] = ..., service_id: _Optional[str] = ...) -> None: ...

class SshRelayTarget(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class TcpRelayTarget(_message.Message):
    __slots__ = ("host", "port")
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    host: str
    port: int
    def __init__(self, host: _Optional[str] = ..., port: _Optional[int] = ...) -> None: ...

class RelayInit(_message.Message):
    __slots__ = ("channel_id",)
    CHANNEL_ID_FIELD_NUMBER: _ClassVar[int]
    channel_id: str
    def __init__(self, channel_id: _Optional[str] = ...) -> None: ...

class RelayFrame(_message.Message):
    __slots__ = ("init", "data")
    INIT_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    init: RelayInit
    data: bytes
    def __init__(self, init: _Optional[_Union[RelayInit, _Mapping]] = ..., data: _Optional[bytes] = ...) -> None: ...

class PeerRelayInit(_message.Message):
    __slots__ = ("sandbox_id", "relay_open", "requester_replica_id")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    RELAY_OPEN_FIELD_NUMBER: _ClassVar[int]
    REQUESTER_REPLICA_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    relay_open: RelayOpen
    requester_replica_id: str
    def __init__(self, sandbox_id: _Optional[str] = ..., relay_open: _Optional[_Union[RelayOpen, _Mapping]] = ..., requester_replica_id: _Optional[str] = ...) -> None: ...

class PeerRelayFrame(_message.Message):
    __slots__ = ("init", "data")
    INIT_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    init: PeerRelayInit
    data: bytes
    def __init__(self, init: _Optional[_Union[PeerRelayInit, _Mapping]] = ..., data: _Optional[bytes] = ...) -> None: ...

class PeerConfigSandboxTarget(_message.Message):
    __slots__ = ("sandbox_id", "session_id")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    session_id: str
    def __init__(self, sandbox_id: _Optional[str] = ..., session_id: _Optional[str] = ...) -> None: ...

class PeerConfigProviderTarget(_message.Message):
    __slots__ = ("workspace", "name")
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    workspace: str
    name: str
    def __init__(self, workspace: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class PeerNotifyConfigUpdateRequest(_message.Message):
    __slots__ = ("sandbox", "workspace", "all_connected", "provider")
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    ALL_CONNECTED_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    sandbox: PeerConfigSandboxTarget
    workspace: str
    all_connected: bool
    provider: PeerConfigProviderTarget
    def __init__(self, sandbox: _Optional[_Union[PeerConfigSandboxTarget, _Mapping]] = ..., workspace: _Optional[str] = ..., all_connected: bool = ..., provider: _Optional[_Union[PeerConfigProviderTarget, _Mapping]] = ...) -> None: ...

class PeerNotifyConfigUpdateResponse(_message.Message):
    __slots__ = ("stale_owner",)
    STALE_OWNER_FIELD_NUMBER: _ClassVar[int]
    stale_owner: bool
    def __init__(self, stale_owner: bool = ...) -> None: ...

class RelayOpenResult(_message.Message):
    __slots__ = ("channel_id", "success", "error")
    CHANNEL_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    channel_id: str
    success: bool
    error: str
    def __init__(self, channel_id: _Optional[str] = ..., success: bool = ..., error: _Optional[str] = ...) -> None: ...

class RelayClose(_message.Message):
    __slots__ = ("channel_id", "reason")
    CHANNEL_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    channel_id: str
    reason: str
    def __init__(self, channel_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class L7RequestSample(_message.Message):
    __slots__ = ("method", "path", "decision", "count")
    METHOD_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    method: str
    path: str
    decision: str
    count: int
    def __init__(self, method: _Optional[str] = ..., path: _Optional[str] = ..., decision: _Optional[str] = ..., count: _Optional[int] = ...) -> None: ...

class DenialSummary(_message.Message):
    __slots__ = ("sandbox_id", "host", "port", "binary", "ancestors", "deny_reason", "first_seen_time", "last_seen_time", "count", "suppressed_count", "total_count", "sample_cmdlines", "binary_sha256", "persistent", "denial_stage", "l7_request_samples", "l7_inspection_active")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    ANCESTORS_FIELD_NUMBER: _ClassVar[int]
    DENY_REASON_FIELD_NUMBER: _ClassVar[int]
    FIRST_SEEN_TIME_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_TIME_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    SUPPRESSED_COUNT_FIELD_NUMBER: _ClassVar[int]
    TOTAL_COUNT_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_CMDLINES_FIELD_NUMBER: _ClassVar[int]
    BINARY_SHA256_FIELD_NUMBER: _ClassVar[int]
    PERSISTENT_FIELD_NUMBER: _ClassVar[int]
    DENIAL_STAGE_FIELD_NUMBER: _ClassVar[int]
    L7_REQUEST_SAMPLES_FIELD_NUMBER: _ClassVar[int]
    L7_INSPECTION_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    host: str
    port: int
    binary: str
    ancestors: _containers.RepeatedScalarFieldContainer[str]
    deny_reason: str
    first_seen_time: _timestamp_pb2.Timestamp
    last_seen_time: _timestamp_pb2.Timestamp
    count: int
    suppressed_count: int
    total_count: int
    sample_cmdlines: _containers.RepeatedScalarFieldContainer[str]
    binary_sha256: str
    persistent: bool
    denial_stage: str
    l7_request_samples: _containers.RepeatedCompositeFieldContainer[L7RequestSample]
    l7_inspection_active: bool
    def __init__(self, sandbox_id: _Optional[str] = ..., host: _Optional[str] = ..., port: _Optional[int] = ..., binary: _Optional[str] = ..., ancestors: _Optional[_Iterable[str]] = ..., deny_reason: _Optional[str] = ..., first_seen_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_seen_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., count: _Optional[int] = ..., suppressed_count: _Optional[int] = ..., total_count: _Optional[int] = ..., sample_cmdlines: _Optional[_Iterable[str]] = ..., binary_sha256: _Optional[str] = ..., persistent: bool = ..., denial_stage: _Optional[str] = ..., l7_request_samples: _Optional[_Iterable[_Union[L7RequestSample, _Mapping]]] = ..., l7_inspection_active: bool = ...) -> None: ...

class DenialGroupCount(_message.Message):
    __slots__ = ("deny_group", "denied_count")
    DENY_GROUP_FIELD_NUMBER: _ClassVar[int]
    DENIED_COUNT_FIELD_NUMBER: _ClassVar[int]
    deny_group: str
    denied_count: int
    def __init__(self, deny_group: _Optional[str] = ..., denied_count: _Optional[int] = ...) -> None: ...

class NetworkActivitySummary(_message.Message):
    __slots__ = ("network_activity_count", "denied_action_count", "denials_by_group")
    NETWORK_ACTIVITY_COUNT_FIELD_NUMBER: _ClassVar[int]
    DENIED_ACTION_COUNT_FIELD_NUMBER: _ClassVar[int]
    DENIALS_BY_GROUP_FIELD_NUMBER: _ClassVar[int]
    network_activity_count: int
    denied_action_count: int
    denials_by_group: _containers.RepeatedCompositeFieldContainer[DenialGroupCount]
    def __init__(self, network_activity_count: _Optional[int] = ..., denied_action_count: _Optional[int] = ..., denials_by_group: _Optional[_Iterable[_Union[DenialGroupCount, _Mapping]]] = ...) -> None: ...

class PolicyChunk(_message.Message):
    __slots__ = ("id", "status", "rule_name", "proposed_rule", "rationale", "security_notes", "confidence", "denial_summary_ids", "created_time", "decided_time", "stage", "supersedes_chunk_id", "hit_count", "first_seen_time", "last_seen_time", "binary", "validation_result", "rejection_reason", "application_error", "review_token", "current_effective_policy_hash", "candidate_effective_policy_hash", "current_effective_policy", "candidate_effective_policy")
    ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    RULE_NAME_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_RULE_FIELD_NUMBER: _ClassVar[int]
    RATIONALE_FIELD_NUMBER: _ClassVar[int]
    SECURITY_NOTES_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    DENIAL_SUMMARY_IDS_FIELD_NUMBER: _ClassVar[int]
    CREATED_TIME_FIELD_NUMBER: _ClassVar[int]
    DECIDED_TIME_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    SUPERSEDES_CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    HIT_COUNT_FIELD_NUMBER: _ClassVar[int]
    FIRST_SEEN_TIME_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_TIME_FIELD_NUMBER: _ClassVar[int]
    BINARY_FIELD_NUMBER: _ClassVar[int]
    VALIDATION_RESULT_FIELD_NUMBER: _ClassVar[int]
    REJECTION_REASON_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_ERROR_FIELD_NUMBER: _ClassVar[int]
    REVIEW_TOKEN_FIELD_NUMBER: _ClassVar[int]
    CURRENT_EFFECTIVE_POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_EFFECTIVE_POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    CURRENT_EFFECTIVE_POLICY_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_EFFECTIVE_POLICY_FIELD_NUMBER: _ClassVar[int]
    id: str
    status: str
    rule_name: str
    proposed_rule: _sandbox_pb2.NetworkPolicyRule
    rationale: str
    security_notes: str
    confidence: float
    denial_summary_ids: _containers.RepeatedScalarFieldContainer[str]
    created_time: _timestamp_pb2.Timestamp
    decided_time: _timestamp_pb2.Timestamp
    stage: str
    supersedes_chunk_id: str
    hit_count: int
    first_seen_time: _timestamp_pb2.Timestamp
    last_seen_time: _timestamp_pb2.Timestamp
    binary: str
    validation_result: str
    rejection_reason: str
    application_error: str
    review_token: str
    current_effective_policy_hash: str
    candidate_effective_policy_hash: str
    current_effective_policy: _sandbox_pb2.SandboxPolicy
    candidate_effective_policy: _sandbox_pb2.SandboxPolicy
    def __init__(self, id: _Optional[str] = ..., status: _Optional[str] = ..., rule_name: _Optional[str] = ..., proposed_rule: _Optional[_Union[_sandbox_pb2.NetworkPolicyRule, _Mapping]] = ..., rationale: _Optional[str] = ..., security_notes: _Optional[str] = ..., confidence: _Optional[float] = ..., denial_summary_ids: _Optional[_Iterable[str]] = ..., created_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., decided_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., stage: _Optional[str] = ..., supersedes_chunk_id: _Optional[str] = ..., hit_count: _Optional[int] = ..., first_seen_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_seen_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., binary: _Optional[str] = ..., validation_result: _Optional[str] = ..., rejection_reason: _Optional[str] = ..., application_error: _Optional[str] = ..., review_token: _Optional[str] = ..., current_effective_policy_hash: _Optional[str] = ..., candidate_effective_policy_hash: _Optional[str] = ..., current_effective_policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ..., candidate_effective_policy: _Optional[_Union[_sandbox_pb2.SandboxPolicy, _Mapping]] = ...) -> None: ...

class DraftPolicyUpdate(_message.Message):
    __slots__ = ("draft_version", "new_chunks", "total_pending", "summary")
    DRAFT_VERSION_FIELD_NUMBER: _ClassVar[int]
    NEW_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_PENDING_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    draft_version: int
    new_chunks: int
    total_pending: int
    summary: str
    def __init__(self, draft_version: _Optional[int] = ..., new_chunks: _Optional[int] = ..., total_pending: _Optional[int] = ..., summary: _Optional[str] = ...) -> None: ...

class SubmitPolicyAnalysisRequest(_message.Message):
    __slots__ = ("workspace_scope", "summaries", "proposed_chunks", "analysis_mode", "name", "network_activity_summaries")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SUMMARIES_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    ANALYSIS_MODE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    NETWORK_ACTIVITY_SUMMARIES_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    summaries: _containers.RepeatedCompositeFieldContainer[DenialSummary]
    proposed_chunks: _containers.RepeatedCompositeFieldContainer[PolicyChunk]
    analysis_mode: str
    name: str
    network_activity_summaries: _containers.RepeatedCompositeFieldContainer[NetworkActivitySummary]
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., summaries: _Optional[_Iterable[_Union[DenialSummary, _Mapping]]] = ..., proposed_chunks: _Optional[_Iterable[_Union[PolicyChunk, _Mapping]]] = ..., analysis_mode: _Optional[str] = ..., name: _Optional[str] = ..., network_activity_summaries: _Optional[_Iterable[_Union[NetworkActivitySummary, _Mapping]]] = ...) -> None: ...

class SubmitPolicyAnalysisResponse(_message.Message):
    __slots__ = ("accepted_chunks", "rejected_chunks", "rejection_reasons", "accepted_chunk_ids")
    ACCEPTED_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    REJECTED_CHUNKS_FIELD_NUMBER: _ClassVar[int]
    REJECTION_REASONS_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_CHUNK_IDS_FIELD_NUMBER: _ClassVar[int]
    accepted_chunks: int
    rejected_chunks: int
    rejection_reasons: _containers.RepeatedScalarFieldContainer[str]
    accepted_chunk_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, accepted_chunks: _Optional[int] = ..., rejected_chunks: _Optional[int] = ..., rejection_reasons: _Optional[_Iterable[str]] = ..., accepted_chunk_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class GetDraftPolicyRequest(_message.Message):
    __slots__ = ("workspace_scope", "status_filter", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    STATUS_FILTER_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    status_filter: str
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., status_filter: _Optional[str] = ..., sandbox: _Optional[str] = ...) -> None: ...

class GetDraftPolicyResponse(_message.Message):
    __slots__ = ("chunks", "rolling_summary", "draft_version", "last_analyzed_time")
    CHUNKS_FIELD_NUMBER: _ClassVar[int]
    ROLLING_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    DRAFT_VERSION_FIELD_NUMBER: _ClassVar[int]
    LAST_ANALYZED_TIME_FIELD_NUMBER: _ClassVar[int]
    chunks: _containers.RepeatedCompositeFieldContainer[PolicyChunk]
    rolling_summary: str
    draft_version: int
    last_analyzed_time: _timestamp_pb2.Timestamp
    def __init__(self, chunks: _Optional[_Iterable[_Union[PolicyChunk, _Mapping]]] = ..., rolling_summary: _Optional[str] = ..., draft_version: _Optional[int] = ..., last_analyzed_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ApproveDraftChunkRequest(_message.Message):
    __slots__ = ("workspace_scope", "chunk_id", "review_token", "sandbox", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_TOKEN_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    chunk_id: str
    review_token: str
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., chunk_id: _Optional[str] = ..., review_token: _Optional[str] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class ApproveDraftChunkResponse(_message.Message):
    __slots__ = ("policy_version", "policy_hash")
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    policy_version: int
    policy_hash: str
    def __init__(self, policy_version: _Optional[int] = ..., policy_hash: _Optional[str] = ...) -> None: ...

class RejectDraftChunkRequest(_message.Message):
    __slots__ = ("workspace_scope", "chunk_id", "reason", "sandbox", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    chunk_id: str
    reason: str
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., chunk_id: _Optional[str] = ..., reason: _Optional[str] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class RejectDraftChunkResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class DraftChunkApproval(_message.Message):
    __slots__ = ("chunk_id", "review_token")
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    REVIEW_TOKEN_FIELD_NUMBER: _ClassVar[int]
    chunk_id: str
    review_token: str
    def __init__(self, chunk_id: _Optional[str] = ..., review_token: _Optional[str] = ...) -> None: ...

class ApproveAllDraftChunksRequest(_message.Message):
    __slots__ = ("workspace_scope", "include_security_flagged", "approvals", "sandbox", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_SECURITY_FLAGGED_FIELD_NUMBER: _ClassVar[int]
    APPROVALS_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    include_security_flagged: bool
    approvals: _containers.RepeatedCompositeFieldContainer[DraftChunkApproval]
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., include_security_flagged: bool = ..., approvals: _Optional[_Iterable[_Union[DraftChunkApproval, _Mapping]]] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class ApproveAllDraftChunksResponse(_message.Message):
    __slots__ = ("policy_version", "policy_hash", "chunks_approved", "chunks_skipped")
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_APPROVED_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_SKIPPED_FIELD_NUMBER: _ClassVar[int]
    policy_version: int
    policy_hash: str
    chunks_approved: int
    chunks_skipped: int
    def __init__(self, policy_version: _Optional[int] = ..., policy_hash: _Optional[str] = ..., chunks_approved: _Optional[int] = ..., chunks_skipped: _Optional[int] = ...) -> None: ...

class EditDraftChunkRequest(_message.Message):
    __slots__ = ("workspace_scope", "chunk_id", "proposed_rule", "sandbox", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_RULE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    chunk_id: str
    proposed_rule: _sandbox_pb2.NetworkPolicyRule
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., chunk_id: _Optional[str] = ..., proposed_rule: _Optional[_Union[_sandbox_pb2.NetworkPolicyRule, _Mapping]] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class EditDraftChunkResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class UndoDraftChunkRequest(_message.Message):
    __slots__ = ("workspace_scope", "chunk_id", "sandbox", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    chunk_id: str
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., chunk_id: _Optional[str] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class UndoDraftChunkResponse(_message.Message):
    __slots__ = ("policy_version", "policy_hash")
    POLICY_VERSION_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    policy_version: int
    policy_hash: str
    def __init__(self, policy_version: _Optional[int] = ..., policy_hash: _Optional[str] = ...) -> None: ...

class ClearDraftChunksRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ..., request_id: _Optional[str] = ...) -> None: ...

class ClearDraftChunksResponse(_message.Message):
    __slots__ = ("chunks_cleared",)
    CHUNKS_CLEARED_FIELD_NUMBER: _ClassVar[int]
    chunks_cleared: int
    def __init__(self, chunks_cleared: _Optional[int] = ...) -> None: ...

class GetDraftHistoryRequest(_message.Message):
    __slots__ = ("workspace_scope", "sandbox")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    SANDBOX_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    sandbox: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., sandbox: _Optional[str] = ...) -> None: ...

class DraftHistoryEntry(_message.Message):
    __slots__ = ("event_time", "event_type", "description", "chunk_id")
    EVENT_TIME_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    event_time: _timestamp_pb2.Timestamp
    event_type: str
    description: str
    chunk_id: str
    def __init__(self, event_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., event_type: _Optional[str] = ..., description: _Optional[str] = ..., chunk_id: _Optional[str] = ...) -> None: ...

class GetDraftHistoryResponse(_message.Message):
    __slots__ = ("entries",)
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    entries: _containers.RepeatedCompositeFieldContainer[DraftHistoryEntry]
    def __init__(self, entries: _Optional[_Iterable[_Union[DraftHistoryEntry, _Mapping]]] = ...) -> None: ...

class CreateWorkspaceRequest(_message.Message):
    __slots__ = ("name", "labels", "request_id")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    labels: _containers.ScalarMap[str, str]
    request_id: str
    def __init__(self, name: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., request_id: _Optional[str] = ...) -> None: ...

class CreateWorkspaceResponse(_message.Message):
    __slots__ = ("workspace",)
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    workspace: _datamodel_pb2.Workspace
    def __init__(self, workspace: _Optional[_Union[_datamodel_pb2.Workspace, _Mapping]] = ...) -> None: ...

class GetWorkspaceRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class GetWorkspaceResponse(_message.Message):
    __slots__ = ("workspace",)
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    workspace: _datamodel_pb2.Workspace
    def __init__(self, workspace: _Optional[_Union[_datamodel_pb2.Workspace, _Mapping]] = ...) -> None: ...

class ListWorkspacesRequest(_message.Message):
    __slots__ = ("page_size", "page_token", "label_selector")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    LABEL_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    page_token: str
    label_selector: str
    def __init__(self, page_size: _Optional[int] = ..., page_token: _Optional[str] = ..., label_selector: _Optional[str] = ...) -> None: ...

class ListWorkspacesResponse(_message.Message):
    __slots__ = ("workspaces", "next_page_token")
    WORKSPACES_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    workspaces: _containers.RepeatedCompositeFieldContainer[_datamodel_pb2.Workspace]
    next_page_token: str
    def __init__(self, workspaces: _Optional[_Iterable[_Union[_datamodel_pb2.Workspace, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class DeleteWorkspaceRequest(_message.Message):
    __slots__ = ("name", "allow_missing", "request_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    allow_missing: bool
    request_id: str
    def __init__(self, name: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class DeleteWorkspaceResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class WorkspaceMember(_message.Message):
    __slots__ = ("metadata", "principal_subject", "role")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    PRINCIPAL_SUBJECT_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    metadata: _datamodel_pb2.ObjectMeta
    principal_subject: str
    role: WorkspaceRole
    def __init__(self, metadata: _Optional[_Union[_datamodel_pb2.ObjectMeta, _Mapping]] = ..., principal_subject: _Optional[str] = ..., role: _Optional[_Union[WorkspaceRole, str]] = ...) -> None: ...

class AddWorkspaceMemberRequest(_message.Message):
    __slots__ = ("workspace_scope", "principal_subject", "role", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PRINCIPAL_SUBJECT_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    principal_subject: str
    role: WorkspaceRole
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., principal_subject: _Optional[str] = ..., role: _Optional[_Union[WorkspaceRole, str]] = ..., request_id: _Optional[str] = ...) -> None: ...

class AddWorkspaceMemberResponse(_message.Message):
    __slots__ = ("member",)
    MEMBER_FIELD_NUMBER: _ClassVar[int]
    member: WorkspaceMember
    def __init__(self, member: _Optional[_Union[WorkspaceMember, _Mapping]] = ...) -> None: ...

class RemoveWorkspaceMemberRequest(_message.Message):
    __slots__ = ("workspace_scope", "principal_subject", "allow_missing", "request_id")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PRINCIPAL_SUBJECT_FIELD_NUMBER: _ClassVar[int]
    ALLOW_MISSING_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    principal_subject: str
    allow_missing: bool
    request_id: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., principal_subject: _Optional[str] = ..., allow_missing: bool = ..., request_id: _Optional[str] = ...) -> None: ...

class RemoveWorkspaceMemberResponse(_message.Message):
    __slots__ = ("outcome",)
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: DeletionOutcome
    def __init__(self, outcome: _Optional[_Union[DeletionOutcome, str]] = ...) -> None: ...

class ListWorkspaceMembersRequest(_message.Message):
    __slots__ = ("workspace_scope", "page_size", "page_token")
    WORKSPACE_SCOPE_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    workspace_scope: _datamodel_pb2.WorkspaceSelector
    page_size: int
    page_token: str
    def __init__(self, workspace_scope: _Optional[_Union[_datamodel_pb2.WorkspaceSelector, _Mapping]] = ..., page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class ListWorkspaceMembersResponse(_message.Message):
    __slots__ = ("members", "next_page_token")
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    members: _containers.RepeatedCompositeFieldContainer[WorkspaceMember]
    next_page_token: str
    def __init__(self, members: _Optional[_Iterable[_Union[WorkspaceMember, _Mapping]]] = ..., next_page_token: _Optional[str] = ...) -> None: ...

class ExtensionServiceCredential(_message.Message):
    __slots__ = ("service_name", "token", "expiration_time")
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    service_name: str
    token: str
    expiration_time: _timestamp_pb2.Timestamp
    def __init__(self, service_name: _Optional[str] = ..., token: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class EndpointObservation(_message.Message):
    __slots__ = ("endpoint_id", "result", "failure")
    ENDPOINT_ID_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    endpoint_id: str
    result: EndpointResult
    failure: EndpointFailure
    def __init__(self, endpoint_id: _Optional[str] = ..., result: _Optional[_Union[EndpointResult, str]] = ..., failure: _Optional[_Union[EndpointFailure, _Mapping]] = ...) -> None: ...

class ReportEndpointStatusRequest(_message.Message):
    __slots__ = ("sandbox_id", "policy_hash", "provider_env_revision", "observations", "observed_endpoint_ids", "supervisor_session_id", "report_sequence", "token_grant_status")
    SANDBOX_ID_FIELD_NUMBER: _ClassVar[int]
    POLICY_HASH_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ENV_REVISION_FIELD_NUMBER: _ClassVar[int]
    OBSERVATIONS_FIELD_NUMBER: _ClassVar[int]
    OBSERVED_ENDPOINT_IDS_FIELD_NUMBER: _ClassVar[int]
    SUPERVISOR_SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    REPORT_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    TOKEN_GRANT_STATUS_FIELD_NUMBER: _ClassVar[int]
    sandbox_id: str
    policy_hash: str
    provider_env_revision: int
    observations: _containers.RepeatedCompositeFieldContainer[EndpointObservation]
    observed_endpoint_ids: _containers.RepeatedScalarFieldContainer[str]
    supervisor_session_id: str
    report_sequence: int
    token_grant_status: TokenGrantStatus
    def __init__(self, sandbox_id: _Optional[str] = ..., policy_hash: _Optional[str] = ..., provider_env_revision: _Optional[int] = ..., observations: _Optional[_Iterable[_Union[EndpointObservation, _Mapping]]] = ..., observed_endpoint_ids: _Optional[_Iterable[str]] = ..., supervisor_session_id: _Optional[str] = ..., report_sequence: _Optional[int] = ..., token_grant_status: _Optional[_Union[TokenGrantStatus, _Mapping]] = ...) -> None: ...

class TokenGrantStatus(_message.Message):
    __slots__ = ("last_failure",)
    LAST_FAILURE_FIELD_NUMBER: _ClassVar[int]
    last_failure: TokenGrantFailureObservation
    def __init__(self, last_failure: _Optional[_Union[TokenGrantFailureObservation, _Mapping]] = ...) -> None: ...

class TokenGrantFailureObservation(_message.Message):
    __slots__ = ("destination_host", "destination_port", "failure")
    DESTINATION_HOST_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_PORT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    destination_host: str
    destination_port: int
    failure: EndpointFailure
    def __init__(self, destination_host: _Optional[str] = ..., destination_port: _Optional[int] = ..., failure: _Optional[_Union[EndpointFailure, _Mapping]] = ...) -> None: ...

class ReportEndpointStatusResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class EndpointStatus(_message.Message):
    __slots__ = ("endpoint_id", "host", "ports", "path", "last_result", "last_reported_time", "last_failure")
    ENDPOINT_ID_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    LAST_RESULT_FIELD_NUMBER: _ClassVar[int]
    LAST_REPORTED_TIME_FIELD_NUMBER: _ClassVar[int]
    LAST_FAILURE_FIELD_NUMBER: _ClassVar[int]
    endpoint_id: str
    host: str
    ports: _containers.RepeatedScalarFieldContainer[int]
    path: str
    last_result: EndpointResult
    last_reported_time: _timestamp_pb2.Timestamp
    last_failure: EndpointFailure
    def __init__(self, endpoint_id: _Optional[str] = ..., host: _Optional[str] = ..., ports: _Optional[_Iterable[int]] = ..., path: _Optional[str] = ..., last_result: _Optional[_Union[EndpointResult, str]] = ..., last_reported_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_failure: _Optional[_Union[EndpointFailure, _Mapping]] = ...) -> None: ...

class SandboxProvisioning(_message.Message):
    __slots__ = ("attempt_id", "configuration_change_id", "configuration_change_time", "first_rejection_time", "deadline", "timeout_time", "cleanup_completed_time", "cleanup_error", "cleanup_retry_time", "attachment_change_id", "attachment_change_time", "preparation_deadline", "admission_start_time", "driver_operation_pending", "driver_operation_id")
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_CHANGE_ID_FIELD_NUMBER: _ClassVar[int]
    CONFIGURATION_CHANGE_TIME_FIELD_NUMBER: _ClassVar[int]
    FIRST_REJECTION_TIME_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_TIME_FIELD_NUMBER: _ClassVar[int]
    CLEANUP_COMPLETED_TIME_FIELD_NUMBER: _ClassVar[int]
    CLEANUP_ERROR_FIELD_NUMBER: _ClassVar[int]
    CLEANUP_RETRY_TIME_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_CHANGE_ID_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENT_CHANGE_TIME_FIELD_NUMBER: _ClassVar[int]
    PREPARATION_DEADLINE_FIELD_NUMBER: _ClassVar[int]
    ADMISSION_START_TIME_FIELD_NUMBER: _ClassVar[int]
    DRIVER_OPERATION_PENDING_FIELD_NUMBER: _ClassVar[int]
    DRIVER_OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    attempt_id: str
    configuration_change_id: str
    configuration_change_time: _timestamp_pb2.Timestamp
    first_rejection_time: _timestamp_pb2.Timestamp
    deadline: _timestamp_pb2.Timestamp
    timeout_time: _timestamp_pb2.Timestamp
    cleanup_completed_time: _timestamp_pb2.Timestamp
    cleanup_error: str
    cleanup_retry_time: _timestamp_pb2.Timestamp
    attachment_change_id: str
    attachment_change_time: _timestamp_pb2.Timestamp
    preparation_deadline: _timestamp_pb2.Timestamp
    admission_start_time: _timestamp_pb2.Timestamp
    driver_operation_pending: bool
    driver_operation_id: str
    def __init__(self, attempt_id: _Optional[str] = ..., configuration_change_id: _Optional[str] = ..., configuration_change_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., first_rejection_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., deadline: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., timeout_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., cleanup_completed_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., cleanup_error: _Optional[str] = ..., cleanup_retry_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., attachment_change_id: _Optional[str] = ..., attachment_change_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., preparation_deadline: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., admission_start_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., driver_operation_pending: bool = ..., driver_operation_id: _Optional[str] = ...) -> None: ...

class SandboxServiceExposure(_message.Message):
    __slots__ = ("service", "target_port", "authorization_mode")
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    TARGET_PORT_FIELD_NUMBER: _ClassVar[int]
    AUTHORIZATION_MODE_FIELD_NUMBER: _ClassVar[int]
    service: str
    target_port: int
    authorization_mode: ServiceAuthorizationMode
    def __init__(self, service: _Optional[str] = ..., target_port: _Optional[int] = ..., authorization_mode: _Optional[_Union[ServiceAuthorizationMode, str]] = ...) -> None: ...

class EndpointFailure(_message.Message):
    __slots__ = ("domain", "code", "recovery_action", "provider", "credential_key", "stage", "upstream_http_status", "request_id")
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    RECOVERY_ACTION_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    UPSTREAM_HTTP_STATUS_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    domain: EndpointFailureDomain
    code: str
    recovery_action: ProviderCredentialRefreshRecoveryAction
    provider: str
    credential_key: str
    stage: str
    upstream_http_status: int
    request_id: str
    def __init__(self, domain: _Optional[_Union[EndpointFailureDomain, str]] = ..., code: _Optional[str] = ..., recovery_action: _Optional[_Union[ProviderCredentialRefreshRecoveryAction, str]] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., stage: _Optional[str] = ..., upstream_http_status: _Optional[int] = ..., request_id: _Optional[str] = ...) -> None: ...

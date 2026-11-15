import datetime

from . import options_pb2 as _options_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class WorkspacePhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WORKSPACE_PHASE_UNSPECIFIED: _ClassVar[WorkspacePhase]
    WORKSPACE_PHASE_ACTIVE: _ClassVar[WorkspacePhase]
    WORKSPACE_PHASE_TERMINATING: _ClassVar[WorkspacePhase]
WORKSPACE_PHASE_UNSPECIFIED: WorkspacePhase
WORKSPACE_PHASE_ACTIVE: WorkspacePhase
WORKSPACE_PHASE_TERMINATING: WorkspacePhase

class WorkspaceSelector(_message.Message):
    __slots__ = ("workspace", "all_workspaces")
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    ALL_WORKSPACES_FIELD_NUMBER: _ClassVar[int]
    workspace: str
    all_workspaces: AllWorkspaces
    def __init__(self, workspace: _Optional[str] = ..., all_workspaces: _Optional[_Union[AllWorkspaces, _Mapping]] = ...) -> None: ...

class AllWorkspaces(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ObjectMeta(_message.Message):
    __slots__ = ("id", "name", "created_time", "labels", "resource_version", "annotations", "workspace", "deletion_time")
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
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CREATED_TIME_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    RESOURCE_VERSION_FIELD_NUMBER: _ClassVar[int]
    ANNOTATIONS_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    DELETION_TIME_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    created_time: _timestamp_pb2.Timestamp
    labels: _containers.ScalarMap[str, str]
    resource_version: int
    annotations: _containers.ScalarMap[str, str]
    workspace: str
    deletion_time: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., created_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., labels: _Optional[_Mapping[str, str]] = ..., resource_version: _Optional[int] = ..., annotations: _Optional[_Mapping[str, str]] = ..., workspace: _Optional[str] = ..., deletion_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class WorkspaceStatus(_message.Message):
    __slots__ = ("phase",)
    PHASE_FIELD_NUMBER: _ClassVar[int]
    phase: WorkspacePhase
    def __init__(self, phase: _Optional[_Union[WorkspacePhase, str]] = ...) -> None: ...

class Workspace(_message.Message):
    __slots__ = ("metadata", "status")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    metadata: ObjectMeta
    status: WorkspaceStatus
    def __init__(self, metadata: _Optional[_Union[ObjectMeta, _Mapping]] = ..., status: _Optional[_Union[WorkspaceStatus, _Mapping]] = ...) -> None: ...

class CredentialHandle(_message.Message):
    __slots__ = ("driver", "handle", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    DRIVER_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    driver: str
    handle: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, driver: _Optional[str] = ..., handle: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class Provider(_message.Message):
    __slots__ = ("metadata", "type", "credentials", "config", "credential_expiration_times", "profile_workspace", "credential_handles")
    class CredentialsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    class ConfigEntry(_message.Message):
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
    class CredentialHandlesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: CredentialHandle
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[CredentialHandle, _Mapping]] = ...) -> None: ...
    METADATA_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_EXPIRATION_TIMES_FIELD_NUMBER: _ClassVar[int]
    PROFILE_WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_HANDLES_FIELD_NUMBER: _ClassVar[int]
    metadata: ObjectMeta
    type: str
    credentials: _containers.ScalarMap[str, str]
    config: _containers.ScalarMap[str, str]
    credential_expiration_times: _containers.MessageMap[str, _timestamp_pb2.Timestamp]
    profile_workspace: str
    credential_handles: _containers.MessageMap[str, CredentialHandle]
    def __init__(self, metadata: _Optional[_Union[ObjectMeta, _Mapping]] = ..., type: _Optional[str] = ..., credentials: _Optional[_Mapping[str, str]] = ..., config: _Optional[_Mapping[str, str]] = ..., credential_expiration_times: _Optional[_Mapping[str, _timestamp_pb2.Timestamp]] = ..., profile_workspace: _Optional[str] = ..., credential_handles: _Optional[_Mapping[str, CredentialHandle]] = ...) -> None: ...

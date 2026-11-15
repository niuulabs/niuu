import datetime

from . import datamodel_pb2 as _datamodel_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from . import extension_pb2 as _extension_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetCredentialDriverCapabilitiesRequest(_message.Message):
    __slots__ = ("gateway",)
    GATEWAY_FIELD_NUMBER: _ClassVar[int]
    gateway: _extension_pb2.PeerMetadata
    def __init__(self, gateway: _Optional[_Union[_extension_pb2.PeerMetadata, _Mapping]] = ...) -> None: ...

class GetCredentialDriverCapabilitiesResponse(_message.Message):
    __slots__ = ("driver_name", "driver_version", "backend_kind", "supports_list", "supports_expires_at", "extension")
    DRIVER_NAME_FIELD_NUMBER: _ClassVar[int]
    DRIVER_VERSION_FIELD_NUMBER: _ClassVar[int]
    BACKEND_KIND_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_LIST_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_FIELD_NUMBER: _ClassVar[int]
    driver_name: str
    driver_version: str
    backend_kind: str
    supports_list: bool
    supports_expires_at: bool
    extension: _extension_pb2.PeerMetadata
    def __init__(self, driver_name: _Optional[str] = ..., driver_version: _Optional[str] = ..., backend_kind: _Optional[str] = ..., supports_list: bool = ..., supports_expires_at: bool = ..., extension: _Optional[_Union[_extension_pb2.PeerMetadata, _Mapping]] = ...) -> None: ...

class StoreCredentialRequest(_message.Message):
    __slots__ = ("provider", "credential_key", "value", "existing_handle", "workspace", "provider_id", "object_id")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    EXISTING_HANDLE_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    provider: str
    credential_key: str
    value: str
    existing_handle: _datamodel_pb2.CredentialHandle
    workspace: str
    provider_id: str
    object_id: str
    def __init__(self, provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., value: _Optional[str] = ..., existing_handle: _Optional[_Union[_datamodel_pb2.CredentialHandle, _Mapping]] = ..., workspace: _Optional[str] = ..., provider_id: _Optional[str] = ..., object_id: _Optional[str] = ...) -> None: ...

class StoreCredentialResponse(_message.Message):
    __slots__ = ("handle",)
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    handle: _datamodel_pb2.CredentialHandle
    def __init__(self, handle: _Optional[_Union[_datamodel_pb2.CredentialHandle, _Mapping]] = ...) -> None: ...

class DeleteCredentialRequest(_message.Message):
    __slots__ = ("provider", "credential_key", "handle", "workspace", "provider_id")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    provider: str
    credential_key: str
    handle: _datamodel_pb2.CredentialHandle
    workspace: str
    provider_id: str
    def __init__(self, provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., handle: _Optional[_Union[_datamodel_pb2.CredentialHandle, _Mapping]] = ..., workspace: _Optional[str] = ..., provider_id: _Optional[str] = ...) -> None: ...

class DeleteCredentialResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ResolveCredentialsRequest(_message.Message):
    __slots__ = ("credentials",)
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.RepeatedCompositeFieldContainer[ResolveCredentialRequest]
    def __init__(self, credentials: _Optional[_Iterable[_Union[ResolveCredentialRequest, _Mapping]]] = ...) -> None: ...

class ResolveCredentialRequest(_message.Message):
    __slots__ = ("request_id", "provider", "credential_key", "handle", "workspace", "provider_id")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KEY_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    WORKSPACE_FIELD_NUMBER: _ClassVar[int]
    PROVIDER_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    provider: str
    credential_key: str
    handle: _datamodel_pb2.CredentialHandle
    workspace: str
    provider_id: str
    def __init__(self, request_id: _Optional[str] = ..., provider: _Optional[str] = ..., credential_key: _Optional[str] = ..., handle: _Optional[_Union[_datamodel_pb2.CredentialHandle, _Mapping]] = ..., workspace: _Optional[str] = ..., provider_id: _Optional[str] = ...) -> None: ...

class ResolveCredentialsResponse(_message.Message):
    __slots__ = ("credentials",)
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.RepeatedCompositeFieldContainer[ResolvedCredential]
    def __init__(self, credentials: _Optional[_Iterable[_Union[ResolvedCredential, _Mapping]]] = ...) -> None: ...

class ResolvedCredential(_message.Message):
    __slots__ = ("request_id", "value", "expiration_time")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_TIME_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    value: str
    expiration_time: _timestamp_pb2.Timestamp
    def __init__(self, request_id: _Optional[str] = ..., value: _Optional[str] = ..., expiration_time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListCredentialsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListCredentialsResponse(_message.Message):
    __slots__ = ("credentials",)
    CREDENTIALS_FIELD_NUMBER: _ClassVar[int]
    credentials: _containers.RepeatedCompositeFieldContainer[ListedCredential]
    def __init__(self, credentials: _Optional[_Iterable[_Union[ListedCredential, _Mapping]]] = ...) -> None: ...

class ListedCredential(_message.Message):
    __slots__ = ("handle", "keys", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    KEYS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    handle: str
    keys: _containers.RepeatedScalarFieldContainer[str]
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, handle: _Optional[str] = ..., keys: _Optional[_Iterable[str]] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

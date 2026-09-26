import datetime

from envoy.config.core.v3 import address_pb2 as _address_pb2
from envoy.config.core.v3 import base_pb2 as _base_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from udpa.annotations import migrate_pb2 as _migrate_pb2
from udpa.annotations import status_pb2 as _status_pb2
from udpa.annotations import versioning_pb2 as _versioning_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AttributeContext(_message.Message):
    __slots__ = ("source", "destination", "request", "context_extensions", "metadata_context", "route_metadata_context", "tls_session")
    class Peer(_message.Message):
        __slots__ = ("address", "service", "labels", "principal", "certificate")
        class LabelsEntry(_message.Message):
            __slots__ = ("key", "value")
            KEY_FIELD_NUMBER: _ClassVar[int]
            VALUE_FIELD_NUMBER: _ClassVar[int]
            key: str
            value: str
            def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
        ADDRESS_FIELD_NUMBER: _ClassVar[int]
        SERVICE_FIELD_NUMBER: _ClassVar[int]
        LABELS_FIELD_NUMBER: _ClassVar[int]
        PRINCIPAL_FIELD_NUMBER: _ClassVar[int]
        CERTIFICATE_FIELD_NUMBER: _ClassVar[int]
        address: _address_pb2.Address
        service: str
        labels: _containers.ScalarMap[str, str]
        principal: str
        certificate: str
        def __init__(self, address: _Optional[_Union[_address_pb2.Address, _Mapping]] = ..., service: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., principal: _Optional[str] = ..., certificate: _Optional[str] = ...) -> None: ...
    class Request(_message.Message):
        __slots__ = ("time", "http")
        TIME_FIELD_NUMBER: _ClassVar[int]
        HTTP_FIELD_NUMBER: _ClassVar[int]
        time: _timestamp_pb2.Timestamp
        http: AttributeContext.HttpRequest
        def __init__(self, time: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., http: _Optional[_Union[AttributeContext.HttpRequest, _Mapping]] = ...) -> None: ...
    class HttpRequest(_message.Message):
        __slots__ = ("id", "method", "headers", "header_map", "path", "host", "scheme", "query", "fragment", "size", "protocol", "body", "raw_body")
        class HeadersEntry(_message.Message):
            __slots__ = ("key", "value")
            KEY_FIELD_NUMBER: _ClassVar[int]
            VALUE_FIELD_NUMBER: _ClassVar[int]
            key: str
            value: str
            def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
        ID_FIELD_NUMBER: _ClassVar[int]
        METHOD_FIELD_NUMBER: _ClassVar[int]
        HEADERS_FIELD_NUMBER: _ClassVar[int]
        HEADER_MAP_FIELD_NUMBER: _ClassVar[int]
        PATH_FIELD_NUMBER: _ClassVar[int]
        HOST_FIELD_NUMBER: _ClassVar[int]
        SCHEME_FIELD_NUMBER: _ClassVar[int]
        QUERY_FIELD_NUMBER: _ClassVar[int]
        FRAGMENT_FIELD_NUMBER: _ClassVar[int]
        SIZE_FIELD_NUMBER: _ClassVar[int]
        PROTOCOL_FIELD_NUMBER: _ClassVar[int]
        BODY_FIELD_NUMBER: _ClassVar[int]
        RAW_BODY_FIELD_NUMBER: _ClassVar[int]
        id: str
        method: str
        headers: _containers.ScalarMap[str, str]
        header_map: _base_pb2.HeaderMap
        path: str
        host: str
        scheme: str
        query: str
        fragment: str
        size: int
        protocol: str
        body: str
        raw_body: bytes
        def __init__(self, id: _Optional[str] = ..., method: _Optional[str] = ..., headers: _Optional[_Mapping[str, str]] = ..., header_map: _Optional[_Union[_base_pb2.HeaderMap, _Mapping]] = ..., path: _Optional[str] = ..., host: _Optional[str] = ..., scheme: _Optional[str] = ..., query: _Optional[str] = ..., fragment: _Optional[str] = ..., size: _Optional[int] = ..., protocol: _Optional[str] = ..., body: _Optional[str] = ..., raw_body: _Optional[bytes] = ...) -> None: ...
    class TLSSession(_message.Message):
        __slots__ = ("sni",)
        SNI_FIELD_NUMBER: _ClassVar[int]
        sni: str
        def __init__(self, sni: _Optional[str] = ...) -> None: ...
    class ContextExtensionsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_EXTENSIONS_FIELD_NUMBER: _ClassVar[int]
    METADATA_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ROUTE_METADATA_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    TLS_SESSION_FIELD_NUMBER: _ClassVar[int]
    source: AttributeContext.Peer
    destination: AttributeContext.Peer
    request: AttributeContext.Request
    context_extensions: _containers.ScalarMap[str, str]
    metadata_context: _base_pb2.Metadata
    route_metadata_context: _base_pb2.Metadata
    tls_session: AttributeContext.TLSSession
    def __init__(self, source: _Optional[_Union[AttributeContext.Peer, _Mapping]] = ..., destination: _Optional[_Union[AttributeContext.Peer, _Mapping]] = ..., request: _Optional[_Union[AttributeContext.Request, _Mapping]] = ..., context_extensions: _Optional[_Mapping[str, str]] = ..., metadata_context: _Optional[_Union[_base_pb2.Metadata, _Mapping]] = ..., route_metadata_context: _Optional[_Union[_base_pb2.Metadata, _Mapping]] = ..., tls_session: _Optional[_Union[AttributeContext.TLSSession, _Mapping]] = ...) -> None: ...

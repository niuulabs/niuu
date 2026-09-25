from envoy.config.core.v3 import base_pb2 as _base_pb2
from envoy.service.auth.v3 import attribute_context_pb2 as _attribute_context_pb2
from envoy.type.v3 import http_status_pb2 as _http_status_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.rpc import status_pb2 as _status_pb2
from envoy.annotations import deprecation_pb2 as _deprecation_pb2
from udpa.annotations import status_pb2 as _status_pb2_1
from udpa.annotations import versioning_pb2 as _versioning_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CheckRequest(_message.Message):
    __slots__ = ("attributes",)
    ATTRIBUTES_FIELD_NUMBER: _ClassVar[int]
    attributes: _attribute_context_pb2.AttributeContext
    def __init__(self, attributes: _Optional[_Union[_attribute_context_pb2.AttributeContext, _Mapping]] = ...) -> None: ...

class DeniedHttpResponse(_message.Message):
    __slots__ = ("status", "headers", "body")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    HEADERS_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    status: _http_status_pb2.HttpStatus
    headers: _containers.RepeatedCompositeFieldContainer[_base_pb2.HeaderValueOption]
    body: str
    def __init__(self, status: _Optional[_Union[_http_status_pb2.HttpStatus, _Mapping]] = ..., headers: _Optional[_Iterable[_Union[_base_pb2.HeaderValueOption, _Mapping]]] = ..., body: _Optional[str] = ...) -> None: ...

class OkHttpResponse(_message.Message):
    __slots__ = ("headers", "headers_to_remove", "dynamic_metadata", "response_headers_to_add", "query_parameters_to_set", "query_parameters_to_remove")
    HEADERS_FIELD_NUMBER: _ClassVar[int]
    HEADERS_TO_REMOVE_FIELD_NUMBER: _ClassVar[int]
    DYNAMIC_METADATA_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_HEADERS_TO_ADD_FIELD_NUMBER: _ClassVar[int]
    QUERY_PARAMETERS_TO_SET_FIELD_NUMBER: _ClassVar[int]
    QUERY_PARAMETERS_TO_REMOVE_FIELD_NUMBER: _ClassVar[int]
    headers: _containers.RepeatedCompositeFieldContainer[_base_pb2.HeaderValueOption]
    headers_to_remove: _containers.RepeatedScalarFieldContainer[str]
    dynamic_metadata: _struct_pb2.Struct
    response_headers_to_add: _containers.RepeatedCompositeFieldContainer[_base_pb2.HeaderValueOption]
    query_parameters_to_set: _containers.RepeatedCompositeFieldContainer[_base_pb2.QueryParameter]
    query_parameters_to_remove: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, headers: _Optional[_Iterable[_Union[_base_pb2.HeaderValueOption, _Mapping]]] = ..., headers_to_remove: _Optional[_Iterable[str]] = ..., dynamic_metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., response_headers_to_add: _Optional[_Iterable[_Union[_base_pb2.HeaderValueOption, _Mapping]]] = ..., query_parameters_to_set: _Optional[_Iterable[_Union[_base_pb2.QueryParameter, _Mapping]]] = ..., query_parameters_to_remove: _Optional[_Iterable[str]] = ...) -> None: ...

class CheckResponse(_message.Message):
    __slots__ = ("status", "denied_response", "ok_response", "error_response", "dynamic_metadata")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DENIED_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    OK_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    ERROR_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    DYNAMIC_METADATA_FIELD_NUMBER: _ClassVar[int]
    status: _status_pb2.Status
    denied_response: DeniedHttpResponse
    ok_response: OkHttpResponse
    error_response: DeniedHttpResponse
    dynamic_metadata: _struct_pb2.Struct
    def __init__(self, status: _Optional[_Union[_status_pb2.Status, _Mapping]] = ..., denied_response: _Optional[_Union[DeniedHttpResponse, _Mapping]] = ..., ok_response: _Optional[_Union[OkHttpResponse, _Mapping]] = ..., error_response: _Optional[_Union[DeniedHttpResponse, _Mapping]] = ..., dynamic_metadata: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

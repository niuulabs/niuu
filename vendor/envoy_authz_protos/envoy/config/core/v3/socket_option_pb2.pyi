from udpa.annotations import status_pb2 as _status_pb2
from udpa.annotations import versioning_pb2 as _versioning_pb2
from validate import validate_pb2 as _validate_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SocketOption(_message.Message):
    __slots__ = ("description", "level", "name", "int_value", "buf_value", "state", "type", "ip_version")
    class SocketState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATE_PREBIND: _ClassVar[SocketOption.SocketState]
        STATE_BOUND: _ClassVar[SocketOption.SocketState]
        STATE_LISTENING: _ClassVar[SocketOption.SocketState]
    STATE_PREBIND: SocketOption.SocketState
    STATE_BOUND: SocketOption.SocketState
    STATE_LISTENING: SocketOption.SocketState
    class SocketIpVersion(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        SOCKET_IP_VERSION_UNSPECIFIED: _ClassVar[SocketOption.SocketIpVersion]
        SOCKET_IP_VERSION_IPV4: _ClassVar[SocketOption.SocketIpVersion]
        SOCKET_IP_VERSION_IPV6: _ClassVar[SocketOption.SocketIpVersion]
    SOCKET_IP_VERSION_UNSPECIFIED: SocketOption.SocketIpVersion
    SOCKET_IP_VERSION_IPV4: SocketOption.SocketIpVersion
    SOCKET_IP_VERSION_IPV6: SocketOption.SocketIpVersion
    class SocketType(_message.Message):
        __slots__ = ("stream", "datagram")
        class Stream(_message.Message):
            __slots__ = ()
            def __init__(self) -> None: ...
        class Datagram(_message.Message):
            __slots__ = ()
            def __init__(self) -> None: ...
        STREAM_FIELD_NUMBER: _ClassVar[int]
        DATAGRAM_FIELD_NUMBER: _ClassVar[int]
        stream: SocketOption.SocketType.Stream
        datagram: SocketOption.SocketType.Datagram
        def __init__(self, stream: _Optional[_Union[SocketOption.SocketType.Stream, _Mapping]] = ..., datagram: _Optional[_Union[SocketOption.SocketType.Datagram, _Mapping]] = ...) -> None: ...
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    INT_VALUE_FIELD_NUMBER: _ClassVar[int]
    BUF_VALUE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    IP_VERSION_FIELD_NUMBER: _ClassVar[int]
    description: str
    level: int
    name: int
    int_value: int
    buf_value: bytes
    state: SocketOption.SocketState
    type: SocketOption.SocketType
    ip_version: SocketOption.SocketIpVersion
    def __init__(self, description: _Optional[str] = ..., level: _Optional[int] = ..., name: _Optional[int] = ..., int_value: _Optional[int] = ..., buf_value: _Optional[bytes] = ..., state: _Optional[_Union[SocketOption.SocketState, str]] = ..., type: _Optional[_Union[SocketOption.SocketType, _Mapping]] = ..., ip_version: _Optional[_Union[SocketOption.SocketIpVersion, str]] = ...) -> None: ...

class SocketOptionsOverride(_message.Message):
    __slots__ = ("socket_options",)
    SOCKET_OPTIONS_FIELD_NUMBER: _ClassVar[int]
    socket_options: _containers.RepeatedCompositeFieldContainer[SocketOption]
    def __init__(self, socket_options: _Optional[_Iterable[_Union[SocketOption, _Mapping]]] = ...) -> None: ...

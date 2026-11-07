"""Forge's own TLS listener for joining hosts, trusted by a pin instead of a CA.

A local Forge (``niuu platform up``) serves its API over plain http and has no CA, but a
hijacked join would give someone root on the host. So the two routes a joining host calls
get a listener of their own, with a self-signed certificate whose public key the join
command pins (``curl -k --pinnedpubkey sha256//…``): the operator copies the pin from the
Forge's UI together with the code, and curl refuses any other server before it sends the
code. The key and certificate are generated once (mode 0600), never overwritten, and
reused across restarts, so a pin stays valid for as long as the key file exists.

See ``docs/design/bm-compute-hosts.md`` and ``docs/operator/compute-hosts.md``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import socket
import ssl
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from cryptography.x509.oid import NameOID
from fastapi import FastAPI

from niuu.adapters.workload_identity.key_file import (
    SigningKeyFileError,
    load_or_create_rsa_key_pem,
    read_private_file,
    write_private_file_once,
)
from niuu.observability import get_observability, install_uvicorn_log_redaction
from volundr.adapters.inbound.rest_compute_hosts import create_host_join_router
from volundr.domain.services.compute_hosts import ComputeHostService

logger = logging.getLogger(__name__)

_CERT = "join listener certificate"
_COMMON_NAME = "niuu-forge-host-join"
#: RFC 5280 4.1.2.5: "no well-defined expiration date". curl -k checks only the pin.
_NO_EXPIRY = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)


class HostJoinListenerError(RuntimeError):
    """The configured join listener cannot load its key or listen. Fatal at startup."""


@dataclass(frozen=True)
class JoinTls:
    cert_file: Path
    key_file: Path
    #: ``curl --pinnedpubkey`` form: ``sha256//`` and the base64 SHA-256 of the SPKI.
    pin: str


def spki_pin(certificate: x509.Certificate) -> str:
    spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()


def load_or_create_join_tls(*, cert_file: str, key_file: str, key_bits: int) -> JoinTls:
    """The listener's key and certificate, generated once and verified to belong together."""
    key_path = Path(key_file).expanduser()
    cert_path = Path(cert_file).expanduser()
    remedy = "fix or delete it (hosts must then be given the new pin)"
    try:
        pem = load_or_create_rsa_key_pem(key_path, key_size=key_bits)
        # load_or_create_rsa_key_pem refuses anything but an RSA key.
        key = cast(RSAPrivateKey, load_pem_private_key(pem.encode(), password=None))
        if not cert_path.exists():
            write_private_file_once(cert_path, _self_signed(key), what=_CERT)
        certificate = x509.load_pem_x509_certificate(read_private_file(cert_path, what=_CERT))
    except SigningKeyFileError as exc:
        raise HostJoinListenerError(
            f"compute.hosts.join_listener has no usable key and certificate: {exc}"
        ) from exc
    except ValueError as exc:
        raise HostJoinListenerError(
            f"compute.hosts.join_listener.cert_file {cert_path} is not a PEM certificate; {remedy}"
        ) from exc
    if certificate.public_key().public_numbers() != key.public_key().public_numbers():
        raise HostJoinListenerError(
            f"compute.hosts.join_listener.cert_file {cert_path} does not belong to key_file "
            f"{key_path}; delete the certificate to have one made from the key (the pin "
            "stays the same)"
        )
    return JoinTls(cert_file=cert_path, key_file=key_path, pin=spki_pin(certificate))


def _self_signed(key: RSAPrivateKey) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _COMMON_NAME)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(_NO_EXPIRY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(Encoding.PEM)


def create_host_join_app(service: ComputeHostService) -> FastAPI:
    """An app with the two host-facing routes and nothing else: no docs, no admin routes."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_host_join_router(service))
    return app


class _EmbeddedServer(uvicorn.Server):
    """A server inside Forge's process: the process's own server keeps the signals."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class HostJoinListener:
    def __init__(self, app: FastAPI, *, bind_host: str, port: int, tls: JoinTls) -> None:
        self._app = app
        self._bind_host = bind_host
        self._port = port
        self._tls = tls
        self._server: _EmbeddedServer | None = None
        self._task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self._stopping = False

    @property
    def port(self) -> int:
        """The bound port; the configured one, or the kernel's choice for port 0."""
        return self._port

    async def start(self) -> None:
        # The join routes carry pairing codes in their Authorization header.
        install_uvicorn_log_redaction()
        config = uvicorn.Config(
            self._app,
            ssl_certfile=str(self._tls.cert_file),
            ssl_keyfile=str(self._tls.key_file),
            lifespan="off",
            log_level="warning",
            access_log=False,
        )
        try:
            config.load()
        except (OSError, ssl.SSLError) as exc:
            raise HostJoinListenerError(
                f"compute.hosts.join_listener cannot load {self._tls.cert_file} and "
                f"{self._tls.key_file}: {exc}"
            ) from exc
        family = socket.AF_INET6 if ":" in self._bind_host else socket.AF_INET
        try:
            sock = socket.create_server((self._bind_host, self._port), family=family)
        except OSError as exc:
            raise HostJoinListenerError(
                f"compute.hosts.join_listener cannot listen on {self._bind_host}:{self._port}: "
                f"{exc}; free the port, or change compute.hosts.join_listener.port or bind_host"
            ) from exc
        self._port = sock.getsockname()[1]
        server = _EmbeddedServer(config)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():
                sock.close()
                raise HostJoinListenerError(
                    f"compute.hosts.join_listener failed to start on "
                    f"{self._bind_host}:{self._port}: {task.exception()}"
                )
            await asyncio.sleep(0)
        task.add_done_callback(self._ended)
        self._server, self._task, self._socket = server, task, sock
        logger.info(
            "Host join listener on https://%s:%d, pinned %s",
            self._bind_host,
            self._port,
            self._tls.pin,
        )

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self._stopping = True
        self._server.should_exit = True
        await asyncio.gather(self._task, return_exceptions=True)
        self._server = self._task = None

    def _ended(self, task: asyncio.Task[None]) -> None:
        # uvicorn closes the socket on a clean shutdown, not when its task is cancelled.
        if self._socket is not None:
            self._socket.close()
        if self._stopping:
            return
        # Hosts can no longer join; Forge itself keeps serving. Loud, never quiet.
        reason = "cancelled" if task.cancelled() else repr(task.exception())
        logger.error(
            "Host join listener on %s:%d stopped (%s); hosts cannot join until Forge restarts",
            self._bind_host,
            self._port,
            reason,
        )
        get_observability().count("volundr.compute.hosts.join_listener_stopped")

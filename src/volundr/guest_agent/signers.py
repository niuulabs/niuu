"""Proof of identity for the metadata request: an Ed25519 signature or a JWT-SVID."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_ssh_private_key

from volundr.guest_agent.config import HostKeyAuth, SpiffeAuth
from volundr.guest_agent.errors import SignerError

HEADER_ALLOCATION = "Niuu-Guest-Allocation"
HEADER_TIMESTAMP = "Niuu-Guest-Timestamp"
HEADER_SIGNATURE = "Niuu-Guest-Signature"

_MS_PER_SECOND = 1000


def signing_input(method: str, path: str, timestamp_ms: int, body: bytes) -> bytes:
    """The exact bytes a host-key request signs; Forge rebuilds them to verify."""
    digest = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{timestamp_ms}\n{digest}".encode()


class RequestSigner(ABC):
    """Produces the authentication headers for one outgoing request."""

    @abstractmethod
    async def headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
        """Headers proving the request; called once per request, never cached."""


class HostKeySigner(RequestSigner):
    """Signs with the guest's OpenSSH Ed25519 host key (loaded once, at startup)."""

    def __init__(
        self,
        *,
        key_path: str,
        allocation_id: str,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._allocation_id = allocation_id
        self._clock_ms = clock_ms or _wall_clock_ms
        self._last_timestamp_ms = 0
        self._key = _load_host_key(key_path)

    async def headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
        # Forge requires the timestamp to strictly increase per allocation, so two
        # requests inside one millisecond must still carry different values.
        timestamp_ms = max(self._clock_ms(), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        signature = self._key.sign(signing_input(method, path, timestamp_ms, body))
        return {
            HEADER_ALLOCATION: self._allocation_id,
            HEADER_TIMESTAMP: str(timestamp_ms),
            HEADER_SIGNATURE: base64.b64encode(signature).decode("ascii"),
        }


class SpiffeSigner(RequestSigner):
    """Fetches a fresh JWT-SVID from the SPIRE agent for every request."""

    def __init__(
        self,
        *,
        audience: str,
        socket_path: str,
        spire_agent_binary: str,
        timeout_seconds: float,
    ) -> None:
        self._audience = audience
        self._socket_path = socket_path
        self._binary = spire_agent_binary
        self._timeout = timeout_seconds

    async def headers(self, *, method: str, path: str, body: bytes) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._fetch_svid()}"}

    async def _fetch_svid(self) -> str:
        command = [
            self._binary,
            "api",
            "fetch",
            "jwt",
            "-audience",
            self._audience,
            "-socketPath",
            self._socket_path,
            "-output",
            "json",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SignerError(
                f"cannot run auth.spire_agent_binary {self._binary!r}: {exc}"
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self._timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise SignerError(
                f"spire-agent api fetch jwt did not answer within request_timeout_seconds="
                f"{self._timeout}; is the SPIRE agent running on auth.socket_path "
                f"{self._socket_path!r}?"
            ) from exc
        if process.returncode != 0:
            raise SignerError(
                f"spire-agent api fetch jwt exited with status {process.returncode}: "
                f"{_summarize_failure(stderr, stdout)}"
            )
        return parse_jwt_svid_output(stdout)


def build_signer(
    auth: HostKeyAuth | SpiffeAuth, *, allocation_id: str, timeout_seconds: float
) -> RequestSigner:
    if isinstance(auth, HostKeyAuth):
        return HostKeySigner(key_path=auth.key_path, allocation_id=allocation_id)
    return SpiffeSigner(
        audience=auth.audience,
        socket_path=auth.socket_path,
        spire_agent_binary=auth.spire_agent_binary,
        timeout_seconds=timeout_seconds,
    )


def parse_jwt_svid_output(stdout: bytes) -> str:
    """Extract the JWT-SVID from ``spire-agent api fetch jwt -output json``.

    SPIRE prints one JSON document: the ``JWTSVIDResponse`` and the
    ``JWTBundlesResponse`` it was printed with, as a two-element array
    (``cmd/spire-agent/cli/api/fetch_jwt.go`` calls ``PrintProto(svidResp,
    bundlesResp)``; ``cliprinter/internal/protojson`` prints a single message as
    an object and several as an array, with proto field names)::

        [{"svids": [{"spiffe_id": "spiffe://...", "svid": "<jwt>", "hint": ""}]},
         {"bundles": {"spiffe://<trust domain>": "<jwks base64>"}}]

    The first SVID is the workload's default identity. A failure printed as
    ``{"error": "..."}`` is reported by its message. Nothing in the output is
    ever echoed back into an error, because it may hold the token.
    """
    try:
        document = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SignerError("spire-agent api fetch jwt printed output that is not JSON") from exc
    messages = document if isinstance(document, list) else [document]
    for message in messages:
        if isinstance(message, dict) and isinstance(message.get("error"), str):
            raise SignerError(f"spire-agent api fetch jwt reported: {message['error']}")
    for message in messages:
        if not isinstance(message, dict) or "svids" not in message:
            continue
        svids = message["svids"]
        if not isinstance(svids, list) or not svids:
            raise SignerError(
                "spire-agent api fetch jwt returned no JWT-SVID; is this workload registered "
                "with the SPIRE server for the configured audience?"
            )
        first = svids[0]
        token = first.get("svid") if isinstance(first, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise SignerError("spire-agent api fetch jwt returned an empty JWT-SVID")
        return token.strip()
    raise SignerError(
        "spire-agent api fetch jwt printed JSON without an 'svids' list; the SPIRE agent "
        "version may not match what the guest agent expects"
    )


def _summarize_failure(stderr: bytes, stdout: bytes) -> str:
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines()]
    lines = [line for line in lines if line]
    if lines:
        return lines[-1]
    try:
        document = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "no error output"
    for message in document if isinstance(document, list) else [document]:
        if isinstance(message, dict) and isinstance(message.get("error"), str):
            return message["error"]
    return "no error output"


def _load_host_key(key_path: str) -> Ed25519PrivateKey:
    try:
        data = Path(key_path).read_bytes()
    except OSError as exc:
        raise SignerError(f"cannot read auth.key_path {key_path}: {exc}") from exc
    try:
        key = load_ssh_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise SignerError(
            f"auth.key_path {key_path} is not an unencrypted OpenSSH private key: {exc}"
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SignerError(f"auth.key_path {key_path} is a {type(key).__name__}, not an Ed25519 key")
    return key


def _wall_clock_ms() -> int:
    return int(time.time() * _MS_PER_SECOND)

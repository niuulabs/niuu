"""Who sent a mesh message: a certificate from Forge and a signature from the sender.

The mesh transports authenticate a connection, not a sender: any member of a realm can
put any peer id, session id or reply route into what it sends. This module makes the
sender provable on **every** transport (Sleipnir over nng, NATS or RabbitMQ, the webhook
adapter, in-process), because the proof travels inside the message:

* **A certificate** (:class:`MeshCertificate`): a short-lived ``mesh_peer`` JWT that Forge
  signs with its workload-identity key. It binds a mesh peer id, a session and launch,
  the memberships (flocks, realms, tenant) the peer belongs to, and the peer's own
  Ed25519 public key. Forge sets all of it; the peer only names its key and peer id.
* **A signature**: the peer signs what it sends with the matching private key, which is
  generated in process memory at start and never leaves it.

A receiver checks the certificate offline against Forge's JWKS (no Forge round trip per
message), then the signature, then that what the message claims about its sender
(session id, peer id, reply route, ``source``) is what the certificate says. Anything it
cannot verify is refused with a reason, never accepted.

The certificate is addressed to the mesh (audience ``niuu-mesh``), has no human subject
and no scopes, and no Forge route admits it (``token_use = "mesh_peer"``), so a peer that
receives one cannot replay it against Forge. It is worthless without the private key.

Replay: a signature covers the recipient, a signing time and the message, and a
receiver refuses one outside the clock-skew window. Within the window a captured request
repeats only what the sender really sent; the inbox already refuses a duplicate
``message_id``. Events carry their ``event_id`` and are checked against a maximum age.
Revocation: a certificate lives for ``ttl`` and is renewed at half of it; Forge also
publishes a signed "session stopped" notice that receivers apply at once
(:meth:`MeshAttestationVerifier.revoke`).
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import json
import logging
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from jwt import PyJWKSet
from jwt.exceptions import PyJWKSetError

from niuu.cap_telemetry import record_degraded, record_retry
from niuu.domain.services.token_scope import MESH_PEER_TOKEN_USE
from niuu.observability import get_observability

logger = logging.getLogger(__name__)

#: Audience of a mesh certificate. Forge's and Envoy's audiences never include it.
MESH_AUDIENCE = "niuu-mesh"
ATTESTATION_VERSION = 1
_DOMAIN = b"niuu-mesh-attest-v1\n"

#: ``kind`` of a certificate held by a session's Skuld.
KIND_SESSION = "session"
#: ``kind`` of a certificate held by a Forge-hosted resident peer.
KIND_RESIDENT = "resident"
#: ``kind`` of Forge's own certificate: the only holder that may publish revocations.
KIND_AUTHORITY = "authority"

PURPOSE_RPC = "rpc"
PURPOSE_RPC_REPLY = "rpc_reply"
PURPOSE_EVENT = "event"
PURPOSE_REVOCATION = "revocation"

COUNTER_REFUSALS = "niuu.mesh.attestation.refusals"
COUNTER_REVOCATIONS = "niuu.mesh.attestation.revocations"

#: Claims a certificate carries under the issuer's ``workload_`` prefix.
_CLAIM_PEER = "workload_peer_id"
_CLAIM_KIND = "workload_kind"
_CLAIM_SESSION = "workload_session_id"
_CLAIM_LAUNCH = "workload_launch_id"
_CLAIM_MEMBERSHIPS = "workload_memberships"
_CLAIM_KEY = "workload_key"


class AttestationReason(StrEnum):
    """Why a message from a mesh peer was refused; the counter's ``reason`` attribute."""

    MISSING = "missing"
    MALFORMED = "malformed"
    CERT_INVALID = "cert_invalid"
    CERT_EXPIRED = "cert_expired"
    UNKNOWN_KEY = "unknown_key"
    SIGNATURE_INVALID = "signature_invalid"
    STALE = "stale"
    BINDING_MISMATCH = "binding_mismatch"
    NOT_MEMBER = "not_member"
    REVOKED = "revoked"
    NOT_AUTHORITY = "not_authority"
    TRUST_UNAVAILABLE = "trust_unavailable"


class AttestationRefusedError(Exception):
    """A message whose sender could not be verified. ``reason`` and ``claimed`` are counted."""

    def __init__(self, reason: AttestationReason, message: str, *, claimed: str = "") -> None:
        super().__init__(f"{reason.value}: {message}")
        self.reason = reason
        self.detail = message
        self.claimed = claimed


class AttestationUnavailableError(RuntimeError):
    """This peer cannot sign: it holds no valid certificate. The message names the fix."""


@dataclass(frozen=True)
class MeshCertificate:
    """What Forge certified about one mesh peer (the verified claims of its JWT)."""

    token: str
    peer_id: str
    kind: str
    session_id: str
    launch_id: str
    memberships: tuple[str, ...]
    public_key: bytes
    issued_at: int
    expires_at: int
    key_id: str


@dataclass(frozen=True)
class IssuedMeshCertificate:
    """A certificate with the trust anchors a peer needs to check others' certificates."""

    token: str
    jwks: dict[str, Any]
    issuer: str
    ttl_s: int


class CertificateSource(Protocol):
    """Where a peer gets (and renews) its certificate for a public key."""

    async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate: ...


@dataclass(frozen=True)
class VerifiedSender:
    """A sender whose certificate and signature checked out."""

    peer_id: str
    kind: str
    session_id: str
    launch_id: str
    memberships: tuple[str, ...]
    signed_at_ms: int


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(value: Any) -> str:
    """Stable JSON of *value*: sorted keys, no spaces. Raises for a non-JSON value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    """SHA-256 of the canonical JSON of *value*."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _signing_input(purpose: str, subject: Any, signed_at_ms: int) -> bytes:
    body = canonical(
        {"v": ATTESTATION_VERSION, "purpose": purpose, "at": signed_at_ms, "subject": subject}
    )
    return _DOMAIN + body.encode("utf-8")


def public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def sanitize_topic(topic: str) -> str:
    """Topics as the mesh transports spell them (lowercase, ``-`` as ``_``)."""
    return ".".join(segment.lower().replace("-", "_") for segment in topic.split("."))


def topic_matches(pattern: str, topic: str) -> bool:
    """Whether a subscription *pattern* covers the *topic* an event was signed for."""
    return fnmatch.fnmatchcase(sanitize_topic(topic), sanitize_topic(pattern))


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _Renewal:
    task: asyncio.Task[None] | None = None
    failures: int = 0
    last_error: str = ""


class MeshSigner:
    """Holds a peer's key and certificate, signs what it sends, renews in the background.

    ``start`` generates the Ed25519 key in memory and obtains the first certificate; it
    raises when none can be had, so a mesh that requires attestation never starts
    unsigned. The certificate is renewed at ``renew_fraction`` of its lifetime. A renewal
    that fails is retried every ``retry_fraction`` of the lifetime and counted; if the
    certificate expires before one succeeds, signing raises
    :class:`AttestationUnavailableError` instead of sending something unverifiable.
    """

    def __init__(
        self,
        source: CertificateSource,
        peer_id: str,
        *,
        renew_fraction: float = 0.5,
        retry_fraction: float = 0.1,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 0.0 < renew_fraction < 1.0:
            raise ValueError(
                f"mesh.attestation.renew_fraction must be between 0 and 1, got {renew_fraction}"
            )
        if not 0.0 < retry_fraction < 1.0:
            raise ValueError(
                f"mesh.attestation.retry_fraction must be between 0 and 1, got {retry_fraction}"
            )
        self._source = source
        self._peer_id = peer_id
        self._renew_fraction = renew_fraction
        self._retry_fraction = retry_fraction
        self._clock = clock
        self._private_key = Ed25519PrivateKey.generate()
        self._certificate: MeshCertificate | None = None
        self._jwks: dict[str, Any] = {"keys": []}
        self._issuer = ""
        self._ttl_s = 0
        self._renewal = _Renewal()
        self._on_renewed: list[Callable[[], None]] = []

    @property
    def peer_id(self) -> str:
        return self._peer_id

    @property
    def certificate(self) -> MeshCertificate:
        if self._certificate is None:
            raise AttestationUnavailableError(
                "this peer has no mesh certificate yet: start the signer first"
            )
        return self._certificate

    @property
    def jwks(self) -> dict[str, Any]:
        return self._jwks

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def memberships(self) -> tuple[str, ...]:
        return self.certificate.memberships

    def add_renewal_listener(self, listener: Callable[[], None]) -> None:
        self._on_renewed.append(listener)

    async def start(self) -> None:
        """Obtain the first certificate (raises when it cannot) and begin renewing."""
        await self._renew_once()
        self._renewal.task = asyncio.create_task(
            self._renew_loop(), name=f"mesh-cert-renewal-{self._peer_id}"
        )

    async def stop(self) -> None:
        task = self._renewal.task
        self._renewal.task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def _renew_once(self) -> None:
        issued = await self._source.issue(public_key_bytes(self._private_key), self._peer_id)
        verifier = MeshAttestationVerifier(
            lambda: issued.jwks, issuer=issued.issuer, own_memberships=lambda: (), clock=self._clock
        )
        certificate = verifier.read_certificate(issued.token)
        if certificate.public_key != public_key_bytes(self._private_key):
            raise AttestationUnavailableError(
                "Forge issued a mesh certificate for a different key than this peer's"
            )
        if certificate.peer_id != self._peer_id:
            raise AttestationUnavailableError(
                f"Forge issued a mesh certificate for peer {certificate.peer_id!r}, not for "
                f"{self._peer_id!r}"
            )
        self._certificate = certificate
        self._jwks = issued.jwks
        self._issuer = issued.issuer
        self._ttl_s = issued.ttl_s
        for listener in self._on_renewed:
            listener()

    async def _renew_loop(self) -> None:
        while True:
            lifetime = max(self.certificate.expires_at - self.certificate.issued_at, 1)
            if self._renewal.failures:
                wait = lifetime * self._retry_fraction
            else:
                wait = lifetime * self._renew_fraction
            await asyncio.sleep(wait)
            try:
                await self._renew_once()
            except Exception as exc:
                self._renewal.failures += 1
                self._renewal.last_error = str(exc)
                if self._clock() >= self.certificate.expires_at:
                    record_degraded(
                        "mesh.attestation.renew",
                        f"mesh certificate for {self._peer_id} expired and could not be "
                        f"renewed ({exc}); this peer's mesh messages are refused by others "
                        "until Forge issues a new one",
                    )
                    continue
                record_retry(
                    "mesh.attestation.renew",
                    f"renewing the mesh certificate for {self._peer_id} failed: {exc}",
                )
                continue
            self._renewal.failures = 0
            self._renewal.last_error = ""

    def attest(
        self, purpose: str, subject: Any, *, extra: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """The attestation to attach to something this peer sends."""
        certificate = self.certificate
        if self._clock() >= certificate.expires_at:
            raise AttestationUnavailableError(
                f"the mesh certificate of {self._peer_id} expired and renewal has failed "
                f"({self._renewal.last_error or 'no attempt succeeded'}); check that this "
                "session can reach Forge (forge_mcp.api_url) and that its credential is valid"
            )
        signed_at = now_ms()
        signature = self._private_key.sign(_signing_input(purpose, subject, signed_at))
        attestation: dict[str, Any] = {
            "v": ATTESTATION_VERSION,
            "cert": certificate.token,
            "at": signed_at,
            "sig": b64url(signature),
        }
        if extra:
            attestation.update(extra)
        return attestation


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------


@dataclass
class _Revocations:
    """Launches whose session stopped, until the time their certificates cannot matter."""

    until_ms: dict[tuple[str, str], int] = field(default_factory=dict)


class MeshAttestationVerifier:
    """Checks the sender of a mesh message against Forge's keys. Offline, no per-message call.

    ``trust`` returns the current JWKS (the signer's, refreshed at each renewal).
    ``own_memberships`` is the verifying peer's own certified memberships; a sender must
    share at least one. A certificate signed with a key the JWKS does not hold is refused
    as ``unknown_key`` and picked up after the next renewal; it is never fetched per
    message, so a flood of unknown keys cannot become a flood of requests to Forge.
    """

    def __init__(
        self,
        trust: Callable[[], Mapping[str, Any]],
        *,
        issuer: str,
        own_memberships: Callable[[], Collection[str]],
        clock_skew_s: float = 300.0,
        event_max_age_s: float = 3600.0,
        revocation_retain_s: float | Callable[[], float] = 0.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._trust = trust
        self._issuer = issuer
        self._own_memberships = own_memberships
        self._skew_ms = int(clock_skew_s * 1000)
        self._event_max_age_ms = int(event_max_age_s * 1000)
        self._retain = revocation_retain_s
        self._clock = clock
        self._revocations = _Revocations()

    # -- certificates -----------------------------------------------------

    def read_certificate(self, token: str, *, at: float | None = None) -> MeshCertificate:
        """Verify a certificate's signature, issuer, audience and shape; return its claims.

        With ``at`` (seconds), validity is judged at that instant instead of now; an event
        is judged at the time its sender signed it.
        """
        jwks = self._trust()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AttestationRefusedError(
                AttestationReason.MALFORMED, f"certificate is not a JWT: {exc}"
            ) from exc
        kid = header.get("kid")
        try:
            key_set = PyJWKSet.from_dict(dict(jwks))
        except PyJWKSetError as exc:
            raise AttestationRefusedError(
                AttestationReason.TRUST_UNAVAILABLE,
                "this peer holds no usable Forge signing keys (JWKS)",
            ) from exc
        keys = [key for key in key_set.keys if key.key_id == kid]
        if not keys:
            raise AttestationRefusedError(
                AttestationReason.UNKNOWN_KEY,
                f"certificate key id {kid!r} is not one of Forge's keys this peer holds; it "
                "is picked up at the next certificate renewal",
            )
        now = self._clock() if at is None else at
        try:
            claims = jwt.decode(
                token,
                keys[0].key,
                algorithms=["RS256"],
                audience=MESH_AUDIENCE,
                issuer=self._issuer,
                options={
                    "require": ["exp", "iat", "iss", "aud"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise AttestationRefusedError(
                AttestationReason.CERT_INVALID, f"certificate is not valid: {exc}"
            ) from exc
        expires = int(claims["exp"])
        if now >= expires:
            raise AttestationRefusedError(
                AttestationReason.CERT_EXPIRED,
                f"certificate expired at {expires} (epoch seconds)",
                claimed=str(claims.get(_CLAIM_PEER, "")),
            )
        if claims.get("token_use") != MESH_PEER_TOKEN_USE:
            raise AttestationRefusedError(
                AttestationReason.CERT_INVALID, "token is not a mesh peer certificate"
            )
        return _certificate_from(token, claims, str(kid))

    # -- verification ------------------------------------------------------

    def verify(
        self,
        attestation: Any,
        purpose: str,
        subject: Any,
        *,
        claimed: str = "",
        authority: bool = False,
        max_age_ms: int | None = None,
        check_membership: bool = True,
    ) -> VerifiedSender:
        """Return the verified sender of *subject*, or raise :class:`AttestationRefusedError`.

        ``max_age_ms`` replaces the clock-skew freshness window (events use their maximum
        age, judged against the certificate at signing time). ``authority`` requires
        Forge's own certificate.
        """
        if not attestation:
            raise AttestationRefusedError(
                AttestationReason.MISSING,
                "no attestation: the sender did not prove who it is (is its mesh signing on?)",
                claimed=claimed,
            )
        if not isinstance(attestation, Mapping):
            raise AttestationRefusedError(
                AttestationReason.MALFORMED, "attestation is not an object", claimed=claimed
            )
        token = attestation.get("cert")
        signature_text = attestation.get("sig")
        signed_at = attestation.get("at")
        if (
            attestation.get("v") != ATTESTATION_VERSION
            or not isinstance(token, str)
            or not isinstance(signature_text, str)
            or not isinstance(signed_at, int)
            or isinstance(signed_at, bool)
        ):
            raise AttestationRefusedError(
                AttestationReason.MALFORMED,
                f"attestation needs v={ATTESTATION_VERSION}, cert, sig and at",
                claimed=claimed,
            )
        now_ms_value = int(self._clock() * 1000)
        try:
            certificate = self.read_certificate(token, at=signed_at / 1000)
        except AttestationRefusedError as exc:
            if not exc.claimed:
                exc.claimed = claimed
            raise
        who = certificate.peer_id
        try:
            signature = b64url_decode(signature_text)
            Ed25519PublicKey.from_public_bytes(certificate.public_key).verify(
                signature, _signing_input(purpose, subject, signed_at)
            )
        except (ValueError, InvalidSignature) as exc:
            raise AttestationRefusedError(
                AttestationReason.SIGNATURE_INVALID,
                "the signature does not match the certified key",
                claimed=who,
            ) from exc
        self._check_freshness(signed_at, now_ms_value, max_age_ms, claimed=who)
        if signed_at / 1000 < certificate.issued_at - self._skew_ms / 1000:
            raise AttestationRefusedError(
                AttestationReason.STALE,
                "the message was signed before its certificate was issued",
                claimed=who,
            )
        if authority and certificate.kind != KIND_AUTHORITY:
            raise AttestationRefusedError(
                AttestationReason.NOT_AUTHORITY,
                f"only Forge may send this; the sender is a {certificate.kind}",
                claimed=who,
            )
        self._check_revoked(certificate, now_ms_value)
        if check_membership and certificate.kind != KIND_AUTHORITY:
            self._check_membership(certificate)
        return VerifiedSender(
            peer_id=certificate.peer_id,
            kind=certificate.kind,
            session_id=certificate.session_id,
            launch_id=certificate.launch_id,
            memberships=certificate.memberships,
            signed_at_ms=signed_at,
        )

    def _check_freshness(
        self, signed_at: int, now: int, max_age_ms: int | None, *, claimed: str
    ) -> None:
        if signed_at - now > self._skew_ms:
            raise AttestationRefusedError(
                AttestationReason.STALE,
                f"the message is signed {signed_at - now} ms in the future; check clocks "
                "(mesh.attestation.clock_skew_s)",
                claimed=claimed,
            )
        window = self._skew_ms if max_age_ms is None else max_age_ms
        if now - signed_at > window:
            raise AttestationRefusedError(
                AttestationReason.STALE,
                f"the message was signed {now - signed_at} ms ago, past the {window} ms "
                "window (mesh.attestation.clock_skew_s for requests, "
                "mesh.attestation.event_max_age_s for events)",
                claimed=claimed,
            )

    def _check_membership(self, certificate: MeshCertificate) -> None:
        own = set(self._own_memberships())
        if own & set(certificate.memberships):
            return
        raise AttestationRefusedError(
            AttestationReason.NOT_MEMBER,
            f"{certificate.peer_id} shares no flock, realm or tenant with this peer "
            f"(it belongs to {sorted(certificate.memberships)}, this peer to {sorted(own)})",
            claimed=certificate.peer_id,
        )

    # -- revocation ----------------------------------------------------------

    def revoke(self, session_id: str, launch_id: str) -> None:
        """Refuse every message of this launch from now on."""
        retain = self._retain() if callable(self._retain) else self._retain
        until = int(self._clock() * 1000) + int(retain * 1000)
        self._revocations.until_ms[(session_id, launch_id)] = until
        get_observability().count(
            COUNTER_REVOCATIONS,
            attributes={"session_id": session_id},
            description="Mesh senders dropped because their session stopped.",
        )

    def revoked(self) -> dict[tuple[str, str], int]:
        self._expire_revocations()
        return dict(self._revocations.until_ms)

    def _expire_revocations(self) -> None:
        now = int(self._clock() * 1000)
        for key in [k for k, until in self._revocations.until_ms.items() if until <= now]:
            del self._revocations.until_ms[key]

    def _check_revoked(self, certificate: MeshCertificate, now: int) -> None:
        if not certificate.session_id:
            return
        until = self._revocations.until_ms.get((certificate.session_id, certificate.launch_id))
        if until is None or until <= now:
            return
        raise AttestationRefusedError(
            AttestationReason.REVOKED,
            f"session {certificate.session_id} (launch {certificate.launch_id}) was stopped",
            claimed=certificate.peer_id,
        )


def _certificate_from(token: str, claims: Mapping[str, Any], key_id: str) -> MeshCertificate:
    peer_id = claims.get(_CLAIM_PEER)
    kind = claims.get(_CLAIM_KIND)
    memberships = claims.get(_CLAIM_MEMBERSHIPS)
    key_text = claims.get(_CLAIM_KEY)
    if (
        not isinstance(peer_id, str)
        or not peer_id
        or kind not in {KIND_SESSION, KIND_RESIDENT, KIND_AUTHORITY}
        or not isinstance(memberships, list)
        or not all(isinstance(item, str) for item in memberships)
        or not isinstance(key_text, str)
    ):
        raise AttestationRefusedError(
            AttestationReason.CERT_INVALID,
            "certificate lacks a peer id, kind, memberships or key",
        )
    try:
        public_key = b64url_decode(key_text)
        Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError as exc:
        raise AttestationRefusedError(
            AttestationReason.CERT_INVALID,
            "certificate key is not an Ed25519 public key",
            claimed=peer_id,
        ) from exc
    return MeshCertificate(
        token=token,
        peer_id=peer_id,
        kind=str(kind),
        session_id=str(claims.get(_CLAIM_SESSION) or ""),
        launch_id=str(claims.get(_CLAIM_LAUNCH) or ""),
        memberships=tuple(memberships),
        public_key=public_key,
        issued_at=int(claims["iat"]),
        expires_at=int(claims["exp"]),
        key_id=key_id,
    )


__all__ = [
    "ATTESTATION_VERSION",
    "COUNTER_REFUSALS",
    "COUNTER_REVOCATIONS",
    "KIND_AUTHORITY",
    "KIND_RESIDENT",
    "KIND_SESSION",
    "MESH_AUDIENCE",
    "PURPOSE_EVENT",
    "PURPOSE_RPC",
    "PURPOSE_RPC_REPLY",
    "PURPOSE_REVOCATION",
    "AttestationReason",
    "AttestationRefusedError",
    "AttestationUnavailableError",
    "CertificateSource",
    "IssuedMeshCertificate",
    "MeshAttestationVerifier",
    "MeshCertificate",
    "MeshSigner",
    "VerifiedSender",
    "b64url",
    "b64url_decode",
    "canonical",
    "digest",
    "public_key_bytes",
    "sanitize_topic",
    "topic_matches",
]

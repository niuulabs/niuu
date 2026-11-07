"""SPIFFE/SPIRE identity for VM guests calling Forge's metadata endpoint.

The guest agent fetches a JWT-SVID from the SPIRE agent on its VM and sends it
as ``Authorization: Bearer <JWT-SVID>``. Forge verifies the SVID against the
SPIRE OIDC discovery provider's keys and reads the allocation out of the
subject, which must match ``spiffe_id_template``
(default ``spiffe://{trust_domain}/forge-vm/{pool_id}/{allocation_id}``).

SPIRE decides who may hold that SVID. Forge registers one spire-controller-manager
``ClusterStaticEntry`` per allocation, whose selectors name the guest container
(``docker:label:compute.niuu.io/allocation:<id>``) and whose parent is the VM's
node agent. A process that is not that container gets no SVID.

Node attestation (how the VM's SPIRE agent proves it is a legitimate node) is
provider-specific and chosen by ``node_attestation``:

``x509pop``
    Forge generates a fresh node key and a certificate signed by an operator CA
    for each machine, and delivers them, a ``spire-agent`` config and a systemd
    unit through the private machine bootstrap. This is the generic path for
    Harvester VMs, which have no cloud metadata attestor.
``provider``
    The image or provider already runs an attested agent. Forge adds nothing to
    the machine bootstrap and takes the entry's parent from ``parent_id_template``.

Why a string choice and not a nested adapter (``dynamic-adapters.md``): the two
variants differ in three small steps of one class (what the bootstrap adds, how
the parent id is derived, which kwargs are legal) and share everything else
(verification, templates, registration). A nested adapter would need its own
ABC and wiring for about forty lines, and the cross-field checks (x509pop
kwargs with ``provider``, a parent template with ``x509pop``) are clearer in
one place. An unknown value is a startup error, never a default.

SPIRE server requirements (x509pop)
-----------------------------------
* ``NodeAttestor "x509pop"`` enabled in ``external_pki`` mode (the plugin's
  default; the Helm chart's ``spire-server.nodeAttestor.x509POP.mode`` defaults to
  ``spiffe`` and must be changed), with ``ca_bundle_path`` containing the
  certificate of ``node_ca_cert_file`` (chart: ``caBundle.bundle``).
* With ``x509pop_agent_id="common_name"`` (default) the plugin's
  ``agent_path_template`` must be ``"/{{ .PluginName }}/{{ .Subject.CommonName }}"``,
  so the agent is ``spiffe://{trust_domain}/spire/agent/x509pop/{node id}``. With
  ``"fingerprint"`` leave the plugin's default
  (``/{{ .PluginName }}/{{ .Fingerprint }}``) and the id is the SHA-1 of the
  node certificate.
* The server's agent endpoint (``server_address:server_port``, normally 8081)
  reachable from the VMs.
* The ``trust_bundle_file`` / ``trust_bundle_url`` the agent bootstraps from.
  SPIRE supports both: ``trust_bundle_path`` (a PEM or SPIFFE-format file, the
  one used here for ``trust_bundle_file``) and ``trust_bundle_url`` (https,
  verified against the system trust store). ``insecure_bootstrap`` is never used.
* spire-controller-manager installed, with permission for Forge to get, create,
  replace and delete ``clusterstaticentries.spire.spiffe.io``.
* The OIDC discovery provider's issuer and ``/keys`` URL reachable from Forge.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import string
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import NameOID

from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier
from volundr.domain.compute import AllocationRef, BootstrapFile, MachineBootstrap
from volundr.domain.guest_credentials import (
    GuestAuthenticationError,
    GuestIdentity,
    GuestRequest,
)

logger = logging.getLogger(__name__)

DEFAULT_SPIFFE_ID_TEMPLATE = "spiffe://{trust_domain}/forge-vm/{pool_id}/{allocation_id}"
DEFAULT_SELECTOR_TEMPLATES = ("docker:label:compute.niuu.io/allocation:{allocation_id}",)
DEFAULT_SOCKET_PATH = "/run/spire/agent/api.sock"
DEFAULT_AGENT_BINARY = "/opt/spire/bin/spire-agent"

NODE_ATTESTATIONS = ("x509pop", "provider")
AGENT_IDS = ("common_name", "fingerprint")
BUNDLE_FORMATS = ("pem", "spiffe")

SPIRE_DIR = "/etc/niuu/spire"
NODE_KEY_PATH = f"{SPIRE_DIR}/node.key"
NODE_CERT_PATH = f"{SPIRE_DIR}/node.crt"
AGENT_CONFIG_PATH = f"{SPIRE_DIR}/agent.conf"
TRUST_BUNDLE_PATH = f"{SPIRE_DIR}/bundle"
AGENT_DATA_DIR = "/var/lib/niuu/spire-agent"
AGENT_UNIT_NAME = "niuu-spire-agent.service"
AGENT_UNIT_PATH = f"/etc/systemd/system/{AGENT_UNIT_NAME}"

#: Where Forge looks the entry's CRD up. Verified against spire-controller-manager
#: ``api/v1alpha1/clusterstaticentry_types.go``: cluster-scoped, group ``spire.spiffe.io``.
ENTRY_GROUP = "spire.spiffe.io"
ENTRY_VERSION = "v1alpha1"
ENTRY_PLURAL = "clusterstaticentries"
ENTRY_KIND = "ClusterStaticEntry"
ENTRY_NAME_PREFIX = "forge-vm-"
ALLOCATION_LABEL = "compute.niuu.io/allocation"
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY = "volundr"

#: A node id is 128 random bits, the width of a UUID; it only has to be unguessable
#: and unique, and is not a tunable.
NODE_ID_BYTES = 16

_ID_FIELDS = frozenset({"trust_domain", "pool_id", "allocation_id"})
_UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TRUST_DOMAIN = re.compile(r"^[a-z0-9._-]+$")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/@+-]+$")
_HOST = re.compile(r"^[A-Za-z0-9._:-]+$")
_URL = re.compile(r"^https://[^\s\"\\]+$")
_SELECTOR = re.compile(r"^[A-Za-z0-9_]+:\S.*$")
_PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)


class SpiffeRegistrationError(RuntimeError):
    """The SPIRE registration entry for an allocation could not be written or removed."""


def _fail(message: str) -> ValueError:
    return ValueError(f"SpiffeGuestIdentity: {message}")


def _template_fields(name: str, template: str) -> list[str]:
    """Field names of ``template``; only plain ``{name}`` fields from ``_ID_FIELDS`` are legal."""
    fields: list[str] = []
    for _literal, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            continue
        if spec or conversion or field not in _ID_FIELDS:
            raise _fail(
                f"{name} may only use the fields {sorted(_ID_FIELDS)} without format "
                f"specifiers, got {template!r}"
            )
        fields.append(field)
    return fields


def _require_path(name: str, value: str) -> str:
    if not _SAFE_PATH.fullmatch(value):
        raise _fail(f"{name} must be an absolute path of plain characters, got {value!r}")
    return value


def _require_file(name: str, value: str) -> Path:
    if not value:
        raise _fail(f"{name} is required")
    path = Path(value)
    if not path.is_file():
        raise _fail(f"{name} {value!r} is not a readable file")
    return path


def _pem_certificates(name: str, content: str) -> list[x509.Certificate]:
    blocks = _PEM_CERT.findall(content)
    if not blocks:
        raise _fail(f"{name} holds no PEM certificate")
    return [x509.load_pem_x509_certificate(block.encode("ascii")) for block in blocks]


class SpiffeGuestIdentity(GuestIdentity):
    """JWT-SVID authentication of VM guests, with SPIRE registration and node attestation."""

    def __init__(
        self,
        *,
        trust_domain: str,
        issuer: str,
        audience: str,
        jwks_uri: str,
        ca_cert_path: str = "",
        spiffe_id_template: str = DEFAULT_SPIFFE_ID_TEMPLATE,
        selector_templates: Sequence[str] = DEFAULT_SELECTOR_TEMPLATES,
        jwt_svid_ttl_seconds: int | None = None,
        socket_path: str = DEFAULT_SOCKET_PATH,
        spire_agent_binary: str = DEFAULT_AGENT_BINARY,
        node_attestation: str = "x509pop",
        parent_id_template: str = "",
        server_address: str = "",
        server_port: int = 0,
        trust_bundle_file: str = "",
        trust_bundle_url: str = "",
        trust_bundle_format: str = "pem",
        node_ca_cert_file: str = "",
        node_ca_key_file: str = "",
        node_certificate_ttl_seconds: int = 30 * 24 * 3600,
        node_certificate_backdate_seconds: int = 300,
        x509pop_agent_id: str = "common_name",
        agent_restart_seconds: int = 5,
    ) -> None:
        if not _TRUST_DOMAIN.fullmatch(trust_domain):
            raise _fail(
                f"trust_domain must be a lower-case SPIFFE trust domain, got {trust_domain!r}"
            )
        for name, value in (("issuer", issuer), ("audience", audience)):
            if not value or value != value.strip():
                raise _fail(f"{name} is required (an empty one would disable that check)")
        if not _URL.fullmatch(jwks_uri):
            raise _fail(
                f"jwks_uri must be an https URL of the OIDC discovery keys, got {jwks_uri!r}"
            )
        if ca_cert_path:
            _require_file("ca_cert_path", ca_cert_path)
        self._trust_domain = trust_domain
        self._audience = audience
        self._socket_path = _require_path("socket_path", socket_path)
        self._binary = _require_path("spire_agent_binary", spire_agent_binary)

        self._id_template = self._validate_id_template("spiffe_id_template", spiffe_id_template)
        if _template_fields("spiffe_id_template", spiffe_id_template).count("allocation_id") != 1:
            raise _fail("spiffe_id_template must contain {allocation_id} exactly once")
        self._subject_pattern = self._subject_regex(spiffe_id_template)

        if isinstance(selector_templates, str) or not selector_templates:
            raise _fail("selector_templates must be a non-empty list of 'type:value' templates")
        self._selector_templates = tuple(selector_templates)
        for selector in self._selector_templates:
            if not isinstance(selector, str) or not _SELECTOR.fullmatch(selector):
                raise _fail(f"selector {selector!r} is not a 'type:value' template")
            _template_fields("selector_templates", selector)
        if not any(
            "allocation_id" in _template_fields("selector_templates", selector)
            for selector in self._selector_templates
        ):
            raise _fail(
                "selector_templates must bind the allocation with {allocation_id} in at "
                "least one selector, otherwise any workload on the node could hold the identity"
            )

        if jwt_svid_ttl_seconds is not None and jwt_svid_ttl_seconds <= 0:
            raise _fail("jwt_svid_ttl_seconds must be positive, or unset to use the server default")
        self._jwt_svid_ttl = jwt_svid_ttl_seconds

        if node_attestation not in NODE_ATTESTATIONS:
            raise _fail(
                f"node_attestation must be one of {NODE_ATTESTATIONS}, got {node_attestation!r}"
            )
        self._node_attestation = node_attestation
        self._parent_template = ""
        self._agent_id = x509pop_agent_id
        x509pop_kwargs = {
            "server_address": server_address,
            "server_port": server_port,
            "trust_bundle_file": trust_bundle_file,
            "trust_bundle_url": trust_bundle_url,
            "node_ca_cert_file": node_ca_cert_file,
            "node_ca_key_file": node_ca_key_file,
        }
        if node_attestation == "provider":
            self._validate_provider(parent_id_template, x509pop_kwargs, x509pop_agent_id)
            self._parent_template = parent_id_template
        else:
            self._validate_x509pop(
                parent_id_template=parent_id_template,
                server_address=server_address,
                server_port=server_port,
                trust_bundle_file=trust_bundle_file,
                trust_bundle_url=trust_bundle_url,
                trust_bundle_format=trust_bundle_format,
                node_ca_cert_file=node_ca_cert_file,
                node_ca_key_file=node_ca_key_file,
                ttl=node_certificate_ttl_seconds,
                backdate=node_certificate_backdate_seconds,
                agent_id=x509pop_agent_id,
                restart=agent_restart_seconds,
            )

        self._verifier = JwtWorkloadIdentityVerifier(
            issuer=issuer,
            audiences=[audience],
            jwks_uri=jwks_uri,
            ca_cert_path=ca_cert_path,
        )
        self._api_client: Any | None = None
        self._api: Any | None = None
        self._api_lock = asyncio.Lock()

    # -- configuration validation -------------------------------------------------

    def _validate_id_template(self, name: str, template: str) -> str:
        _template_fields(name, template)
        self._check_spiffe_id(name, self._render(template, "pool", UUID(int=0)))
        return template

    def _check_spiffe_id(self, name: str, value: str) -> None:
        pattern = rf"spiffe://{re.escape(self._trust_domain)}(/[A-Za-z0-9._-]+)+"
        if not re.fullmatch(pattern, value):
            raise _fail(
                f"{name} must render a SPIFFE ID in trust domain {self._trust_domain!r}, "
                f"got {value!r}"
            )

    def _render(self, template: str, pool_id: str, allocation_id: UUID) -> str:
        return template.format(
            trust_domain=self._trust_domain, pool_id=pool_id, allocation_id=str(allocation_id)
        )

    def _subject_regex(self, template: str) -> re.Pattern[str]:
        parts: list[str] = []
        seen: set[str] = set()
        for literal, field, _spec, _conversion in string.Formatter().parse(template):
            parts.append(re.escape(literal))
            if field is None:
                continue
            if field in seen and field != "trust_domain":
                raise _fail(f"spiffe_id_template uses {{{field}}} more than once")
            seen.add(field)
            match field:
                case "trust_domain":
                    parts.append(re.escape(self._trust_domain))
                case "pool_id":
                    parts.append(r"[A-Za-z0-9._-]+")
                case "allocation_id":
                    parts.append(f"(?P<allocation_id>{_UUID_PATTERN})")
        return re.compile("".join(parts))

    def _validate_provider(
        self, parent_id_template: str, x509pop_kwargs: dict[str, Any], agent_id: str
    ) -> None:
        if not parent_id_template:
            raise _fail(
                "node_attestation='provider' requires parent_id_template, the SPIFFE ID of "
                "the agent the provider runs"
            )
        stray = sorted(name for name, value in x509pop_kwargs.items() if value)
        if stray or agent_id != "common_name":
            raise _fail(
                f"{stray or ['x509pop_agent_id']} only apply to node_attestation='x509pop'; "
                "remove them or set node_attestation to 'x509pop'"
            )
        self._validate_id_template("parent_id_template", parent_id_template)

    def _validate_x509pop(
        self,
        *,
        parent_id_template: str,
        server_address: str,
        server_port: int,
        trust_bundle_file: str,
        trust_bundle_url: str,
        trust_bundle_format: str,
        node_ca_cert_file: str,
        node_ca_key_file: str,
        ttl: int,
        backdate: int,
        agent_id: str,
        restart: int,
    ) -> None:
        if parent_id_template:
            raise _fail(
                "parent_id_template only applies to node_attestation='provider'; under "
                "'x509pop' the parent is the node agent derived from the node certificate"
            )
        if not _HOST.fullmatch(server_address):
            raise _fail("server_address is required: the SPIRE server the VMs' agents dial")
        if not isinstance(server_port, int) or not 0 < server_port < 65536:
            raise _fail(f"server_port must be a TCP port, got {server_port!r}")
        if bool(trust_bundle_file) == bool(trust_bundle_url):
            raise _fail(
                "set exactly one of trust_bundle_file (a bundle Forge ships to each VM) or "
                "trust_bundle_url (https, verified against the VM's system trust store)"
            )
        if trust_bundle_format not in BUNDLE_FORMATS:
            raise _fail(f"trust_bundle_format must be one of {BUNDLE_FORMATS}")
        if trust_bundle_url and not _URL.fullmatch(trust_bundle_url):
            raise _fail(f"trust_bundle_url must be an https URL, got {trust_bundle_url!r}")
        self._bundle_content = ""
        if trust_bundle_file:
            self._bundle_content = _require_file("trust_bundle_file", trust_bundle_file).read_text()
            self._validate_bundle(trust_bundle_format, self._bundle_content)
        if agent_id not in AGENT_IDS:
            raise _fail(f"x509pop_agent_id must be one of {AGENT_IDS}, got {agent_id!r}")
        if ttl <= 0:
            raise _fail("node_certificate_ttl_seconds must be positive")
        if backdate < 0:
            raise _fail("node_certificate_backdate_seconds must not be negative")
        if restart <= 0:
            raise _fail("agent_restart_seconds must be positive")
        self._server_address = server_address
        self._server_port = server_port
        self._trust_bundle_url = trust_bundle_url
        self._trust_bundle_format = trust_bundle_format
        self._node_ca_cert_file = str(_require_file("node_ca_cert_file", node_ca_cert_file))
        self._node_ca_key_file = str(_require_file("node_ca_key_file", node_ca_key_file))
        self._cert_ttl = timedelta(seconds=ttl)
        self._cert_backdate = timedelta(seconds=backdate)
        self._restart_seconds = restart
        # Fail at startup, not at the first VM, if the CA cannot sign node certificates.
        self._load_node_ca()

    @staticmethod
    def _validate_bundle(bundle_format: str, content: str) -> None:
        if bundle_format == "pem":
            _pem_certificates("trust_bundle_file", content)
            return
        try:
            document = json.loads(content)
        except ValueError:
            raise _fail("trust_bundle_file is not a SPIFFE bundle (JSON)") from None
        if not isinstance(document, dict) or not document.get("keys"):
            raise _fail("trust_bundle_file is a SPIFFE bundle with no keys")

    def _load_node_ca(self) -> tuple[x509.Certificate, Any]:
        content = Path(self._node_ca_cert_file).read_text()
        ca_cert = _pem_certificates("node_ca_cert_file", content)[0]
        try:
            ca_key = serialization.load_pem_private_key(
                Path(self._node_ca_key_file).read_bytes(), password=None
            )
        except (ValueError, TypeError):
            raise _fail("node_ca_key_file is not an unencrypted PEM private key") from None
        if not isinstance(
            ca_key, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey
        ):
            raise _fail("node_ca_key_file must be an RSA, ECDSA or Ed25519 key")
        public = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if ca_key.public_key().public_bytes(*public) != ca_cert.public_key().public_bytes(*public):
            raise _fail("node_ca_key_file does not belong to node_ca_cert_file")
        try:
            constraints = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
        except x509.ExtensionNotFound:
            raise _fail(
                "node_ca_cert_file is not a CA certificate (no basic constraints)"
            ) from None
        if not constraints.value.ca:
            raise _fail("node_ca_cert_file is not a CA certificate")
        return ca_cert, ca_key

    # -- authentication -----------------------------------------------------------

    async def authenticate(self, request: GuestRequest) -> UUID:
        token = self._bearer_token(request)
        try:
            claims = await self._verifier.verify(token)
        except jwt.PyJWKClientConnectionError:
            logger.error(
                "SPIFFE guest authentication cannot reach the JWKS endpoint; check the "
                "configured jwks_uri and ca_cert_path"
            )
            raise GuestAuthenticationError() from None
        except (jwt.PyJWTError, ValueError):
            raise GuestAuthenticationError() from None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not claims.get("exp"):
            raise GuestAuthenticationError()
        match = self._subject_pattern.fullmatch(subject)
        if match is None:
            raise GuestAuthenticationError()
        return UUID(match.group("allocation_id"))

    @staticmethod
    def _bearer_token(request: GuestRequest) -> str:
        values = [v for k, v in request.headers.items() if k.lower() == "authorization"]
        if len(values) != 1:
            raise GuestAuthenticationError()
        scheme, _, token = values[0].partition(" ")
        if scheme.lower() != "bearer" or not token or token != token.strip() or " " in token:
            raise GuestAuthenticationError()
        return token

    # -- guest side ---------------------------------------------------------------

    def agent_auth(self, allocation_id: UUID) -> dict:
        return {
            "kind": "spiffe",
            "audience": self._audience,
            "socket_path": self._socket_path,
            "spire_agent_binary": self._binary,
        }

    def agent_mounts(self) -> tuple[dict, ...]:
        return (
            {
                "source": str(Path(self._socket_path).parent),
                "target": str(Path(self._socket_path).parent),
                "readonly": True,
            },
            {"source": self._binary, "target": self._binary, "readonly": True},
        )

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        if self._node_attestation == "provider":
            return defaults
        taken = {file.path for file in defaults.files}
        node_id = secrets.token_hex(NODE_ID_BYTES)
        files = self._node_files(node_id)
        clash = sorted(taken & {file.path for file in files})
        if clash:
            raise _fail(f"the machine bootstrap already writes {clash}")
        return defaults.model_copy(
            update={
                "files": (*defaults.files, *files),
                "commands": (*defaults.commands, *self._agent_commands()),
            }
        )

    def _node_files(self, node_id: str) -> tuple[BootstrapFile, ...]:
        key, certificate = self._issue_node_certificate(node_id)
        files = [
            BootstrapFile(path=NODE_KEY_PATH, content=key, permissions="0600"),
            BootstrapFile(path=NODE_CERT_PATH, content=certificate, permissions="0644"),
            BootstrapFile(path=AGENT_CONFIG_PATH, content=self._agent_config(), permissions="0644"),
            BootstrapFile(path=AGENT_UNIT_PATH, content=self._agent_unit(), permissions="0644"),
        ]
        if self._bundle_content:
            files.append(
                BootstrapFile(
                    path=TRUST_BUNDLE_PATH, content=self._bundle_content, permissions="0644"
                )
            )
        return tuple(files)

    def _issue_node_certificate(self, node_id: str) -> tuple[str, str]:
        ca_cert, ca_key = self._load_node_ca()
        now = datetime.now(UTC)
        not_after = now + self._cert_ttl
        if now < ca_cert.not_valid_before_utc or not_after > ca_cert.not_valid_after_utc:
            raise _fail(
                f"the node CA is valid {ca_cert.not_valid_before_utc} to "
                f"{ca_cert.not_valid_after_utc}, which does not cover a node certificate "
                f"issued now for {self._cert_ttl}; renew the CA or lower "
                "node_certificate_ttl_seconds"
            )
        node_key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]))
            .issuer_name(ca_cert.subject)
            .public_key(node_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - self._cert_backdate)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
                critical=False,
            )
            .sign(
                ca_key, None if isinstance(ca_key, ed25519.Ed25519PrivateKey) else hashes.SHA256()
            )
        )
        key_pem = node_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        # The CA follows the identity certificate so an intermediate CA still chains
        # to the server's root bundle.
        chain = certificate.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(
            serialization.Encoding.PEM
        )
        return key_pem, chain.decode("ascii")

    def _agent_config(self) -> str:
        bundle = (
            f'trust_bundle_path = "{TRUST_BUNDLE_PATH}"'
            if self._bundle_content
            else f'trust_bundle_url = "{self._trust_bundle_url}"'
        )
        return (
            "agent {\n"
            f'    data_dir = "{AGENT_DATA_DIR}"\n'
            f'    server_address = "{self._server_address}"\n'
            f'    server_port = "{self._server_port}"\n'
            f'    socket_path = "{self._socket_path}"\n'
            f'    trust_domain = "{self._trust_domain}"\n'
            f"    {bundle}\n"
            f'    trust_bundle_format = "{self._trust_bundle_format}"\n'
            "}\n\n"
            "plugins {\n"
            '    NodeAttestor "x509pop" {\n'
            "        plugin_data {\n"
            f'            private_key_path = "{NODE_KEY_PATH}"\n'
            f'            certificate_path = "{NODE_CERT_PATH}"\n'
            "        }\n"
            "    }\n"
            '    KeyManager "memory" {\n'
            "        plugin_data {\n"
            "        }\n"
            "    }\n"
            '    WorkloadAttestor "docker" {\n'
            "        plugin_data {\n"
            "        }\n"
            "    }\n"
            '    WorkloadAttestor "unix" {\n'
            "        plugin_data {\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

    def _agent_unit(self) -> str:
        socket_dir = str(Path(self._socket_path).parent)
        return (
            "[Unit]\n"
            "Description=Niuu SPIRE agent for the Forge VM guest\n"
            "After=network-online.target docker.service\n"
            "Wants=network-online.target docker.service\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStartPre=/usr/bin/env mkdir -p {socket_dir} {AGENT_DATA_DIR}\n"
            f"ExecStart={self._binary} run -config {AGENT_CONFIG_PATH}\n"
            "Restart=always\n"
            f"RestartSec={self._restart_seconds}\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    def _agent_commands(self) -> tuple[tuple[str, ...], ...]:
        check = (
            'test -x "$1" || { echo "spire-agent binary $1 is missing or not executable: '
            'the VM image must ship it at spire_agent_binary" >&2; exit 1; }'
        )
        return (
            ("sh", "-c", check, "sh", self._binary),
            ("systemctl", "daemon-reload"),
            ("systemctl", "enable", "--now", AGENT_UNIT_NAME),
        )

    # -- registration -------------------------------------------------------------

    def _parent_id(self, lease: AllocationRef, machine: MachineBootstrap) -> str:
        if self._node_attestation == "provider":
            return self._render(self._parent_template, lease.pool_id, lease.id)
        certificates = [file for file in machine.files if file.path == NODE_CERT_PATH]
        if not certificates:
            raise SpiffeRegistrationError(
                f"allocation {lease.id} has no {NODE_CERT_PATH} in its machine bootstrap; it "
                "was created without this identity's machine_bootstrap"
            )
        node = _pem_certificates(NODE_CERT_PATH, certificates[0].content)[0]
        if self._agent_id == "fingerprint":
            node_id = hashlib.sha1(  # noqa: S324 - SPIRE's own x509pop agent id is SHA-1
                node.public_bytes(serialization.Encoding.DER)
            ).hexdigest()
        else:
            node_id = node.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        return f"spiffe://{self._trust_domain}/spire/agent/x509pop/{node_id}"

    def _entry(self, lease: AllocationRef, machine: MachineBootstrap) -> dict[str, Any]:
        spiffe_id = self._render(self._id_template, lease.pool_id, lease.id)
        parent_id = self._parent_id(lease, machine)
        for name, value in (("SPIFFE ID", spiffe_id), ("parent ID", parent_id)):
            try:
                self._check_spiffe_id(name, value)
            except ValueError as exc:
                raise SpiffeRegistrationError(
                    f"allocation {lease.id}: pool_id {lease.pool_id!r} does not produce a "
                    f"valid {name}: {exc}"
                ) from None
        spec: dict[str, Any] = {
            "spiffeID": spiffe_id,
            "parentID": parent_id,
            "selectors": [
                self._render(selector, lease.pool_id, lease.id)
                for selector in self._selector_templates
            ],
        }
        if self._jwt_svid_ttl is not None:
            spec["jwtSVIDTTL"] = f"{self._jwt_svid_ttl}s"
        return {
            "apiVersion": f"{ENTRY_GROUP}/{ENTRY_VERSION}",
            "kind": ENTRY_KIND,
            "metadata": {
                "name": f"{ENTRY_NAME_PREFIX}{lease.id}",
                "labels": {MANAGED_BY_LABEL: MANAGED_BY, ALLOCATION_LABEL: str(lease.id)},
            },
            "spec": spec,
        }

    async def _custom_objects(self) -> Any:
        async with self._api_lock:
            if self._api is not None:
                return self._api
            from kubernetes_asyncio import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                await config.load_kube_config()
            self._api_client = client.ApiClient()
            self._api = client.CustomObjectsApi(self._api_client)
            return self._api

    @staticmethod
    def _refused(action: str, name: str, status: int) -> SpiffeRegistrationError:
        return SpiffeRegistrationError(
            f"Kubernetes refused to {action} {ENTRY_KIND} {name} (HTTP {status}); install "
            "spire-controller-manager and grant Forge get, create, update and delete on "
            f"{ENTRY_PLURAL}.{ENTRY_GROUP}"
        )

    async def register(self, lease: AllocationRef, machine: MachineBootstrap) -> None:
        from kubernetes_asyncio.client.exceptions import ApiException

        body = self._entry(lease, machine)
        name = body["metadata"]["name"]
        api = await self._custom_objects()
        coordinates = (ENTRY_GROUP, ENTRY_VERSION, ENTRY_PLURAL)
        try:
            existing = await api.get_cluster_custom_object(*coordinates, name)
        except ApiException as exc:
            if exc.status != 404:
                raise self._refused("read", name, exc.status) from None
            existing = None
        try:
            if existing is None:
                await api.create_cluster_custom_object(*coordinates, body)
                return
            if existing.get("spec") == body["spec"]:
                return
            # A PUT needs the stored resourceVersion; replacing (not merging) also
            # drops any field somebody added to the entry by hand.
            body["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
            await api.replace_cluster_custom_object(*coordinates, name, body)
        except ApiException as exc:
            raise self._refused("write", name, exc.status) from None

    async def unregister(self, lease: AllocationRef, machine: MachineBootstrap) -> None:
        from kubernetes_asyncio.client.exceptions import ApiException

        name = f"{ENTRY_NAME_PREFIX}{lease.id}"
        api = await self._custom_objects()
        try:
            await api.delete_cluster_custom_object(ENTRY_GROUP, ENTRY_VERSION, ENTRY_PLURAL, name)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise self._refused("delete", name, exc.status) from None

    async def close(self) -> None:
        async with self._api_lock:
            api_client, self._api_client, self._api = self._api_client, None, None
        if api_client is not None:
            await api_client.close()

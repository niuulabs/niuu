"""SpiffeGuestIdentity: JWT-SVID auth, x509pop node bootstrap and SPIRE registration."""

from __future__ import annotations

import base64
import hashlib
import sys
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.x509.oid import NameOID

from niuu.adapters.workload_identity.jwt import JwtWorkloadIdentityVerifier
from volundr.adapters.outbound.guest_identity_spiffe import (
    AGENT_CONFIG_PATH,
    AGENT_UNIT_NAME,
    AGENT_UNIT_PATH,
    NODE_CERT_PATH,
    NODE_KEY_PATH,
    TRUST_BUNDLE_PATH,
    SpiffeGuestIdentity,
    SpiffeRegistrationError,
)
from volundr.domain.compute import BootstrapFile, ComputeLease, MachineBootstrap
from volundr.domain.guest_credentials import GuestAuthenticationError, GuestRequest

ISSUER = "https://spire-oidc.ymir.asgard.niuu.world"
AUDIENCE = "forge-metadata"
TRUST_DOMAIN = "ymir.niuu.world"
POOL = "harvester-vm"
KID = "svid-key"


def _b64url(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwk(key: rsa.RSAPrivateKey) -> dict[str, Any]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n),
        "e": _b64url(numbers.e),
    }


def _subject(allocation: UUID, pool: str = POOL) -> str:
    return f"spiffe://{TRUST_DOMAIN}/forge-vm/{pool}/{allocation}"


def _svid(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": _subject(uuid4()),
        "aud": [AUDIENCE],
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


def _request(token: str | None, *, header: str = "Authorization") -> GuestRequest:
    headers = {} if token is None else {header: token}
    return GuestRequest(method="POST", path="/x", headers=headers)


def _lease(allocation: UUID | None = None, pool: str = POOL) -> ComputeLease:
    return ComputeLease(
        id=allocation or uuid4(),
        pool_id=pool,
        session_id=uuid4(),
        tenant_id="t",
        owner_id="o",
        profile="p",
        request_fingerprint="f",
    )


@pytest.fixture
def svid_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def ca(tmp_path: Path) -> tuple[Path, Path, x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Forge VM node CA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "ca.crt"
    key_file = tmp_path / "ca.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file, cert, key


@pytest.fixture
def spire_bundle(tmp_path: Path, ca: Any) -> Path:
    path = tmp_path / "bundle.pem"
    path.write_bytes(ca[2].public_bytes(serialization.Encoding.PEM))
    return path


def _make(ca: Any, bundle: Path, svid_key: rsa.RSAPrivateKey | None = None, **kw: Any):
    kwargs: dict[str, Any] = {
        "trust_domain": TRUST_DOMAIN,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_uri": f"{ISSUER}/keys",
        "server_address": "spire.ymir.niuu.world",
        "server_port": 8081,
        "trust_bundle_file": str(bundle),
        "node_ca_cert_file": str(ca[0]),
        "node_ca_key_file": str(ca[1]),
    }
    kwargs.update(kw)
    identity = SpiffeGuestIdentity(**kwargs)
    if svid_key is not None:
        identity._verifier = JwtWorkloadIdentityVerifier(
            issuer=ISSUER, audiences=[AUDIENCE], static_jwks={"keys": [_jwk(svid_key)]}
        )
    return identity


@pytest.fixture
def identity(ca: Any, spire_bundle: Path, svid_key: rsa.RSAPrivateKey) -> SpiffeGuestIdentity:
    return _make(ca, spire_bundle, svid_key)


# -- authenticate ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_returns_the_allocation_from_the_subject(identity, svid_key):
    allocation = uuid4()
    token = _svid(svid_key, sub=_subject(allocation))
    assert await identity.authenticate(_request(f"Bearer {token}")) == allocation


@pytest.mark.asyncio
async def test_authenticate_accepts_lowercase_scheme_and_header_name(identity, svid_key):
    allocation = uuid4()
    token = _svid(svid_key, sub=_subject(allocation))
    request = _request(f"bearer {token}", header="authorization")
    assert await identity.authenticate(request) == allocation


@pytest.mark.asyncio
async def test_custom_subject_template_is_honoured(ca, spire_bundle, svid_key):
    identity = _make(
        ca,
        spire_bundle,
        svid_key,
        spiffe_id_template="spiffe://{trust_domain}/vm/{allocation_id}",
    )
    allocation = uuid4()
    token = _svid(svid_key, sub=f"spiffe://{TRUST_DOMAIN}/vm/{allocation}")
    assert await identity.authenticate(_request(f"Bearer {token}")) == allocation


def _bad_tokens(key: rsa.RSAPrivateKey) -> dict[str, str]:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    allocation = uuid4()
    return {
        "wrong_trust_domain": _svid(key, sub=f"spiffe://evil.example/forge-vm/{POOL}/{allocation}"),
        "wrong_path": _svid(key, sub=f"spiffe://{TRUST_DOMAIN}/other/{POOL}/{allocation}"),
        "not_a_uuid": _svid(key, sub=f"spiffe://{TRUST_DOMAIN}/forge-vm/{POOL}/not-a-uuid"),
        "uppercase_uuid": _svid(key, sub=_subject(allocation)[:-36] + str(allocation).upper()),
        "suffix": _svid(key, sub=_subject(allocation) + "/extra"),
        "prefix": _svid(key, sub="x" + _subject(allocation)),
        "missing_pool": _svid(key, sub=f"spiffe://{TRUST_DOMAIN}/forge-vm//{allocation}"),
        "no_subject": _svid(key, sub=None),
        "no_expiry": _svid(key, exp=None),
        "expired": _svid(key, exp=int(time.time()) - 60),
        "wrong_audience": _svid(key, aud=["somebody-else"]),
        "wrong_issuer": _svid(key, iss="https://other.example"),
        "wrong_signature": _svid(other),
        "garbage": "not.a.jwt",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "wrong_trust_domain",
        "wrong_path",
        "not_a_uuid",
        "uppercase_uuid",
        "suffix",
        "prefix",
        "missing_pool",
        "no_subject",
        "no_expiry",
        "expired",
        "wrong_audience",
        "wrong_issuer",
        "wrong_signature",
        "garbage",
    ],
)
async def test_authenticate_rejects_without_detail(identity, svid_key, case):
    token = _bad_tokens(svid_key)[case]
    with pytest.raises(GuestAuthenticationError) as raised:
        await identity.authenticate(_request(f"Bearer {token}"))
    assert raised.value.args == ()
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer ", "Basic abc", "Bearer a b", "Bearer  a", "Token abc"],
)
async def test_authenticate_rejects_malformed_authorization(identity, header):
    with pytest.raises(GuestAuthenticationError):
        await identity.authenticate(_request(header))


@pytest.mark.asyncio
async def test_authenticate_rejects_duplicate_authorization_headers(identity, svid_key):
    token = _svid(svid_key)
    request = GuestRequest(
        method="POST",
        path="/x",
        headers={"Authorization": f"Bearer {token}", "authorization": f"Bearer {token}"},
    )
    with pytest.raises(GuestAuthenticationError):
        await identity.authenticate(request)


@pytest.mark.asyncio
async def test_unreachable_jwks_is_logged_and_rejected(identity, caplog):
    class Unreachable:
        async def verify(self, token: str) -> dict:
            raise jwt.PyJWKClientConnectionError("down")

    identity._verifier = Unreachable()
    with pytest.raises(GuestAuthenticationError):
        await identity.authenticate(_request("Bearer abc"))
    assert "cannot reach the JWKS endpoint" in caplog.text


# -- guest side ------------------------------------------------------------------


def test_agent_auth_and_mounts(identity):
    assert identity.agent_auth(uuid4()) == {
        "kind": "spiffe",
        "audience": AUDIENCE,
        "socket_path": "/run/spire/agent/api.sock",
        "spire_agent_binary": "/opt/spire/bin/spire-agent",
    }
    assert identity.agent_mounts() == (
        {"source": "/run/spire/agent", "target": "/run/spire/agent", "readonly": True},
        {
            "source": "/opt/spire/bin/spire-agent",
            "target": "/opt/spire/bin/spire-agent",
            "readonly": True,
        },
    )


def test_custom_socket_and_binary_paths(ca, spire_bundle):
    identity = _make(
        ca,
        spire_bundle,
        socket_path="/var/run/spire/sock/agent.sock",
        spire_agent_binary="/usr/local/bin/spire-agent",
    )
    auth = identity.agent_auth(uuid4())
    assert auth["socket_path"] == "/var/run/spire/sock/agent.sock"
    assert [m["source"] for m in identity.agent_mounts()] == [
        "/var/run/spire/sock",
        "/usr/local/bin/spire-agent",
    ]


# -- x509pop machine bootstrap ---------------------------------------------------


def _files(bootstrap: MachineBootstrap) -> dict[str, BootstrapFile]:
    return {file.path: file for file in bootstrap.files}


def test_machine_bootstrap_adds_files_and_commands(identity):
    base = MachineBootstrap(
        files=(BootstrapFile(path="/etc/other", content="x"),),
        commands=(("true",),),
        ssh_authorized_keys=("ssh-ed25519 AAAA",),
    )
    result = identity.machine_bootstrap(base)
    files = _files(result)
    assert set(files) == {
        "/etc/other",
        NODE_KEY_PATH,
        NODE_CERT_PATH,
        AGENT_CONFIG_PATH,
        AGENT_UNIT_PATH,
        TRUST_BUNDLE_PATH,
    }
    assert files[NODE_KEY_PATH].permissions == "0600"
    assert result.ssh_authorized_keys == base.ssh_authorized_keys
    assert result.commands[0] == ("true",)
    verify, reload, enable = result.commands[1:]
    assert verify[:2] == ("sh", "-c")
    assert verify[-1] == "/opt/spire/bin/spire-agent"
    assert "missing or not executable" in verify[2]
    assert reload == ("systemctl", "daemon-reload")
    assert enable == ("systemctl", "enable", "--now", AGENT_UNIT_NAME)


def test_node_certificate_chains_to_the_ca(identity, ca):
    files = _files(identity.machine_bootstrap(MachineBootstrap()))
    chain = x509.load_pem_x509_certificates(files[NODE_CERT_PATH].content.encode())
    leaf, issuer = chain
    ca_cert, ca_key = ca[2], ca[3]
    assert issuer == ca_cert
    leaf.verify_directly_issued_by(ca_cert)
    ca_key.public_key().verify(
        leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(leaf.signature_hash_algorithm)
    )
    cn = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert len(cn) == 32 and int(cn, 16) >= 0
    usage = leaf.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and not usage.key_cert_sign
    assert leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    with pytest.raises(x509.ExtensionNotFound):
        leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    now = datetime.now(UTC)
    assert leaf.not_valid_before_utc <= now - timedelta(seconds=290)
    assert leaf.not_valid_after_utc == pytest.approx(
        now + timedelta(days=30), abs=timedelta(minutes=1)
    )


def test_node_key_matches_the_certificate(identity):
    files = _files(identity.machine_bootstrap(MachineBootstrap()))
    key = serialization.load_pem_private_key(files[NODE_KEY_PATH].content.encode(), password=None)
    leaf = x509.load_pem_x509_certificate(files[NODE_CERT_PATH].content.encode())
    der = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert key.public_key().public_bytes(*der) == leaf.public_key().public_bytes(*der)
    assert "BEGIN PRIVATE KEY" in files[NODE_KEY_PATH].content


def test_every_machine_gets_a_fresh_node_id_and_key(identity):
    first = _files(identity.machine_bootstrap(MachineBootstrap()))
    second = _files(identity.machine_bootstrap(MachineBootstrap()))
    assert first[NODE_KEY_PATH].content != second[NODE_KEY_PATH].content
    assert first[NODE_CERT_PATH].content != second[NODE_CERT_PATH].content


def test_agent_config_content(identity):
    config = _files(identity.machine_bootstrap(MachineBootstrap()))[AGENT_CONFIG_PATH].content
    assert 'server_address = "spire.ymir.niuu.world"' in config
    assert 'server_port = "8081"' in config
    assert f'trust_domain = "{TRUST_DOMAIN}"' in config
    assert f'trust_bundle_path = "{TRUST_BUNDLE_PATH}"' in config
    assert 'trust_bundle_format = "pem"' in config
    assert 'socket_path = "/run/spire/agent/api.sock"' in config
    assert 'NodeAttestor "x509pop"' in config
    assert f'private_key_path = "{NODE_KEY_PATH}"' in config
    assert f'certificate_path = "{NODE_CERT_PATH}"' in config
    assert 'WorkloadAttestor "docker"' in config
    assert 'WorkloadAttestor "unix"' in config
    assert "insecure_bootstrap" not in config
    assert "trust_bundle_url" not in config


def test_agent_config_with_bundle_url(ca):
    identity = _make(
        ca,
        Path(),
        trust_bundle_file="",
        trust_bundle_url="https://bundle.ymir.niuu.world/bundle",
        trust_bundle_format="spiffe",
    )
    files = _files(identity.machine_bootstrap(MachineBootstrap()))
    config = files[AGENT_CONFIG_PATH].content
    assert 'trust_bundle_url = "https://bundle.ymir.niuu.world/bundle"' in config
    assert 'trust_bundle_format = "spiffe"' in config
    assert "trust_bundle_path" not in config
    assert TRUST_BUNDLE_PATH not in files


def test_trust_bundle_file_is_shipped_verbatim(identity, spire_bundle):
    files = _files(identity.machine_bootstrap(MachineBootstrap()))
    assert files[TRUST_BUNDLE_PATH].content == spire_bundle.read_text()


def test_systemd_unit_content(ca, spire_bundle):
    identity = _make(ca, spire_bundle, agent_restart_seconds=9)
    unit = _files(identity.machine_bootstrap(MachineBootstrap()))[AGENT_UNIT_PATH].content
    assert f"ExecStart=/opt/spire/bin/spire-agent run -config {AGENT_CONFIG_PATH}" in unit
    assert "mkdir -p /run/spire/agent /var/lib/niuu/spire-agent" in unit
    assert "RestartSec=9" in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit


def test_bootstrap_conflicting_with_existing_file_raises(identity):
    base = MachineBootstrap(files=(BootstrapFile(path=NODE_KEY_PATH, content="x"),))
    with pytest.raises(ValueError, match="already writes"):
        identity.machine_bootstrap(base)


def test_ca_that_expires_before_the_certificate_raises(ca, spire_bundle):
    identity = _make(ca, spire_bundle, node_certificate_ttl_seconds=2 * 365 * 24 * 3600)
    with pytest.raises(ValueError, match="does not cover"):
        identity.machine_bootstrap(MachineBootstrap())


def test_ed25519_ca_signs_node_certificates(tmp_path, spire_bundle):
    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ed ca")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, None)
    )
    cert_file, key_file = tmp_path / "ed.crt", tmp_path / "ed.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    identity = _make((cert_file, key_file), spire_bundle)
    files = _files(identity.machine_bootstrap(MachineBootstrap()))
    leaf = x509.load_pem_x509_certificate(files[NODE_CERT_PATH].content.encode())
    leaf.verify_directly_issued_by(cert)


def test_rsa_ca_signs_node_certificates(tmp_path, spire_bundle):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rsa ca")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "rsa.crt", tmp_path / "rsa.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    identity = _make((cert_file, key_file), spire_bundle)
    leaf = x509.load_pem_x509_certificate(
        _files(identity.machine_bootstrap(MachineBootstrap()))[NODE_CERT_PATH].content.encode()
    )
    key.public_key().verify(
        leaf.signature, leaf.tbs_certificate_bytes, padding.PKCS1v15(), hashes.SHA256()
    )


# -- provider attestation --------------------------------------------------------


def _provider(**kw: Any) -> SpiffeGuestIdentity:
    kwargs: dict[str, Any] = {
        "trust_domain": TRUST_DOMAIN,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_uri": f"{ISSUER}/keys",
        "node_attestation": "provider",
        "parent_id_template": "spiffe://{trust_domain}/spire/agent/harvester/{allocation_id}",
    }
    kwargs.update(kw)
    return SpiffeGuestIdentity(**kwargs)


def test_provider_attestation_adds_nothing_to_the_bootstrap():
    base = MachineBootstrap(commands=(("true",),))
    assert _provider().machine_bootstrap(base) is base


@pytest.mark.asyncio
async def test_provider_parent_comes_from_the_template(fake_kubernetes):
    identity = _provider()
    lease = _lease()
    await identity.register(lease, MachineBootstrap())
    spec = fake_kubernetes.created[0]["spec"]
    assert spec["parentID"] == f"spiffe://{TRUST_DOMAIN}/spire/agent/harvester/{lease.id}"


# -- configuration validation ----------------------------------------------------


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"trust_domain": "Bad Domain"}, "trust_domain"),
        ({"trust_domain": ""}, "trust_domain"),
        ({"issuer": ""}, "issuer"),
        ({"audience": ""}, "audience"),
        ({"jwks_uri": "http://insecure/keys"}, "jwks_uri"),
        ({"jwks_uri": ""}, "jwks_uri"),
        ({"ca_cert_path": "/nonexistent/ca.pem"}, "ca_cert_path"),
        ({"socket_path": "relative/api.sock"}, "socket_path"),
        ({"spire_agent_binary": "/opt/spire bin/spire-agent"}, "spire_agent_binary"),
        ({"spiffe_id_template": "spiffe://{trust_domain}/forge-vm/{pool_id}"}, "exactly once"),
        (
            {"spiffe_id_template": "spiffe://{trust_domain}/{allocation_id}/{allocation_id}"},
            "exactly once",
        ),
        (
            {"spiffe_id_template": "spiffe://{trust_domain}/{pool_id}/{pool_id}/{allocation_id}"},
            "more than once",
        ),
        ({"spiffe_id_template": "spiffe://{trust_domain}/{allocation_id}/{other}"}, "fields"),
        ({"spiffe_id_template": "spiffe://{trust_domain}/{allocation_id:>40}"}, "fields"),
        ({"spiffe_id_template": "spiffe://other.example/{allocation_id}"}, "trust domain"),
        ({"spiffe_id_template": "https://{trust_domain}/{allocation_id}"}, "SPIFFE ID"),
        ({"selector_templates": []}, "non-empty"),
        ({"selector_templates": "docker:label:a:b"}, "non-empty"),
        ({"selector_templates": ["not-a-selector-{allocation_id}"]}, "type:value"),
        ({"selector_templates": ["docker:label:fixed:value"]}, "{allocation_id}"),
        ({"jwt_svid_ttl_seconds": 0}, "jwt_svid_ttl_seconds"),
        ({"node_attestation": "token"}, "node_attestation"),
        ({"parent_id_template": "spiffe://{trust_domain}/x"}, "parent_id_template"),
        ({"server_address": ""}, "server_address"),
        ({"server_port": 0}, "server_port"),
        ({"server_port": 70000}, "server_port"),
        ({"trust_bundle_url": "https://x/bundle"}, "exactly one"),
        ({"trust_bundle_file": ""}, "exactly one"),
        ({"trust_bundle_format": "der"}, "trust_bundle_format"),
        ({"trust_bundle_file": "", "trust_bundle_url": "http://x/b"}, "https"),
        ({"trust_bundle_file": "/nonexistent/bundle.pem"}, "trust_bundle_file"),
        ({"node_ca_cert_file": ""}, "node_ca_cert_file"),
        ({"node_ca_key_file": "/nonexistent.key"}, "node_ca_key_file"),
        ({"node_certificate_ttl_seconds": 0}, "node_certificate_ttl_seconds"),
        ({"node_certificate_backdate_seconds": -1}, "backdate"),
        ({"x509pop_agent_id": "serial"}, "x509pop_agent_id"),
        ({"agent_restart_seconds": 0}, "agent_restart_seconds"),
    ],
)
def test_invalid_configuration_is_fatal(ca, spire_bundle, override, message):
    with pytest.raises(ValueError, match=message):
        _make(ca, spire_bundle, **override)


def test_unknown_kwargs_are_rejected(ca, spire_bundle):
    with pytest.raises(TypeError):
        _make(ca, spire_bundle, trust_domian="typo")


def test_bundle_that_is_not_pem_is_rejected(ca, tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate")
    with pytest.raises(ValueError, match="no PEM certificate"):
        _make(ca, bad)


def test_spiffe_format_bundle_is_validated(ca, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text("{}")
    with pytest.raises(ValueError, match="no keys"):
        _make(ca, bundle, trust_bundle_format="spiffe")
    bundle.write_text("nope")
    with pytest.raises(ValueError, match="JSON"):
        _make(ca, bundle, trust_bundle_format="spiffe")
    bundle.write_text('{"keys": [{"use": "x509-svid"}]}')
    assert _make(ca, bundle, trust_bundle_format="spiffe")


def test_ca_key_must_match_the_ca_certificate(ca, spire_bundle, tmp_path):
    other = tmp_path / "other.key"
    other.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError, match="does not belong"):
        _make((ca[0], other), spire_bundle)


def test_ca_key_must_be_an_unencrypted_pem(ca, spire_bundle, tmp_path):
    junk = tmp_path / "junk.key"
    junk.write_text("junk")
    with pytest.raises(ValueError, match="unencrypted PEM"):
        _make((ca[0], junk), spire_bundle)


def test_ca_certificate_must_be_a_ca(ca, spire_bundle, tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leaf")])
    now = datetime.now(UTC)

    def build(extensions: bool) -> x509.Certificate:
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=1))
        )
        if extensions:
            builder = builder.add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
        return builder.sign(key, hashes.SHA256())

    key_file = tmp_path / "leaf.key"
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    for extensions, message in ((True, "not a CA certificate"), (False, "no basic constraints")):
        cert_file = tmp_path / f"leaf{extensions}.crt"
        cert_file.write_bytes(build(extensions).public_bytes(serialization.Encoding.PEM))
        with pytest.raises(ValueError, match=message):
            _make((cert_file, key_file), spire_bundle)


@pytest.mark.parametrize(
    "override",
    [
        {"server_address": "spire.example"},
        {"trust_bundle_url": "https://x/b"},
        {"node_ca_cert_file": "/x"},
        {"x509pop_agent_id": "fingerprint"},
    ],
)
def test_provider_rejects_x509pop_settings(override):
    with pytest.raises(ValueError, match="only apply to node_attestation='x509pop'"):
        _provider(**override)


def test_provider_requires_a_parent_template():
    with pytest.raises(ValueError, match="requires parent_id_template"):
        _provider(parent_id_template="")


def test_provider_parent_template_must_be_in_the_trust_domain():
    with pytest.raises(ValueError, match="trust domain"):
        _provider(parent_id_template="spiffe://other.example/agent/{allocation_id}")


# -- registration ----------------------------------------------------------------


class FakeApiException(Exception):  # noqa: N818 - mirrors kubernetes_asyncio's name
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class FakeCustomObjects:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.created: list[dict] = []
        self.replaced: list[dict] = []
        self.deleted: list[str] = []
        self.fail: dict[str, int] = {}

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail:
            raise FakeApiException(self.fail[op])

    async def get_cluster_custom_object(self, group, version, plural, name):
        assert (group, version, plural) == ("spire.spiffe.io", "v1alpha1", "clusterstaticentries")
        self._maybe_fail("get")
        if name not in self.store:
            raise FakeApiException(404)
        return self.store[name]

    async def create_cluster_custom_object(self, group, version, plural, body):
        assert (group, version, plural) == ("spire.spiffe.io", "v1alpha1", "clusterstaticentries")
        self._maybe_fail("create")
        self.created.append(body)
        self.store[body["metadata"]["name"]] = {
            **body,
            "metadata": {**body["metadata"], "resourceVersion": "7"},
        }

    async def replace_cluster_custom_object(self, group, version, plural, name, body):
        assert (group, version, plural) == ("spire.spiffe.io", "v1alpha1", "clusterstaticentries")
        self._maybe_fail("replace")
        self.replaced.append(body)
        self.store[name] = body

    async def delete_cluster_custom_object(self, group, version, plural, name):
        assert (group, version, plural) == ("spire.spiffe.io", "v1alpha1", "clusterstaticentries")
        self._maybe_fail("delete")
        if name not in self.store:
            raise FakeApiException(404)
        self.deleted.append(name)
        del self.store[name]


class FakeKubernetes(FakeCustomObjects):
    def __init__(self) -> None:
        super().__init__()
        self.config_calls: list[str] = []
        self.clients_closed = 0
        self.incluster_available = True


@pytest.fixture
def fake_kubernetes(monkeypatch: pytest.MonkeyPatch) -> FakeKubernetes:
    state = FakeKubernetes()

    class ConfigException(Exception):  # noqa: N818 - mirrors kubernetes_asyncio's name
        pass

    class ApiClient:
        async def close(self) -> None:
            state.clients_closed += 1

    def load_incluster_config() -> None:
        state.config_calls.append("incluster")
        if not state.incluster_available:
            raise ConfigException()

    async def load_kube_config() -> None:
        state.config_calls.append("kubeconfig")

    root = types.ModuleType("kubernetes_asyncio")
    client = types.ModuleType("kubernetes_asyncio.client")
    exceptions = types.ModuleType("kubernetes_asyncio.client.exceptions")
    config = types.ModuleType("kubernetes_asyncio.config")
    exceptions.ApiException = FakeApiException
    client.ApiClient = ApiClient
    client.CustomObjectsApi = lambda api_client: state
    client.exceptions = exceptions
    config.ConfigException = ConfigException
    config.load_incluster_config = load_incluster_config
    config.load_kube_config = load_kube_config
    root.client = client
    root.config = config
    for name, module in (
        ("kubernetes_asyncio", root),
        ("kubernetes_asyncio.client", client),
        ("kubernetes_asyncio.client.exceptions", exceptions),
        ("kubernetes_asyncio.config", config),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return state


def _machine(identity: SpiffeGuestIdentity) -> MachineBootstrap:
    return identity.machine_bootstrap(MachineBootstrap())


def _node_cn(machine: MachineBootstrap) -> str:
    cert = x509.load_pem_x509_certificate(_files(machine)[NODE_CERT_PATH].content.encode())
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


@pytest.mark.asyncio
async def test_register_creates_the_entry(identity, fake_kubernetes):
    machine = _machine(identity)
    lease = _lease()
    await identity.register(lease, machine)
    (body,) = fake_kubernetes.created
    assert body["apiVersion"] == "spire.spiffe.io/v1alpha1"
    assert body["kind"] == "ClusterStaticEntry"
    assert body["metadata"]["name"] == f"forge-vm-{lease.id}"
    assert body["metadata"]["labels"] == {
        "app.kubernetes.io/managed-by": "volundr",
        "compute.niuu.io/allocation": str(lease.id),
    }
    assert body["spec"] == {
        "spiffeID": f"spiffe://{TRUST_DOMAIN}/forge-vm/{POOL}/{lease.id}",
        "parentID": f"spiffe://{TRUST_DOMAIN}/spire/agent/x509pop/{_node_cn(machine)}",
        "selectors": [f"docker:label:compute.niuu.io/allocation:{lease.id}"],
    }
    assert fake_kubernetes.config_calls == ["incluster"]


@pytest.mark.asyncio
async def test_registered_spiffe_id_authenticates_back_to_the_allocation(
    identity, svid_key, fake_kubernetes
):
    lease = _lease()
    await identity.register(lease, _machine(identity))
    spiffe_id = fake_kubernetes.created[0]["spec"]["spiffeID"]
    token = _svid(svid_key, sub=spiffe_id)
    assert await identity.authenticate(_request(f"Bearer {token}")) == lease.id


@pytest.mark.asyncio
async def test_register_sets_the_jwt_ttl_and_custom_selectors(ca, spire_bundle, fake_kubernetes):
    identity = _make(
        ca,
        spire_bundle,
        jwt_svid_ttl_seconds=120,
        selector_templates=[
            "docker:label:compute.niuu.io/allocation:{allocation_id}",
            "docker:label:compute.niuu.io/pool:{pool_id}",
        ],
    )
    lease = _lease()
    await identity.register(lease, _machine(identity))
    spec = fake_kubernetes.created[0]["spec"]
    assert spec["jwtSVIDTTL"] == "120s"
    assert spec["selectors"] == [
        f"docker:label:compute.niuu.io/allocation:{lease.id}",
        f"docker:label:compute.niuu.io/pool:{POOL}",
    ]


@pytest.mark.asyncio
async def test_register_uses_the_certificate_fingerprint_when_configured(
    ca, spire_bundle, fake_kubernetes
):
    identity = _make(ca, spire_bundle, x509pop_agent_id="fingerprint")
    machine = _machine(identity)
    await identity.register(_lease(), machine)
    leaf = x509.load_pem_x509_certificate(_files(machine)[NODE_CERT_PATH].content.encode())
    fingerprint = hashlib.sha1(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()
    assert fake_kubernetes.created[0]["spec"]["parentID"].endswith(f"/x509pop/{fingerprint}")


@pytest.mark.asyncio
async def test_register_is_idempotent(identity, fake_kubernetes):
    machine, lease = _machine(identity), _lease()
    await identity.register(lease, machine)
    await identity.register(lease, machine)
    assert len(fake_kubernetes.created) == 1
    assert fake_kubernetes.replaced == []


@pytest.mark.asyncio
async def test_register_replaces_a_drifted_entry(identity, fake_kubernetes):
    machine, lease = _machine(identity), _lease()
    await identity.register(lease, machine)
    name = f"forge-vm-{lease.id}"
    fake_kubernetes.store[name]["spec"]["admin"] = True
    await identity.register(lease, machine)
    (replaced,) = fake_kubernetes.replaced
    assert replaced["metadata"]["resourceVersion"] == "7"
    assert "admin" not in replaced["spec"]
    assert fake_kubernetes.store[name]["spec"] == replaced["spec"]


@pytest.mark.asyncio
async def test_register_replaces_an_entry_for_a_new_machine(identity, fake_kubernetes):
    lease = _lease()
    await identity.register(lease, _machine(identity))
    second = _machine(identity)
    await identity.register(lease, second)
    assert fake_kubernetes.replaced[0]["spec"]["parentID"].endswith(_node_cn(second))


@pytest.mark.asyncio
async def test_register_falls_to_kubeconfig_outside_the_cluster(identity, fake_kubernetes):
    fake_kubernetes.incluster_available = False
    await identity.register(_lease(), _machine(identity))
    assert fake_kubernetes.config_calls == ["incluster", "kubeconfig"]


@pytest.mark.asyncio
async def test_register_loads_the_kubernetes_client_once(identity, fake_kubernetes):
    await identity.register(_lease(), _machine(identity))
    await identity.register(_lease(), _machine(identity))
    assert fake_kubernetes.config_calls == ["incluster"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "create", "replace"])
async def test_register_raises_on_kubernetes_errors(identity, fake_kubernetes, operation):
    machine, lease = _machine(identity), _lease()
    if operation == "replace":
        await identity.register(lease, machine)
        fake_kubernetes.store[f"forge-vm-{lease.id}"]["spec"]["admin"] = True
    fake_kubernetes.fail[operation] = 403
    with pytest.raises(SpiffeRegistrationError, match=r"HTTP 403.*clusterstaticentries"):
        await identity.register(lease, machine)


@pytest.mark.asyncio
async def test_register_requires_the_x509pop_machine_bootstrap(identity, fake_kubernetes):
    with pytest.raises(SpiffeRegistrationError, match="without this identity"):
        await identity.register(_lease(), MachineBootstrap())
    assert fake_kubernetes.created == []


@pytest.mark.asyncio
async def test_register_rejects_a_pool_that_cannot_form_a_spiffe_id(identity, fake_kubernetes):
    with pytest.raises(SpiffeRegistrationError, match="valid SPIFFE ID"):
        await identity.register(_lease(pool="bad pool"), _machine(identity))
    assert fake_kubernetes.created == []


@pytest.mark.asyncio
async def test_unregister_deletes_the_entry(identity, fake_kubernetes):
    machine, lease = _machine(identity), _lease()
    await identity.register(lease, machine)
    await identity.unregister(lease, machine)
    assert fake_kubernetes.deleted == [f"forge-vm-{lease.id}"]
    assert fake_kubernetes.store == {}


@pytest.mark.asyncio
async def test_unregister_tolerates_a_missing_entry(identity, fake_kubernetes):
    await identity.unregister(_lease(), MachineBootstrap())
    assert fake_kubernetes.deleted == []


@pytest.mark.asyncio
async def test_unregister_raises_on_other_errors(identity, fake_kubernetes):
    fake_kubernetes.fail["delete"] = 500
    with pytest.raises(SpiffeRegistrationError, match="HTTP 500"):
        await identity.unregister(_lease(), MachineBootstrap())


@pytest.mark.asyncio
async def test_close_releases_the_client_and_allows_reuse(identity, fake_kubernetes):
    await identity.close()
    assert fake_kubernetes.clients_closed == 0
    await identity.register(_lease(), _machine(identity))
    await identity.close()
    assert fake_kubernetes.clients_closed == 1
    await identity.register(_lease(), _machine(identity))
    assert fake_kubernetes.config_calls == ["incluster", "incluster"]


@pytest.mark.asyncio
async def test_template_with_a_trailing_literal_authenticates(ca, spire_bundle, svid_key):
    identity = _make(
        ca,
        spire_bundle,
        svid_key,
        spiffe_id_template="spiffe://{trust_domain}/vm/{allocation_id}/guest",
    )
    allocation = uuid4()
    token = _svid(svid_key, sub=f"spiffe://{TRUST_DOMAIN}/vm/{allocation}/guest")
    assert await identity.authenticate(_request(f"Bearer {token}")) == allocation


def test_unsupported_ca_key_type_is_rejected(ca, spire_bundle, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import x25519

    key_file = tmp_path / "x25519.key"
    key_file.write_bytes(
        x25519.X25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError, match="RSA, ECDSA or Ed25519"):
        _make((ca[0], key_file), spire_bundle)

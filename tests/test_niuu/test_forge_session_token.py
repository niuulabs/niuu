"""Forge session credentials: minting, verification, grants and the key file."""

from __future__ import annotations

import os
import stat
import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from niuu.adapters.workload_identity.key_file import (
    SigningKeyFileError,
    load_or_create_rsa_key_pem,
)
from niuu.domain.services.forge_session_token import (
    FORGE_SESSION_ROLES,
    LAUNCH_ID_CLAIM,
    SESSION_ID_CLAIM,
    ForgeSessionTokenError,
    ForgeSessionTokenService,
    new_launch_id,
)
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
    FORGE_SESSION_TOKEN_USE,
)
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.forge_mcp.credentials import (
    effective_grants,
    grants_from_scopes,
    is_forge_session_token,
    normalize_grants,
    scopes_for_grants,
    token_scopes,
    unverified_claims,
)
from niuu.forge_mcp.models import ForgeMcpGrant
from niuu.ports.workload_identity import WorkloadTokenVerificationError

KEY_BITS = 2048
SESSION = str(uuid.uuid4())
TTL = 30 * 24 * 3600


def _issuer(**config) -> WorkloadIdentityService:
    base = {
        "enabled": True,
        "issuer": "niuu-forge-session",
        "audiences": ["volundr-api"],
        "token_ttl_seconds": 900,
        "key_id": "niuu-forge-session",
    }
    base.update(config)
    return WorkloadIdentityService(SimpleNamespace(**base))


def _service(issuer: WorkloadIdentityService | None = None, ttl: int = TTL):
    return ForgeSessionTokenService(
        issuer or _issuer(), audiences=["volundr-api"], ttl_seconds=ttl, key_source="key_file"
    )


def _mint(service: ForgeSessionTokenService, grants=(), launch_id="launch-1"):
    return service.mint(
        session_id=SESSION,
        session_name="builder",
        owner_id="owner-1",
        tenant_id="tenant-1",
        grants=grants,
        launch_id=launch_id,
    )


class TestMint:
    def test_claims(self) -> None:
        service = _service()
        issued = _mint(service, grants=[ForgeMcpGrant.MESSAGE])
        claims = jwt.decode(issued.token, options={"verify_signature": False})
        assert claims["token_use"] == FORGE_SESSION_TOKEN_USE
        assert claims["sub"] == "owner-1" and claims["tenant_id"] == "tenant-1"
        assert claims[SESSION_ID_CLAIM] == SESSION
        assert claims[LAUNCH_ID_CLAIM] == "launch-1"
        assert claims["scopes"] == [
            FORGE_NOTIFY_SCOPE,
            FORGE_SESSION_READ_SCOPE,
            FORGE_SESSION_MESSAGE_SCOPE,
        ]
        assert claims["resource_access"]["volundr"]["roles"] == list(FORGE_SESSION_ROLES)
        assert claims["iss"] == "niuu-forge-session" and claims["aud"] == ["volundr-api"]
        assert claims["workload_sub"] == f"forge-session:{SESSION}"
        assert issued.scopes == tuple(claims["scopes"])
        assert issued.grants == (ForgeMcpGrant.MESSAGE,)

    def test_ttl_is_the_configured_backstop(self) -> None:
        before = int(time.time())
        issued = _mint(_service(ttl=TTL))
        claims = jwt.decode(issued.token, options={"verify_signature": False})
        assert before + TTL <= claims["exp"] <= int(time.time()) + TTL
        assert issued.expires_at == claims["exp"]

    def test_defaults_only_without_grants(self) -> None:
        issued = _mint(_service())
        assert issued.scopes == (FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE)
        assert issued.grants == ()

    def test_repr_never_contains_the_token(self) -> None:
        issued = _mint(_service())
        assert issued.token not in repr(issued)
        assert "launch-1" in repr(issued)

    @pytest.mark.parametrize(("owner", "launch"), [("", "l"), ("o", "")])
    def test_owner_and_launch_are_required(self, owner, launch) -> None:
        with pytest.raises(ForgeSessionTokenError):
            _service().mint(
                session_id=SESSION,
                session_name="x",
                owner_id=owner,
                tenant_id=None,
                grants=(),
                launch_id=launch,
            )

    def test_unknown_grant_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _mint(_service(), grants=["admin"])

    def test_launch_ids_are_unique(self) -> None:
        assert new_launch_id() != new_launch_id()
        assert len(new_launch_id()) == 32


class TestVerify:
    def test_round_trip(self) -> None:
        service = _service()
        issued = _mint(service, grants=["lifecycle", "message"])
        claims = service.verify(issued.token)
        assert claims.session_id == SESSION and claims.launch_id == "launch-1"
        assert claims.owner_id == "owner-1" and claims.tenant_id == "tenant-1"
        assert claims.grants == {ForgeMcpGrant.MESSAGE, ForgeMcpGrant.LIFECYCLE}
        principal = claims.principal()
        assert principal.user_id == "owner-1"
        assert principal.roles == list(FORGE_SESSION_ROLES)
        assert principal.token_use == FORGE_SESSION_TOKEN_USE
        assert principal.bound_session_id == SESSION
        assert FORGE_SESSION_LIFECYCLE_SCOPE in principal.scopes

    def test_another_key_is_rejected(self) -> None:
        issued = _mint(_service())
        with pytest.raises(ForgeSessionTokenError, match="invalid Forge session token"):
            _service(_issuer()).verify(issued.token)

    def test_expired_is_rejected(self) -> None:
        issuer = _issuer()
        service = ForgeSessionTokenService(
            issuer, audiences=["volundr-api"], ttl_seconds=-60, key_source="key_file"
        )
        issued = _mint(service)
        with pytest.raises(ForgeSessionTokenError, match="expired"):
            service.verify(issued.token)

    def _forge(self, issuer: WorkloadIdentityService, **overrides) -> str:
        pem = issuer.private_key_pem_for_tests()
        now = int(time.time())
        payload = {
            "iss": "niuu-forge-session",
            "aud": ["volundr-api"],
            "sub": "owner-1",
            "iat": now,
            "exp": now + 60,
            "token_use": FORGE_SESSION_TOKEN_USE,
            "scopes": [FORGE_NOTIFY_SCOPE],
            SESSION_ID_CLAIM: SESSION,
            LAUNCH_ID_CLAIM: "l",
        }
        payload.update(overrides)
        payload = {key: value for key, value in payload.items() if value is not None}
        return jwt.encode(payload, pem, algorithm="RS256")

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"token_use": "valkyrie_build"}, "not a Forge session token"),
            ({"scopes": "forge:notify"}, "malformed scopes"),
            ({"scopes": ["forge:session:create"]}, "foreign scopes"),
            ({SESSION_ID_CLAIM: "not-a-uuid"}, "not bound to a session"),
            ({LAUNCH_ID_CLAIM: None}, "lacks its launch"),
            ({"aud": ["someone-else"]}, "invalid Forge session token"),
            ({"iss": "other"}, "invalid Forge session token"),
        ],
    )
    def test_shape_is_enforced(self, overrides, message) -> None:
        issuer = _issuer()
        service = _service(issuer)
        with pytest.raises(ForgeSessionTokenError, match=message):
            service.verify(self._forge(issuer, **overrides))


class TestWorkloadIssuerAdditions:
    def test_ttl_override_and_configured_key(self) -> None:
        issuer = _issuer()
        assert issuer.has_configured_key is False
        stable = WorkloadIdentityService(
            SimpleNamespace(enabled=True, issuer="", audiences=["volundr-api"]),
            signing_key_pem=issuer.private_key_pem_for_tests(),
        )
        assert stable.has_configured_key is True
        assert stable.issuer == "niuu-workload"
        from niuu.domain.models import Principal

        principal = Principal(user_id="u", email="", tenant_id="t", roles=[])
        issued = stable.issue_token(
            principal=principal,
            workload_subject="s",
            workload_name="n",
            audiences=[],
            ttl_seconds=1234,
        )
        claims = stable.verify_token(issued.token)
        assert claims["exp"] - claims["iat"] == 1234

    def test_verify_rejects_garbage(self) -> None:
        with pytest.raises(WorkloadTokenVerificationError):
            _issuer().verify_token("not-a-jwt")

    async def test_exchange_grants_only_build_scopes(self) -> None:
        class Verifier:
            async def verify(self, token: str) -> dict:
                return {"sub": "system:serviceaccount:ns:builder", "iss": "k8s"}

        config = SimpleNamespace(
            enabled=True,
            issuer="",
            audiences=["volundr-api"],
            mappings=[
                SimpleNamespace(
                    verifier="kubernetes",
                    subject="system:serviceaccount:ns:builder",
                    issuer="k8s",
                    owner_id="owner-1",
                    name="b",
                )
            ],
        )
        service = WorkloadIdentityService(config, verifiers={"kubernetes": Verifier()})
        result = await service.exchange(
            "proof", scopes=["forge:session:create", FORGE_SESSION_LIFECYCLE_SCOPE]
        )
        claims = jwt.decode(result.token, options={"verify_signature": False})
        assert claims["scopes"] == ["forge:session:create"]
        assert claims["token_use"] == "valkyrie_build"


class TestGrantsAndScopes:
    def test_scopes_for_grants(self) -> None:
        assert scopes_for_grants([]) == (FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE)
        assert scopes_for_grants(["message", "lifecycle", "message"]) == (
            FORGE_NOTIFY_SCOPE,
            FORGE_SESSION_READ_SCOPE,
            FORGE_SESSION_LIFECYCLE_SCOPE,
            FORGE_SESSION_MESSAGE_SCOPE,
        )

    def test_grants_from_scopes(self) -> None:
        assert grants_from_scopes([FORGE_NOTIFY_SCOPE]) == frozenset()
        assert grants_from_scopes([FORGE_SESSION_MESSAGE_SCOPE]) == {ForgeMcpGrant.MESSAGE}

    def test_normalize(self) -> None:
        assert normalize_grants(["message", ForgeMcpGrant.LIFECYCLE]) == (
            ForgeMcpGrant.LIFECYCLE,
            ForgeMcpGrant.MESSAGE,
        )
        with pytest.raises(ValueError):
            normalize_grants(["root"])

    def test_unverified_claims(self) -> None:
        assert unverified_claims("") is None
        assert unverified_claims("junk") is None
        token = _mint(_service()).token
        assert unverified_claims(token)["token_use"] == FORGE_SESSION_TOKEN_USE
        assert is_forge_session_token(token) is True
        assert is_forge_session_token(jwt.encode({"type": "pat"}, "k" * 32)) is False
        assert token_scopes(None) == () and token_scopes({"scopes": "x"}) == ()

    def test_effective_grants(self) -> None:
        full = _mint(_service(), grants=["message", "lifecycle"]).token
        assert effective_grants(full, None) == {ForgeMcpGrant.MESSAGE, ForgeMcpGrant.LIFECYCLE}
        assert effective_grants(full, ["message"]) == {ForgeMcpGrant.MESSAGE}
        assert effective_grants(full, []) == frozenset()
        narrow = _mint(_service(), grants=["message"]).token
        assert effective_grants(narrow, ["message", "lifecycle"]) == {ForgeMcpGrant.MESSAGE}
        assert effective_grants("", ["message"]) == frozenset()
        assert effective_grants("opaque-token", None) == frozenset()


class TestKeyFile:
    def test_created_once_with_private_modes(self, tmp_path) -> None:
        path = tmp_path / "state" / "key.pem"
        first = load_or_create_rsa_key_pem(path, key_size=KEY_BITS)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert load_or_create_rsa_key_pem(path, key_size=KEY_BITS) == first
        assert "PRIVATE KEY" in first
        assert [p.name for p in path.parent.iterdir()] == ["key.pem"]  # no temp left

    def test_tokens_survive_a_restart(self, tmp_path) -> None:
        path = tmp_path / "key.pem"
        before = _service(_issuer_with(load_or_create_rsa_key_pem(path, key_size=KEY_BITS)))
        issued = _mint(before)
        after = _service(_issuer_with(load_or_create_rsa_key_pem(path, key_size=KEY_BITS)))
        assert after.verify(issued.token).session_id == SESSION

    def test_readable_by_others_is_refused(self, tmp_path) -> None:
        path = tmp_path / "key.pem"
        load_or_create_rsa_key_pem(path, key_size=KEY_BITS)
        path.chmod(0o644)
        with pytest.raises(SigningKeyFileError, match="chmod 600"):
            load_or_create_rsa_key_pem(path, key_size=KEY_BITS)

    def test_corrupt_is_refused_and_kept(self, tmp_path) -> None:
        path = tmp_path / "key.pem"
        path.write_text("garbage")
        path.chmod(0o600)
        with pytest.raises(SigningKeyFileError, match="not a valid PEM"):
            load_or_create_rsa_key_pem(path, key_size=KEY_BITS)
        assert path.read_text() == "garbage"

    def test_non_rsa_is_refused(self, tmp_path) -> None:
        path = tmp_path / "key.pem"
        key = ec.generate_private_key(ec.SECP256R1())
        path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        path.chmod(0o600)
        with pytest.raises(SigningKeyFileError, match="not an RSA"):
            load_or_create_rsa_key_pem(path, key_size=KEY_BITS)

    def test_not_a_regular_file(self, tmp_path) -> None:
        (tmp_path / "key.pem").mkdir()
        with pytest.raises(SigningKeyFileError, match="not a regular file"):
            load_or_create_rsa_key_pem(tmp_path / "key.pem", key_size=KEY_BITS)

    def test_unwritable_directory(self, tmp_path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x")
        with pytest.raises(SigningKeyFileError, match="cannot create the directory"):
            load_or_create_rsa_key_pem(blocker / "key.pem", key_size=KEY_BITS)

    def test_read_only_directory(self, tmp_path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            if os.access(locked, os.W_OK):
                pytest.skip("running with privileges that ignore directory modes")
            with pytest.raises(SigningKeyFileError, match="cannot write"):
                load_or_create_rsa_key_pem(locked / "key.pem", key_size=KEY_BITS)
        finally:
            locked.chmod(0o700)

    def test_losing_the_creation_race_uses_the_winner(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "key.pem"
        winner = load_or_create_rsa_key_pem(tmp_path / "winner.pem", key_size=KEY_BITS)
        real_link = os.link

        def racing_link(src, dst):
            path.write_text(winner)
            path.chmod(0o600)
            return real_link(src, dst)

        monkeypatch.setattr(os, "link", racing_link)
        monkeypatch.setattr(
            "niuu.adapters.workload_identity.key_file.Path.exists", lambda self: False
        )
        assert load_or_create_rsa_key_pem(path, key_size=KEY_BITS) == winner


class TestKeyFileFailures:
    def test_link_failure_is_reported_and_cleaned(self, tmp_path, monkeypatch) -> None:
        def broken_link(src, dst):
            raise PermissionError("denied")

        monkeypatch.setattr(os, "link", broken_link)
        with pytest.raises(SigningKeyFileError, match="cannot write the signing key"):
            load_or_create_rsa_key_pem(tmp_path / "key.pem", key_size=KEY_BITS)
        assert list(tmp_path.iterdir()) == []  # the temporary file is removed

    def test_stat_failure(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "key.pem"
        load_or_create_rsa_key_pem(path, key_size=KEY_BITS)
        real_stat = type(path).stat

        def failing_stat(self, *args, **kwargs):
            if self == path:
                raise PermissionError("denied")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(type(path), "stat", failing_stat)
        monkeypatch.setattr(type(path), "exists", lambda self: True)
        with pytest.raises(SigningKeyFileError, match="cannot read the signing key"):
            load_or_create_rsa_key_pem(path, key_size=KEY_BITS)

    def test_owned_by_someone_else(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "key.pem"
        load_or_create_rsa_key_pem(path, key_size=KEY_BITS)
        monkeypatch.setattr(os, "getuid", lambda: -1)
        with pytest.raises(SigningKeyFileError, match="owned by another user"):
            load_or_create_rsa_key_pem(path, key_size=KEY_BITS)

    def test_binary_garbage(self, tmp_path) -> None:
        path = tmp_path / "key.pem"
        path.write_bytes(b"\xff\xfe\x00garbage")
        path.chmod(0o600)
        with pytest.raises(SigningKeyFileError, match="cannot read the signing key"):
            load_or_create_rsa_key_pem(path, key_size=KEY_BITS)


def _issuer_with(pem: str) -> WorkloadIdentityService:
    return WorkloadIdentityService(
        SimpleNamespace(
            enabled=True,
            issuer="niuu-forge-session",
            audiences=["volundr-api"],
            key_id="niuu-forge-session",
        ),
        signing_key_pem=pem,
    )

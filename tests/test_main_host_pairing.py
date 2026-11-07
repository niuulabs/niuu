"""Composition of the host pairing-code signer: no identity infrastructure in mini mode."""

from __future__ import annotations

import stat

import pytest

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import (
    COMPUTE_HOST_JOIN_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
    credential_allows_route,
)
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.service_runtime import create_workload_identity_service
from volundr.composition_builders import (
    HOST_PAIRING_ISSUER,
    HostPairingSignerUnavailableError,
    _create_host_pairing_signer,
)
from volundr.config import Settings

ADMIN = Principal(user_id="admin", email="a@x", tenant_id="t", roles=[])
ENVOY_IDENTITY = "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"


def _settings(tmp_path, *, hosts: bool = True, **overrides) -> Settings:
    compute = {
        "pool_id": "pool",
        "max_machines": 1,
        "provider": {"adapter": "x.Y"},
    }
    if hosts:
        compute["hosts"] = {
            "join_url": "https://forge.example.test",
            "connectors": [{"adapter": "x.Connector", "binding_id": "direct-ssh"}],
            "reserved_cpu": 1,
            "reserved_memory_mib": 1024,
            "min_free_disk_mib": 1024,
            "pairing_signing_key_file": str(tmp_path / "keys" / "pairing.pem"),
        }
    return Settings(compute=compute, **overrides)


def _code(signer: WorkloadIdentityService) -> str:
    return signer.issue_token(
        principal=ADMIN,
        workload_subject="admin",
        workload_name="forge-host-pairing",
        audiences=["volundr-api"],
        token_use=VALKYRIE_BUILD_TOKEN_USE,
        claims={"scopes": [COMPUTE_HOST_JOIN_SCOPE]},
    ).token


def test_mini_mode_signs_with_a_local_key_file_that_survives_a_restart(tmp_path):
    settings = _settings(tmp_path)
    workload = create_workload_identity_service(settings.workload_identity)
    assert workload.enabled is False

    signer = _create_host_pairing_signer(settings, workload)

    key = tmp_path / "keys" / "pairing.pem"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    code = _code(signer)
    restarted = _create_host_pairing_signer(settings, workload)
    claims = restarted.verify_token(code)
    assert claims["iss"] == HOST_PAIRING_ISSUER
    assert claims["scopes"] == [COMPUTE_HOST_JOIN_SCOPE]


def test_a_configured_workload_identity_key_signs_instead(tmp_path):
    pem = WorkloadIdentityService(type("C", (), {"enabled": True})()).private_key_pem_for_tests()
    settings = _settings(
        tmp_path,
        workload_identity={"enabled": True, "issuer": "niuu", "signing_key_pem": pem},
    )
    workload = create_workload_identity_service(settings.workload_identity)

    assert _create_host_pairing_signer(settings, workload) is workload
    assert not (tmp_path / "keys" / "pairing.pem").exists()


def test_an_authenticating_identity_without_a_workload_key_is_fatal(tmp_path):
    settings = _settings(tmp_path, identity={"adapter": ENVOY_IDENTITY})
    workload = create_workload_identity_service(settings.workload_identity)

    with pytest.raises(HostPairingSignerUnavailableError, match="signing_key_pem"):
        _create_host_pairing_signer(settings, workload)
    assert not (tmp_path / "keys" / "pairing.pem").exists()


def test_an_unusable_key_file_is_fatal_with_the_knob(tmp_path):
    blocker = tmp_path / "keys"
    blocker.write_text("a file where the key directory should be")
    settings = _settings(tmp_path)
    workload = create_workload_identity_service(settings.workload_identity)

    with pytest.raises(HostPairingSignerUnavailableError, match="pairing_signing_key_file"):
        _create_host_pairing_signer(settings, workload)


def test_without_hosts_there_is_nothing_to_sign(tmp_path):
    settings = _settings(tmp_path, hosts=False)
    workload = create_workload_identity_service(settings.workload_identity)

    with pytest.raises(HostPairingSignerUnavailableError, match="not configured"):
        _create_host_pairing_signer(settings, workload)


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/api/v1/forge/compute/hosts/join.sh", True),
        ("POST", "/api/v1/forge/compute/hosts/join", True),
        ("GET", "/api/v1/forge/compute/hosts/joinXsh", False),
        ("POST", "/api/v1/forge/compute/hosts/join.sh", False),
        ("GET", "/api/v1/forge/compute/hosts", False),
        ("POST", "/api/v1/forge/compute/hosts/pairing-codes", False),
    ],
)
def test_the_edge_admits_a_pairing_code_only_on_the_two_host_routes(
    tmp_path, method, path, allowed
):
    settings = _settings(tmp_path)
    signer = _create_host_pairing_signer(
        settings, create_workload_identity_service(settings.workload_identity)
    )

    assert credential_allows_route(_code(signer), method, path) is allowed

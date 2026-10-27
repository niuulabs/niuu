"""OpenShell request rules Völundr checks before sending (no SDK needed)."""

import re

import pytest

from volundr.adapters.outbound import openshell_contract as contract

_PEER = {"name": "ravn-a", "image": "ravn:1", "command": ["ravn"]}


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("tls", None, ""),
        ("tls", " skip ", "skip"),
        ("enforcement", "audit", "audit"),
        ("access", "read-only", "read-only"),
    ],
)
def test_network_modes_use_the_policy_yaml_spelling(field, value, expected):
    assert contract.network_mode(field, value) == expected


def test_endpoint_mode_check_ignores_absent_fields_and_rejects_removed_ones():
    contract.check_network_endpoint_modes({"host": "a.example", "port": 443})
    with pytest.raises(ValueError, match="no longer accepted"):
        contract.check_network_endpoint_modes({"tls": "terminate"})


@pytest.mark.parametrize(
    ("keys", "rejected"),
    [
        (["openshell.ai/managed-by"], True),
        (["sub.openshell.ai/x"], True),
        (["agents.x-k8s.io/pod-name"], True),
        (["app"], False),
        (["ravn.niuu.world/realm", "notopenshell.ai/x"], False),
    ],
)
def test_reserved_pod_metadata_domains(keys, rejected):
    if not rejected:
        contract.check_pod_metadata_keys("labels", keys)
        return
    with pytest.raises(ValueError, match="no gateway allowlist admits them"):
        contract.check_pod_metadata_keys("labels", keys)


@pytest.mark.parametrize(
    ("workloads", "volumes", "message"),
    [
        (["not-a-mapping"], [], "peer workload must be a mapping"),
        ([{**_PEER, "command": [""]}], [], "explicit command"),
        ([{**_PEER, "environment": "A=B"}], [], "environment must be a mapping"),
        ([{**_PEER, "environment": {"A": 1}}], [], "must be a string without NUL"),
        ([{**_PEER, "resources": {"cpu": "1"}}], [], "take only requests and limits"),
        ([_PEER], ["not-a-mapping"], "driver volume must be a mapping"),
        ([_PEER], [{"name": "Bad_Name", "empty_dir": {}}], "must be a DNS-1123 label"),
        ([_PEER], [{"name": "v", "empty_dir": {}}, {"name": "v", "empty_dir": {}}], "twice"),
        ([_PEER], [{"name": "v", "empty_dir": {}, "host_path": "/"}], "rejects: host_path"),
        ([_PEER], [{"name": "v"}], "exactly one of"),
        ([_PEER], [{"name": "v", "persistent_volume_claim": {}}], "needs a claim_name"),
        (
            [_PEER],
            [{"name": "v", "persistent_volume_claim": {"claim_name": "c", "size": "1Gi"}}],
            "rejects: size",
        ),
        (
            [{**_PEER, "volume_mounts": ["not-a-mapping"]}],
            [{"name": "v", "empty_dir": {}}],
            "volume mount must be a mapping",
        ),
        (
            [{**_PEER, "volume_mounts": [{"name": "v", "mount_path": "/d", "mode": "rw"}]}],
            [{"name": "v", "empty_dir": {}}],
            "rejects: mode",
        ),
        (
            [{**_PEER, "volume_mounts": [{"name": "v", "mount_path": "/d", "read_only": False}]}],
            [{"name": "v", "persistent_volume_claim": {"claim_name": "c"}}],
            "read-only volume 'v' with read_only: false",
        ),
        (
            [{**_PEER, "volume_mounts": [{"name": "v", "mount_path": "relative"}]}],
            [{"name": "v", "empty_dir": {}}],
            "must be absolute",
        ),
        (
            [{**_PEER, "volume_mounts": [{"name": "v", "mount_path": "/"}]}],
            [{"name": "v", "empty_dir": {}}],
            "overlaps /.openshell",
        ),
        (
            [
                {
                    **_PEER,
                    "volume_mounts": [
                        {"name": "v", "mount_path": "/d"},
                        {"name": "v", "mount_path": "/d/"},
                    ],
                }
            ],
            [{"name": "v", "empty_dir": {}}],
            "mounts '/d/' twice",
        ),
    ],
)
def test_peer_workloads_are_checked_like_the_driver_checks_them(workloads, volumes, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        contract.check_peer_workloads(workloads, volumes)


def test_a_valid_flock_passes():
    contract.check_peer_workloads(
        [
            {
                **_PEER,
                "environment": {"RAVN_PERSONA": "a"},
                "resources": {"requests": {"cpu": "100m"}, "limits": {"memory": "1Gi"}},
                "volume_mounts": [
                    {"name": "ipc", "mount_path": "/tmp/mesh", "read_only": False},
                    {"name": "data", "mount_path": "/data", "sub_path": "a"},
                ],
            }
        ],
        [
            {"name": "ipc", "empty_dir": {}},
            {"name": "data", "persistent_volume_claim": {"claim_name": "c", "read_only": True}},
        ],
    )


def test_agent_mounts_are_checked_against_the_declared_volumes():
    with pytest.raises(ValueError, match="agent mounts undeclared volume 'missing'"):
        contract.check_volume_mounts("agent", [{"name": "missing", "mount_path": "/d"}], [])

"""Slot ids, addresses, names and guest paths, derived from configuration and lease."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from volundr.adapters.outbound.guest_credential_delivery import AGENT_CONFIG_DIRECTORY
from volundr.adapters.outbound.vm_slot_layout import (
    SESSION_LAUNCH,
    SlotIsolation,
    SlotLayout,
    slot_index,
    state_key,
)
from volundr.domain.compute import ComputeLease, ComputeSlot, SlotLease

LIMITS = {"cpu": 2, "memory_mib": 4096, "pids": 1024}
ALLOCATION = uuid4()


def _layout(slot: int, **config) -> SlotLayout:
    return SlotLayout(
        config=SlotIsolation(**{**LIMITS, **config}), allocation_id=ALLOCATION, slot=slot
    )


def test_limits_must_be_written_down_and_unknown_keys_are_refused():
    with pytest.raises(ValidationError, match="cpu"):
        SlotIsolation(memory_mib=1, pids=1)
    with pytest.raises(ValidationError, match="extra"):
        SlotIsolation(**LIMITS, slots=4)
    with pytest.raises(ValidationError):
        SlotIsolation(**{**LIMITS, "cpu": 0})
    assert SlotIsolation(cpu=None, memory_mib=None, pids=None).slot_capacity == 16384


def test_machine_leases_are_slot_zero_and_slot_leases_name_their_slot():
    machine = ComputeLease(
        id=ALLOCATION, pool_id="pool", profile="small", request_fingerprint="f", slots=4
    )
    binding = ComputeSlot(
        id=uuid4(),
        allocation_id=ALLOCATION,
        slot=2,
        pool_id="pool",
        session_id=uuid4(),
        owner_id="o",
        tenant_id="t",
    )
    lease = SlotLease(allocation=machine, binding=binding)

    assert slot_index(machine) == 0
    assert state_key(machine) == str(ALLOCATION)
    assert slot_index(lease) == 2
    assert state_key(lease) == f"{ALLOCATION}-slot-2"
    assert SlotLayout.for_lease(SlotIsolation(**LIMITS), lease).slot == 2


@pytest.mark.parametrize("slot", [-1, True, "1", 1.0])
def test_a_slot_index_must_be_a_non_negative_integer(slot):
    with pytest.raises(ValueError, match="non-negative integer"):
        slot_index(SimpleNamespace(id=ALLOCATION, slot=slot))


def test_each_slot_gets_its_own_ids_addresses_and_names():
    zero, three = _layout(0), _layout(3)

    assert (zero.uid, three.uid) == (61000, 61003)
    assert (zero.gateway, zero.address) == ("10.249.0.1", "10.249.0.2")
    assert (three.gateway, three.address) == ("10.249.0.13", "10.249.0.14")
    assert (zero.platform_forward_port, three.platform_forward_port) == (18100, 18103)
    assert zero.key == str(ALLOCATION) and three.key == f"{ALLOCATION}-slot-3"
    assert three.name == "niuu-slot-3" and three.holder == "niuu-slot-3-holder"
    assert three.skuld == "niuu-slot-3-skuld" and three.slice == "niuu_slot_3.slice"
    assert three.host_link == "niuuslot3" and len(_layout(9999).host_link) <= 15
    assert three.data_root == "/var/lib/niuu/slots/3" and three.run_root == "/run/niuu/slots/3"
    assert SlotLayout.for_lease(three.config, SimpleNamespace(id=ALLOCATION, slot=3)) == three


@pytest.mark.parametrize(
    ("slot", "config", "message"),
    [
        (1, {"subnet": "10.249.0.0/30"}, "larger subnet"),
        (2, {"uid_base": 2**32 - 3}, "lower uid_base"),
        (2, {"platform_forward_port_base": 65534}, "platform_forward_port_base"),
    ],
)
def test_a_slot_past_a_real_bound_is_refused_with_the_knob(slot, config, message):
    with pytest.raises(ValueError, match=message):
        _layout(slot, **config)


@pytest.mark.parametrize(
    ("path", "placed"),
    [
        ("/var/lib/niuu/session", "/var/lib/niuu/slots/3/session"),
        ("/var/lib/niuu/session/home/x", "/var/lib/niuu/slots/3/session/home/x"),
        ("/etc/niuu/session-launch.json", "/var/lib/niuu/slots/3/etc/session-launch.json"),
        ("/etc/niuu/session-secrets/0", "/var/lib/niuu/slots/3/etc/session-secrets/0"),
        ("/etc/niuu/forge-session", "/var/lib/niuu/slots/3/etc/forge-session"),
        # Root's, never the slot's: the agent's configuration names the slot it serves.
        (AGENT_CONFIG_DIRECTORY, "/etc/niuu/slots/3/guest-agent"),
        ("/run/niuu/session/tokens/sa", "/run/niuu/slots/3/tokens/sa"),
        # The VM's own files, shared by every slot, and look-alikes stay put.
        ("/etc/niuu/forge-session-other", "/etc/niuu/forge-session-other"),
        (
            "/var/run/secrets/niuu-guest-identity/token",
            "/var/run/secrets/niuu-guest-identity/token",
        ),
    ],
)
def test_session_guest_paths_move_into_the_slot(path, placed):
    assert _layout(3).relocate(path) == placed


def test_a_sidecars_group_becomes_the_slots_so_the_session_can_read_what_it_writes():
    layout = _layout(2)
    payload = {
        "environment": {},
        "init_containers": [{"name": "openbao", "user": "100:1000", "mounts": []}],
        "sidecars": [
            {"name": "agent", "user": "0", "mounts": []},
            {"name": "plain", "mounts": []},
        ],
    }

    placed = layout.launch(payload, platform_port=18080)

    # OpenBao renders 0640 for its group; gid 1000 is the VM's login account.
    assert placed["init_containers"][0]["user"] == "100:61002"
    assert [c.get("user") for c in placed["sidecars"]] == ["0", None]
    assert payload["init_containers"][0]["user"] == "100:1000"


def test_launch_moves_guest_sources_but_not_container_paths():
    layout = _layout(1)
    payload = {
        "image": "skuld",
        "environment": {
            "SKULD__HOST": "127.0.0.1",
            "SKULD__VOLUNDR_API_URL": "http://127.0.0.1:18080",
            "JWKS": "http://127.0.0.1:18080/api/v1/tokens/workload/jwks",
            "OTHER": "http://127.0.0.1:9000",
        },
        "directories": [{"path": "/run/niuu/session/volumes/cache", "mode": "0777"}],
        "secret_mounts": [{"source": "/etc/niuu/session-secrets/0", "target": "/run/secrets/a"}],
        "volume_mounts": [
            {"source": "/etc/niuu/forge-session", "target": "/var/run/niuu/forge-session"}
        ],
        "init_containers": [
            {
                "name": "agent",
                "mounts": [{"source": AGENT_CONFIG_DIRECTORY, "target": AGENT_CONFIG_DIRECTORY}],
            }
        ],
        "sidecars": [],
        "token_volumes": [{"directory": "/run/niuu/session/tokens/sa"}],
    }

    placed = layout.launch(payload, platform_port=18080)

    assert payload["environment"]["SKULD__HOST"] == "127.0.0.1"  # the original is untouched
    assert placed["directories"][0]["path"] == "/run/niuu/slots/1/volumes/cache"
    assert placed["secret_mounts"][0] == {
        "source": "/var/lib/niuu/slots/1/etc/session-secrets/0",
        "target": "/run/secrets/a",
    }
    assert placed["volume_mounts"][0]["target"] == "/var/run/niuu/forge-session"
    agent = placed["init_containers"][0]["mounts"][0]
    assert agent == {
        "source": "/etc/niuu/slots/1/guest-agent",
        "target": AGENT_CONFIG_DIRECTORY,
    }
    # The delivery's plan keeps the session's paths; the runtime's file writes move them.
    assert placed["token_volumes"] == payload["token_volumes"]
    assert placed["environment"] == {
        "SKULD__HOST": "0.0.0.0",
        "SKULD__VOLUNDR_API_URL": "http://10.249.0.5:18080",
        "JWKS": "http://10.249.0.5:18080/api/v1/tokens/workload/jwks",
        "OTHER": "http://127.0.0.1:9000",
    }
    assert placed["slot"] == {
        "index": 1,
        "uid": 61001,
        "data_root": "/var/lib/niuu/slots/1/session",
        "marker": f"{ALLOCATION}-slot-1",
        "netns": "niuu-slot-1",
        "holder": "niuu-slot-1-holder",
        "skuld": "niuu-slot-1-skuld",
        "sidecar_prefix": "niuu-slot-1-sidecar-",
        "slice": "niuu_slot_1.slice",
        "labels": [f"compute.niuu.io/allocation={ALLOCATION}", "compute.niuu.io/slot=1"],
        "identity": "/var/lib/niuu/slots/1/identity",
    }


def test_supervisor_and_stop_specs_name_the_same_slot():
    layout = _layout(2, cpu=None)

    spec = layout.supervisor_spec(image="skuld@sha256:x", platform_port=18080)

    assert spec["prefix_length"] == 30 and spec["netns"] == "niuu-slot-2"
    assert (spec["gateway"], spec["address"]) == ("10.249.0.9", "10.249.0.10")
    assert spec["platform_forward_port"] == 18102 and spec["platform_port"] == 18080
    assert spec["limits"] == {"cpu": None, "memory_mib": 4096, "pids": 1024}
    assert spec["image"] == "skuld@sha256:x" and spec["image_user"] == "skuld"
    assert spec["agent_root"] == "/etc/niuu/slots/2"
    assert layout.stop_spec() == {
        "data_root": "/var/lib/niuu/slots/2/session",
        "marker": f"{ALLOCATION}-slot-2",
        "skuld": "niuu-slot-2-skuld",
        "slot_label": "compute.niuu.io/slot=2",
    }
    assert layout.relocate(SESSION_LAUNCH) == "/var/lib/niuu/slots/2/etc/session-launch.json"

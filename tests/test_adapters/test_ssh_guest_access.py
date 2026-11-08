"""SshGuestAccess for registered hosts: pinned to the host's own key, never trusted on first use.

The subprocess is faked the way the SSH runtime tests fake it; nothing here reaches a network.
"""

import asyncio
import json
import logging
import stat
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from volundr.adapters.outbound.ssh_guest_access import (
    HOST_KEY_CONFIG_PATH,
    HOST_KEY_PATH,
    SshGuestAccess,
)
from volundr.domain.compute import (
    BootstrapFile,
    ComputeLease,
    Machine,
    MachineBootstrap,
    MachineState,
)
from volundr.domain.services.host_access_key import HostAccessKey, generate_host_access_key
from volundr.domain.vm_runtime import VmRuntimeUnavailableError


def _openssh_public(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )


@pytest.fixture
def keys() -> HostAccessKey:
    return generate_host_access_key()


def _access(tmp_path: Path, keys: HostAccessKey, **kwargs) -> SshGuestAccess:
    return SshGuestAccess(host_key=keys, data_dir=str(tmp_path / "data"), **kwargs)


@pytest.fixture
def registered(tmp_path, keys) -> SshGuestAccess:
    return _access(tmp_path, keys, bootstrap_delivery="registered", ssh_port=22)


HOST_KEY = _openssh_public(Ed25519PrivateKey.generate())


def _lease(**machine) -> ComputeLease:
    allocation = uuid4()
    fields = {
        "resource_id": "host-1",
        "state": MachineState.RUNNING,
        "addresses": ("192.0.2.20",),
        "host_public_key": HOST_KEY,
        **machine,
    }
    return ComputeLease(
        id=allocation,
        pool_id="pool",
        profile="small",
        request_fingerprint="digest",
        machine=Machine(allocation_id=allocation, **fields),
    )


def _option(argv: list[str], name: str) -> str:
    return next(arg.split("=", 1)[1] for arg in argv if arg.startswith(f"{name}="))


# -- configuration ------------------------------------------------------------------------------


def test_registered_is_a_bootstrap_delivery_and_registered_host_key_its_trust_mode(registered):
    registered.configure_trust(principal="ubuntu", mode="registered_host_key", kwargs={})


@pytest.mark.parametrize("mode", ["provider_identity", "tofu"])
def test_a_registered_access_refuses_the_other_trust_modes(registered, mode):
    with pytest.raises(ValueError, match="conflicts with bootstrap_delivery 'registered'"):
        registered.configure_trust(principal="ubuntu", mode=mode, kwargs={})


@pytest.mark.parametrize("delivery", ["provider", "ssh"])
def test_the_other_accesses_refuse_the_registered_trust_mode(tmp_path, keys, delivery):
    access = _access(tmp_path, keys, bootstrap_delivery=delivery)

    with pytest.raises(ValueError, match="'registered_host_key' conflicts with bootstrap_delivery"):
        access.configure_trust(principal="ubuntu", mode="registered_host_key", kwargs={})


def test_an_unknown_bootstrap_delivery_names_all_three_modes(tmp_path, keys):
    with pytest.raises(ValueError, match="'provider', 'ssh' or 'registered'"):
        _access(tmp_path, keys, bootstrap_delivery="automatic")


# -- the pool's key, from the credential store --------------------------------------------------


def _key_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "host-access" / "id_ed25519"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_stored_key_is_written_where_ssh_reads_it_and_nobody_else_can(tmp_path, keys, caplog):
    with caplog.at_level(logging.INFO):
        access = _access(tmp_path, keys)

    path = _key_path(tmp_path)
    assert access.private_key == str(path.resolve())
    assert path.read_text() == keys.private_key
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert access.public_key == keys.public_key
    public = path.with_name("id_ed25519.pub")
    assert public.read_text() == keys.public_key + "\n"
    assert _mode(public) == 0o600
    assert not list(path.parent.glob(".*"))
    logged = [r.getMessage() for r in caplog.records if "host access" in r.getMessage()]
    assert logged == [f"Forge host access: SSH key {keys.fingerprint} from the credential store"]
    assert "PRIVATE KEY" not in logged[0]


def test_the_store_is_the_truth_a_restart_rewrites_the_file(tmp_path, keys):
    _access(tmp_path, generate_host_access_key())
    path = _key_path(tmp_path)
    path.chmod(0o644)
    public = path.with_name("id_ed25519.pub")
    public.chmod(0o644)
    path.parent.chmod(0o755)

    _access(tmp_path, keys)

    assert path.read_text() == keys.private_key
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert _mode(public) == 0o600


def test_a_public_file_left_by_another_key_is_replaced(tmp_path, keys):
    # ssh offers the public half from <key>.pub: a stale one is refused by every host.
    public = _key_path(tmp_path).with_name("id_ed25519.pub")
    public.parent.mkdir(parents=True)
    public.write_text(generate_host_access_key().public_key + "\n")

    _access(tmp_path, keys)

    assert public.read_text() == keys.public_key + "\n"


def test_a_temporary_file_left_by_a_crash_does_not_stop_the_next_start(tmp_path, keys):
    leftover = _key_path(tmp_path).parent / ".id_ed25519.tmp"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("half a key")

    _access(tmp_path, keys)

    assert not leftover.exists()
    assert _key_path(tmp_path).read_text() == keys.private_key


def test_ssh_is_handed_the_written_key_file(tmp_path, keys):
    access = _access(tmp_path, keys, bootstrap_delivery="ssh")

    argv = access.bootstrap_argv(_lease())

    assert argv[argv.index("-i") + 1] == str(_key_path(tmp_path).resolve())


# -- machine bootstrap --------------------------------------------------------------------------


def test_a_registered_machine_bootstrap_installs_no_key_of_either_side(registered):
    defaults = MachineBootstrap(
        files=(BootstrapFile(path="/etc/motd", content="hi"),),
        commands=(("true",),),
        ssh_authorized_keys=("ssh-ed25519 AAAA-existing",),
    )

    bootstrap = registered.machine_bootstrap(defaults)

    assert bootstrap.files == defaults.files
    assert bootstrap.ssh_authorized_keys == defaults.ssh_authorized_keys
    assert registered.public_key not in bootstrap.ssh_authorized_keys
    assert all(item.path not in {HOST_KEY_PATH, HOST_KEY_PATH + ".pub"} for item in bootstrap.files)
    assert ("systemctl", "restart", "ssh") not in bootstrap.commands


def test_a_registered_machine_bootstrap_still_carries_the_machine_commands(tmp_path, keys):
    access = _access(
        tmp_path,
        keys,
        bootstrap_delivery="registered",
        machine_commands=[["install-thing", "--yes"]],
    )

    bootstrap = access.machine_bootstrap(MachineBootstrap(commands=(("first",),)))

    assert bootstrap.commands == (("first",), ("install-thing", "--yes"))


def test_a_registered_machine_bootstrap_still_refuses_defaults_that_shadow_ssh_files(registered):
    for path in (HOST_KEY_PATH, HOST_KEY_PATH + ".pub", HOST_KEY_CONFIG_PATH):
        defaults = MachineBootstrap(files=(BootstrapFile(path=path, content="x"),))
        with pytest.raises(ValueError, match="conflicts with SSH access files"):
            registered.machine_bootstrap(defaults)


# -- reaching the host --------------------------------------------------------------------------


def test_command_argv_pins_the_machines_registered_key(registered):
    lease = _lease()

    argv = registered.command_argv(lease, MachineBootstrap())

    known = Path(_option(argv, "UserKnownHostsFile"))
    alias = f"niuu-{lease.id}"
    assert known.read_text() == f"{alias} {HOST_KEY}\n"
    assert _option(argv, "HostKeyAlias") == alias
    assert "StrictHostKeyChecking=yes" in argv
    assert "StrictHostKeyChecking=accept-new" not in argv
    assert argv[-1] == "ubuntu@192.0.2.20"


def test_command_argv_trims_the_registered_key_and_repins_when_it_changes(registered):
    lease = _lease(host_public_key=f"  {HOST_KEY}\n")
    known = Path(_option(registered.command_argv(lease, MachineBootstrap()), "UserKnownHostsFile"))
    assert known.read_text() == f"niuu-{lease.id} {HOST_KEY}\n"

    other = _openssh_public(Ed25519PrivateKey.generate())
    rekeyed = lease.model_copy(
        update={"machine": lease.machine.model_copy(update={"host_public_key": other})}
    )
    registered.command_argv(rekeyed, MachineBootstrap())

    assert known.read_text() == f"niuu-{lease.id} {other}\n"


@pytest.mark.parametrize("missing", [None, ""])
def test_command_argv_refuses_a_machine_without_a_registered_key(registered, missing):
    lease = _lease(host_public_key=missing)

    with pytest.raises(VmRuntimeUnavailableError, match="no host key"):
        registered.command_argv(lease, MachineBootstrap())

    assert not (registered.data_dir / str(lease.id) / "known_hosts").exists()


def test_command_argv_still_needs_an_observed_address(registered):
    with pytest.raises(RuntimeError, match="no observed network address"):
        registered.command_argv(_lease(addresses=()), MachineBootstrap())


def test_a_registered_host_is_never_trusted_on_first_use(registered):
    with pytest.raises(VmRuntimeUnavailableError, match="never trusted on first use"):
        registered.bootstrap_argv(_lease())


def test_a_registered_host_always_counts_as_pinned(registered):
    lease = _lease()

    assert not (registered.data_dir / str(lease.id) / "known_hosts").exists()
    assert registered._has_host_pin(lease) is True


def test_the_other_accesses_are_pinned_only_once_a_known_hosts_entry_exists(tmp_path, keys):
    access = _access(tmp_path, keys, bootstrap_delivery="ssh")
    lease = _lease()

    assert access._has_host_pin(lease) is False


def test_the_ssh_port_is_the_machines_own_when_it_has_one(registered):
    argv = registered._base_ssh(_lease(ssh_port=2222), strict="yes")

    assert argv[argv.index("-p") + 1] == "2222"


def test_the_ssh_port_is_the_access_default_when_the_machine_has_none(tmp_path, keys):
    access = _access(tmp_path, keys, bootstrap_delivery="registered", ssh_port=2200)

    argv = access._base_ssh(_lease(ssh_port=None), strict="yes")

    assert argv[argv.index("-p") + 1] == "2200"


def test_a_connector_proxy_command_becomes_a_proxy_command_option(registered):
    lease = _lease(ssh_proxy_command=("tailscale", "nc", "%h", "%p"))

    argv = registered._base_ssh(lease, strict="yes")

    assert "ProxyCommand=tailscale nc %h %p" in argv
    index = argv.index("ProxyCommand=tailscale nc %h %p")
    assert argv[index - 1] == "-o"


def test_a_proxy_command_is_shell_quoted_as_one_argument_per_word(registered):
    lease = _lease(ssh_proxy_command=("relay", "--name", "my host; rm -rf /"))

    argv = registered._base_ssh(lease, strict="yes")

    assert "ProxyCommand=relay --name 'my host; rm -rf /'" in argv


def test_without_a_proxy_command_the_machine_is_dialled_directly(registered):
    argv = registered._base_ssh(_lease(), strict="yes")

    assert not any("ProxyCommand" in arg for arg in argv)


# -- delivering the bootstrap -------------------------------------------------------------------


def _fake_ssh(monkeypatch, returncode=0):
    calls = []

    async def spawn(*argv, **kwargs):
        process = AsyncMock()
        process.returncode = returncode

        async def communicate(data=None):
            calls.append((list(argv), data))
            return None, b""

        process.communicate = communicate
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return calls


async def test_ensure_in_registered_mode_delivers_the_bootstrap_over_pinned_ssh(
    registered, monkeypatch
):
    lease = _lease()
    bootstrap = MachineBootstrap(commands=(("true",),))
    calls = _fake_ssh(monkeypatch)

    await registered.ensure(lease, bootstrap)

    assert len(calls) == 2
    install, check = calls
    assert "StrictHostKeyChecking=yes" in install[0]
    assert "StrictHostKeyChecking=accept-new" not in install[0]
    assert "cloud-init" not in install[0][-1]
    payload = json.loads(install[1])
    assert payload["ssh_user"] == "ubuntu"
    assert payload["commands"] == [["true"]]
    assert payload["digest"]
    assert check[0][-1] == "true" and "StrictHostKeyChecking=yes" in check[0]
    assert registered.bootstrap_verified_marker(lease).read_text() == payload["digest"]


async def test_ensure_in_registered_mode_without_a_registered_key_spawns_nothing(
    registered, monkeypatch
):
    lease = _lease(host_public_key=None)
    calls = _fake_ssh(monkeypatch)

    with pytest.raises(VmRuntimeUnavailableError, match="no host key"):
        await registered.ensure(lease, MachineBootstrap())

    assert calls == []
    assert not registered.bootstrap_verified_marker(lease).exists()


async def test_ensure_in_registered_mode_fails_loudly_when_the_host_is_unreachable(
    registered, monkeypatch
):
    lease = _lease()
    _fake_ssh(monkeypatch, returncode=255)

    with pytest.raises(VmRuntimeUnavailableError, match="pinned identity"):
        await registered.ensure(lease, MachineBootstrap())

    assert not registered.bootstrap_verified_marker(lease).exists()


async def test_ensure_in_provider_mode_still_waits_for_cloud_init(tmp_path, keys, monkeypatch):
    access = _access(tmp_path, keys, bootstrap_delivery="provider")
    bootstrap = access.machine_bootstrap(MachineBootstrap())
    calls = _fake_ssh(monkeypatch)

    await access.ensure(_lease(host_public_key=None), bootstrap)

    assert len(calls) == 1
    assert calls[0][0][-1] == "sudo -n cloud-init status --wait"

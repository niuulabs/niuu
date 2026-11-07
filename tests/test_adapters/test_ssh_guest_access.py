"""SshGuestAccess for registered hosts: pinned to the host's own key, never trusted on first use.

The subprocess is faked the way the SSH runtime tests fake it; nothing here reaches a network.
"""

import asyncio
import base64
import hashlib
import json
import logging
import stat
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.adapters.workload_identity import key_file
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
from volundr.domain.vm_runtime import VmRuntimeUnavailableError


def _openssh_public(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )


@pytest.fixture
def keys(tmp_path: Path) -> tuple[Path, Path]:
    key = Ed25519PrivateKey.generate()
    private, public = tmp_path / "forge-key", tmp_path / "forge-key.pub"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    public.write_text(_openssh_public(key))
    return private, public


def _access(tmp_path: Path, keys, **kwargs) -> SshGuestAccess:
    private, public = keys
    return SshGuestAccess(
        ssh_private_key_file=str(private),
        ssh_public_key_file=str(public),
        data_dir=str(tmp_path / "data"),
        **kwargs,
    )


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


# -- the key Forge generates --------------------------------------------------------------------


def _generated(tmp_path: Path, **kwargs) -> SshGuestAccess:
    return SshGuestAccess(data_dir=str(tmp_path / "data"), **kwargs)


def _host_access(tmp_path: Path) -> Path:
    return tmp_path / "data" / "host-access"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_without_key_files_forge_generates_its_own_key_on_first_start(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        access = _generated(tmp_path)

    private, public = (
        _host_access(tmp_path) / "id_ed25519",
        _host_access(tmp_path) / "id_ed25519.pub",
    )
    assert access.private_key == str(private.resolve())
    assert _mode(_host_access(tmp_path)) == 0o700
    assert _mode(private) == 0o600
    assert _mode(public) == 0o644
    assert access.public_key == public.read_text().strip()
    assert access.public_key.startswith("ssh-ed25519 AAAA")
    assert access.public_key.endswith(" forge-host-access")
    key = serialization.load_ssh_private_key(private.read_bytes(), password=None)
    assert isinstance(key, Ed25519PrivateKey)
    assert private.read_bytes().startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
    blob = base64.b64decode(access.public_key.split()[1])
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    logged = [record.getMessage() for record in caplog.records if "host access" in record.message]
    assert len(logged) == 1
    assert "generated" in logged[0] and fingerprint in logged[0]
    assert "PRIVATE KEY" not in logged[0]
    assert not list(_host_access(tmp_path).glob(".niuu-*"))


def test_the_generated_key_is_reused_across_restarts_and_never_overwritten(tmp_path, caplog):
    first = _generated(tmp_path)
    private = _host_access(tmp_path) / "id_ed25519"
    before = private.read_bytes()

    with caplog.at_level(logging.INFO):
        second = _generated(tmp_path)

    assert second.public_key == first.public_key
    assert private.read_bytes() == before
    assert any("using the SSH key" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("missing", ["id_ed25519", "id_ed25519.pub"])
def test_one_half_of_the_generated_pair_is_refused(tmp_path, missing):
    _generated(tmp_path)
    (_host_access(tmp_path) / missing).unlink()

    with pytest.raises(key_file.SshKeyFileError, match="incomplete") as raised:
        _generated(tmp_path)

    assert str(_host_access(tmp_path) / missing) in str(raised.value)
    assert "every host must then join again" in str(raised.value)
    assert not (_host_access(tmp_path) / missing).exists()


def test_a_generated_private_key_others_can_read_is_refused(tmp_path):
    _generated(tmp_path)
    private = _host_access(tmp_path) / "id_ed25519"
    private.chmod(0o640)

    with pytest.raises(key_file.SshKeyFileError, match="chmod 600") as raised:
        _generated(tmp_path)

    assert str(private) in str(raised.value)


def test_a_public_file_that_is_not_the_private_keys_is_refused(tmp_path):
    _generated(tmp_path)
    public = _host_access(tmp_path) / "id_ed25519.pub"
    public.write_text(_openssh_public(Ed25519PrivateKey.generate()) + " other\n")

    with pytest.raises(key_file.SshKeyFileError, match="does not belong to the private key"):
        _generated(tmp_path)


def test_a_public_file_that_is_unreadable_is_refused(tmp_path):
    _generated(tmp_path)
    public = _host_access(tmp_path) / "id_ed25519.pub"
    public.write_bytes(b"\xff\xfe")

    with pytest.raises(key_file.SshKeyFileError, match="cannot read the SSH public key"):
        _generated(tmp_path)


def test_a_private_key_that_is_not_openssh_is_refused(tmp_path):
    _generated(tmp_path)
    private = _host_access(tmp_path) / "id_ed25519"
    private.write_text("not a key")

    with pytest.raises(key_file.SshKeyFileError, match="not a valid unencrypted OpenSSH"):
        _generated(tmp_path)


def test_a_private_key_that_is_not_ed25519_is_refused(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa

    _generated(tmp_path)
    private = _host_access(tmp_path) / "id_ed25519"
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(
        rsa_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )

    with pytest.raises(key_file.SshKeyFileError, match="not an Ed25519 key"):
        _generated(tmp_path)


def test_a_directory_that_cannot_be_made_is_refused(tmp_path):
    blocker = tmp_path / "data" / "host-access"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("a file where the directory belongs")

    with pytest.raises(key_file.SshKeyFileError, match="cannot use the SSH key directory"):
        _generated(tmp_path)


def test_a_failed_write_of_the_public_file_is_refused_and_leaves_no_temporary_file(
    tmp_path, monkeypatch
):
    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(key_file.os, "replace", refuse)

    with pytest.raises(key_file.SshKeyFileError, match="cannot write the SSH public key"):
        _generated(tmp_path)

    assert not list(_host_access(tmp_path).glob(".niuu-public-*"))


def test_an_unwritable_public_file_directory_is_refused(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(key_file.tempfile, "mkstemp", refuse)

    with pytest.raises(key_file.SshKeyFileError, match="cannot write the SSH private key"):
        _generated(tmp_path)


def test_a_process_that_loses_the_creation_race_adopts_the_winners_key(tmp_path, monkeypatch):
    winner = Ed25519PrivateKey.generate()
    real_write = key_file.write_private_file_once

    def lose_the_race(path, data, *, what):
        # Another process creates the private file between our check and our write.
        real_write(
            path,
            winner.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            ),
            what=what,
        )
        real_write(path, data, what=what)  # ours finds the file and keeps theirs

    monkeypatch.setattr(key_file, "write_private_file_once", lose_the_race)

    access = _generated(tmp_path)

    assert access.public_key.split()[:2] == _openssh_public(winner).split()
    assert _generated(tmp_path).public_key == access.public_key


def test_concurrent_starts_share_one_generated_key(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        accesses = list(pool.map(lambda _: _generated(tmp_path), range(8)))

    assert len({access.public_key for access in accesses}) == 1


def test_a_configured_key_is_still_used_and_logged(tmp_path, keys, caplog):
    with caplog.at_level(logging.INFO):
        access = _access(tmp_path, keys)

    assert access.private_key == str(keys[0].resolve())
    assert access.public_key == keys[1].read_text().strip()
    assert not (_host_access(tmp_path)).exists()
    assert any("configured SSH key" in record.getMessage() for record in caplog.records)


def test_a_configured_key_that_is_missing_is_still_fatal(tmp_path):
    with pytest.raises(FileNotFoundError):
        _generated(
            tmp_path,
            ssh_private_key_file=str(tmp_path / "absent"),
            ssh_public_key_file=str(tmp_path / "absent.pub"),
        )


@pytest.mark.parametrize("given", ["ssh_private_key_file", "ssh_public_key_file"])
def test_one_of_the_two_key_kwargs_alone_is_refused(tmp_path, keys, given):
    kwargs = {given: str(keys[0] if given == "ssh_private_key_file" else keys[1])}

    with pytest.raises(ValueError, match="ssh_private_key_file and ssh_public_key_file"):
        _generated(tmp_path, **kwargs)

    assert not _host_access(tmp_path).exists()


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


def test_a_public_file_that_cannot_be_started_is_refused(tmp_path, monkeypatch):
    real_mkstemp = key_file.tempfile.mkstemp
    calls = []

    def second_one_fails(*args, **kwargs):
        calls.append(kwargs["prefix"])
        if kwargs["prefix"] == ".niuu-public-":
            raise OSError("no inodes")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(key_file.tempfile, "mkstemp", second_one_fails)

    with pytest.raises(key_file.SshKeyFileError, match="cannot write the SSH public key"):
        _generated(tmp_path)

    assert calls == [".niuu-private-", ".niuu-public-"]

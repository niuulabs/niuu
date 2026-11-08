"""A bootstrap built for several host platforms, and what a runtime sees of it."""

from __future__ import annotations

import pytest

from volundr.domain import compute, compute_hosts
from volundr.domain.compute import (
    PLATFORM_BOOTSTRAP_ROOT,
    BootstrapFile,
    HostPlatform,
    MachineBootstrap,
    encode_platform_bootstraps,
    platform_bootstrap,
    refused_platforms,
)

LINUX = HostPlatform.LINUX
DARWIN = HostPlatform.DARWIN
COMMANDS = (("systemctl", "daemon-reload"),)
KEYS = ("ssh-ed25519 AAAA forge",)


def _bootstrap(*files: tuple[str, str], commands=COMMANDS, keys=KEYS) -> MachineBootstrap:
    return MachineBootstrap(
        files=tuple(BootstrapFile(path=path, content=content) for path, content in files),
        commands=commands,
        ssh_authorized_keys=keys,
    )


def test_host_platform_is_defined_in_compute_and_re_exported_from_compute_hosts():
    assert compute_hosts.HostPlatform is compute.HostPlatform
    assert {p.value for p in HostPlatform} == {"linux", "darwin"}


def test_each_platform_gets_back_its_own_files_and_mode():
    built = {
        LINUX: MachineBootstrap(
            files=(BootstrapFile(path="/etc/launch", content="lin", permissions="0644"),),
            commands=COMMANDS,
            ssh_authorized_keys=KEYS,
        ),
        DARWIN: _bootstrap(("/etc/launch", "mac"), ("/etc/extra", "x")),
    }

    encoded = encode_platform_bootstraps(built, {})

    assert all(item.path.startswith(f"{PLATFORM_BOOTSTRAP_ROOT}/") for item in encoded.files)
    linux = platform_bootstrap(encoded, LINUX)
    darwin = platform_bootstrap(encoded, DARWIN)
    assert [(i.path, i.content, i.permissions) for i in linux.files] == [
        ("/etc/launch", "lin", "0644")
    ]
    assert [(i.path, i.content, i.permissions) for i in darwin.files] == [
        ("/etc/launch", "mac", "0600"),
        ("/etc/extra", "x", "0600"),
    ]
    for view in (linux, darwin, encoded):
        assert view.commands == COMMANDS
        assert view.ssh_authorized_keys == KEYS


def test_a_bootstrap_built_for_one_platform_is_returned_as_is():
    plain = _bootstrap(("/etc/launch", "lin"))

    assert platform_bootstrap(plain, LINUX) is plain
    assert platform_bootstrap(plain, DARWIN) is plain
    assert platform_bootstrap(plain, None) is plain


def test_an_encoded_bootstrap_with_no_platform_raises():
    encoded = encode_platform_bootstraps({LINUX: _bootstrap(("/etc/launch", "lin"))}, {})

    with pytest.raises(ValueError, match="built for several host platforms.*records none"):
        platform_bootstrap(encoded, None)


def test_a_platform_that_built_nothing_sees_no_files():
    encoded = encode_platform_bootstraps({LINUX: _bootstrap(("/etc/launch", "lin"))}, {})

    darwin = platform_bootstrap(encoded, DARWIN)

    assert darwin.files == ()
    assert darwin.commands == COMMANDS


def test_refusals_are_recorded_with_their_reasons_and_hidden_from_the_platforms():
    encoded = encode_platform_bootstraps(
        {LINUX: _bootstrap(("/etc/launch", "lin"))}, {DARWIN: "a Mac cannot run an image"}
    )

    assert refused_platforms(encoded) == {DARWIN: "a Mac cannot run an image"}
    assert platform_bootstrap(encoded, DARWIN).files == ()
    assert [i.path for i in platform_bootstrap(encoded, LINUX).files] == ["/etc/launch"]


def test_a_bootstrap_with_no_refusals_reports_none():
    encoded = encode_platform_bootstraps({LINUX: _bootstrap(("/etc/launch", "lin"))}, {})

    assert refused_platforms(encoded) == {}
    assert refused_platforms(MachineBootstrap()) == {}


def test_only_refusals_encode_an_empty_bootstrap_of_no_commands():
    encoded = encode_platform_bootstraps({}, {LINUX: "no", DARWIN: "no either"})

    assert refused_platforms(encoded) == {LINUX: "no", DARWIN: "no either"}
    assert encoded.commands == ()
    assert encoded.ssh_authorized_keys == ()


def test_a_platform_cannot_build_and_refuse():
    with pytest.raises(ValueError, match="cannot both build and refuse"):
        encode_platform_bootstraps({LINUX: _bootstrap()}, {LINUX: "no"})


def test_platforms_must_agree_on_commands():
    built = {
        LINUX: _bootstrap(commands=COMMANDS),
        DARWIN: _bootstrap(commands=(("other",),)),
    }

    with pytest.raises(ValueError, match="disagree on the machine's boot commands"):
        encode_platform_bootstraps(built, {})


def test_platforms_must_agree_on_authorized_keys():
    built = {
        LINUX: _bootstrap(keys=KEYS),
        DARWIN: _bootstrap(keys=("ssh-ed25519 BBBB",)),
    }

    with pytest.raises(ValueError, match="disagree on the machine's boot commands or authorized"):
        encode_platform_bootstraps(built, {})


def test_the_encoding_is_stable_across_build_order():
    one = encode_platform_bootstraps(
        {LINUX: _bootstrap(("/a", "1")), DARWIN: _bootstrap(("/a", "2"))}, {}
    )
    two = encode_platform_bootstraps(
        {DARWIN: _bootstrap(("/a", "2")), LINUX: _bootstrap(("/a", "1"))}, {}
    )

    assert one == two

"""Tests for the live-preview adapters: socket-table scanners and router auth."""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from niuu.domain.services.token_scope import (
    FORGE_SESSION_PREVIEW_SCOPE,
    FORGE_SESSION_TOKEN_USE,
    VALKYRIE_BUILD_TOKEN_USE,
)
from skuld import preview_adapters
from skuld.preview_adapters import (
    LsofSessionPortScanner,
    NoRouterAuth,
    ProcNetListeningPortScanner,
    WorkloadJwtRouterAuth,
    _merge,
)
from skuld.preview_port import ListeningPort, PortScanError, PreviewAuthError

# --------------------------------------------------------------------------- /proc/net


def _v4(address: str) -> str:
    """An IPv4 address as /proc/net/tcp prints it (little-endian hex)."""
    return ipaddress.IPv4Address(address).packed[::-1].hex().upper()


def _v6(address: str) -> str:
    """An IPv6 address as /proc/net/tcp6 prints it (four host-order words)."""
    raw = ipaddress.IPv6Address(address).packed
    return b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4)).hex().upper()


_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode"
)


def _row(index: int, local: str, port: int, *, state: str = "0A", inode: int = 1000) -> str:
    return (
        f"  {index}: {local}:{port:04X} 00000000:0000 {state} 00000000:00000000 "
        f"00:00000000 00000000  1000        0 {inode} 1 0000000000000000 100 0 0 10 0"
    )


def _table(*rows: str) -> str:
    return "\n".join([_HEADER, *rows]) + "\n"


class _Proc:
    """A fake /proc with the tcp tables and this process's descriptor directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "net").mkdir(parents=True)
        self.fd = root / str(os.getpid()) / "fd"
        self.fd.mkdir(parents=True)

    def tcp(self, *rows: str) -> None:
        (self.root / "net" / "tcp").write_text(_table(*rows), encoding="ascii")

    def tcp6(self, *rows: str) -> None:
        (self.root / "net" / "tcp6").write_text(_table(*rows), encoding="ascii")

    def own_socket(self, inode: int, fd: int = 3) -> None:
        (self.fd / str(fd)).symlink_to(f"socket:[{inode}]")


@pytest.fixture
def proc(tmp_path) -> _Proc:
    return _Proc(tmp_path / "proc")


def _scanner(proc: _Proc, **kwargs: Any) -> ProcNetListeningPortScanner:
    return ProcNetListeningPortScanner(proc_root=str(proc.root), **kwargs)


class TestProcNetScanner:
    async def test_listening_sockets_only(self, proc) -> None:
        proc.tcp(
            _row(0, _v4("0.0.0.0"), 3000, state="0A"),
            _row(1, _v4("0.0.0.0"), 3001, state="01"),  # ESTABLISHED
            _row(2, _v4("0.0.0.0"), 3002, state="06"),  # TIME_WAIT
        )

        assert await _scanner(proc).scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_ipv4_wildcard_and_loopback_are_reached_on_127_0_0_1(self, proc) -> None:
        proc.tcp(_row(0, _v4("0.0.0.0"), 3000), _row(1, _v4("127.0.0.1"), 3001))

        assert await _scanner(proc).scan() == {
            ListeningPort(3000, "127.0.0.1"),
            ListeningPort(3001, "127.0.0.1"),
        }

    async def test_other_loopback_addresses_count_as_loopback(self, proc) -> None:
        proc.tcp(_row(0, _v4("127.0.0.2"), 3000))

        assert await _scanner(proc).scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_a_listener_bound_to_a_specific_address_is_skipped(self, proc) -> None:
        proc.tcp(_row(0, _v4("10.0.0.5"), 3000), _row(1, _v4("192.168.1.9"), 3001))
        proc.tcp6(_row(0, _v6("2001:db8::1"), 3002))

        assert await _scanner(proc).scan() == set()

    async def test_ipv6_wildcard_and_loopback_are_reached_on_colon_colon_1(self, proc) -> None:
        proc.tcp()
        proc.tcp6(_row(0, _v6("::"), 4000), _row(1, _v6("::1"), 4001))

        assert await _scanner(proc).scan() == {
            ListeningPort(4000, "::1"),
            ListeningPort(4001, "::1"),
        }

    async def test_v4_mapped_addresses_follow_the_ipv4_rule(self, proc) -> None:
        proc.tcp()
        proc.tcp6(
            _row(0, _v6("::ffff:127.0.0.1"), 4000),
            _row(1, _v6("::ffff:0.0.0.0"), 4001),
            _row(2, _v6("::ffff:10.0.0.5"), 4002),
        )

        assert await _scanner(proc).scan() == {
            ListeningPort(4000, "127.0.0.1"),
            ListeningPort(4001, "127.0.0.1"),
        }

    async def test_a_port_on_both_families_prefers_ipv4_loopback(self, proc) -> None:
        proc.tcp(_row(0, _v4("127.0.0.1"), 5000))
        proc.tcp6(_row(0, _v6("::"), 5000), _row(1, _v6("::"), 5001))

        assert await _scanner(proc).scan() == {
            ListeningPort(5000, "127.0.0.1"),
            ListeningPort(5001, "::1"),
        }

    async def test_skulds_own_sockets_are_excluded_by_inode(self, proc) -> None:
        proc.own_socket(111)
        proc.tcp(
            _row(0, _v4("0.0.0.0"), 8081, inode=111),
            _row(1, _v4("0.0.0.0"), 3000, inode=222),
        )

        assert await _scanner(proc).scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_other_descriptors_and_vanished_ones_are_ignored(self, proc) -> None:
        (proc.fd / "0").symlink_to("pipe:[111]")
        (proc.fd / "1").write_text("not a symlink: readlink fails like a closed fd")
        proc.tcp(_row(0, _v4("0.0.0.0"), 3000, inode=111))

        assert await _scanner(proc).scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_excluded_ports_are_never_offered(self, proc) -> None:
        proc.tcp(_row(0, _v4("0.0.0.0"), 8080), _row(1, _v4("0.0.0.0"), 3000))
        proc.tcp6(_row(0, _v6("::"), 7480))

        scanner = _scanner(proc, excluded_ports=[8080, "7480"])

        assert await scanner.scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_short_and_header_lines_are_skipped(self, proc) -> None:
        (proc.root / "net" / "tcp").write_text(
            _table("garbage", _row(0, _v4("0.0.0.0"), 3000)), encoding="ascii"
        )

        assert await _scanner(proc).scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_missing_tcp6_means_no_ipv6_listeners(self, proc) -> None:
        proc.tcp(_row(0, _v4("0.0.0.0"), 3000))

        assert await _scanner(proc).scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_missing_tcp_is_a_scan_error(self, proc) -> None:
        proc.tcp6(_row(0, _v6("::"), 3000))

        with pytest.raises(PortScanError, match="not a Linux /proc"):
            await _scanner(proc).scan()

    async def test_an_unreadable_table_is_a_scan_error(self, proc) -> None:
        (proc.root / "net" / "tcp").mkdir()  # reading a directory fails with an OSError

        with pytest.raises(PortScanError, match="Cannot read"):
            await _scanner(proc).scan()

    async def test_missing_descriptor_directory_is_a_scan_error(self, proc) -> None:
        proc.tcp()
        (proc.fd).rmdir()

        with pytest.raises(PortScanError, match="Cannot list"):
            await _scanner(proc).scan()

    def test_merge_prefers_ipv4_whichever_family_came_first(self) -> None:
        assert _merge([(80, "::1"), (80, "127.0.0.1")]) == {ListeningPort(80, "127.0.0.1")}
        assert _merge([(80, "127.0.0.1"), (80, "::1")]) == {ListeningPort(80, "127.0.0.1")}


# --------------------------------------------------------------------------- lsof

SESSION_ID = "sess-1"


class _FakeTools:
    """Fake ``ps`` and ``lsof`` executables whose output and exit code the test sets."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        directory.mkdir()
        self.lsof_args = directory / "lsof.args"
        self.ps_calls = directory / "ps.calls"
        self.lsof_out = ""
        self.lsof_exit = 0
        self.lsof_err = ""
        self.ps_tree = ""
        self.ps_env = ""
        self.ps_exit = 0
        self.ps_env_exit = 0

    def install(self) -> tuple[str, str]:
        (self.dir / "lsof.out").write_text(self.lsof_out)
        (self.dir / "lsof.err").write_text(self.lsof_err)
        (self.dir / "ps.tree").write_text(self.ps_tree)
        (self.dir / "ps.env").write_text(self.ps_env)
        lsof = self.dir / "lsof"
        lsof.write_text(
            f"#!/bin/sh\nprintf '%s ' \"$@\" > '{self.lsof_args}'\n"
            f"cat '{self.dir / 'lsof.out'}'\ncat '{self.dir / 'lsof.err'}' >&2\n"
            f"exit {self.lsof_exit}\n"
        )
        ps = self.dir / "ps"
        ps.write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{self.ps_calls}'\n"
            f'if [ "$1" = "-A" ]; then cat \'{self.dir / "ps.tree"}\'; exit {self.ps_exit}; fi\n'
            f"cat '{self.dir / 'ps.env'}'; exit {self.ps_env_exit}\n"
        )
        lsof.chmod(0o755)
        ps.chmod(0o755)
        return str(ps), str(lsof)


@pytest.fixture
def tools(tmp_path) -> _FakeTools:
    return _FakeTools(tmp_path / "bin")


def _lsof_scanner(
    tools: _FakeTools, tmp_path: Path, *, with_proc: bool = True, **kwargs: Any
) -> LsofSessionPortScanner:
    ps, lsof = tools.install()
    proc_root = tmp_path / "proc"
    if with_proc:
        proc_root.mkdir(exist_ok=True)
    return LsofSessionPortScanner(
        session_id=SESSION_ID, ps_path=ps, lsof_path=lsof, proc_root=str(proc_root), **kwargs
    )


def _environ(tmp_path: Path, pid: int, *entries: str) -> None:
    directory = tmp_path / "proc" / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "environ").write_bytes(b"\0".join(e.encode() for e in entries) + b"\0")


def _tree(*edges: tuple[int, int]) -> str:
    """``ps -A -o pid=,ppid=`` output for (pid, ppid) edges."""
    return "".join(f"{pid:>6} {ppid:>6}\n" for pid, ppid in edges)


class TestLsofSessionPortScanner:
    async def test_descendants_of_skuld_count_at_any_depth(self, tools, tmp_path) -> None:
        me = os.getpid()
        tools.lsof_out = "p101\nn*:3000\np102\nn127.0.0.1:3001\np900\nn*:9999\n"
        tools.ps_tree = _tree((100, me), (101, 100), (102, 101), (900, 1))
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == {
            ListeningPort(3000, "127.0.0.1"),
            ListeningPort(3001, "127.0.0.1"),
        }
        assert tools.lsof_args.read_text().split() == [
            "-nP",
            "-iTCP",
            "-sTCP:LISTEN",
            "-Fpn",
        ]

    async def test_malformed_and_cyclic_ps_lines_do_not_derail_the_walk(
        self, tools, tmp_path
    ) -> None:
        me = os.getpid()
        tools.lsof_out = "p101\nn*:3000\n"
        tools.ps_tree = "PID PPID\nnot a line\n" + _tree((100, me), (101, 100), (100, 101))
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_other_processes_without_the_marker_are_not_the_sessions(
        self, tools, tmp_path
    ) -> None:
        tools.lsof_out = "p900\nn*:9999\np901\nn*:9998\n"
        tools.ps_tree = _tree((900, 1), (901, 1))
        _environ(tmp_path, 900, "PATH=/usr/bin", "SKULD__SESSION__ID=another-session")
        _environ(tmp_path, 901, "SKULD__SESSION__ID=sess-10", "X=1")  # prefix, not equal
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == set()

    async def test_a_detached_server_is_found_by_its_environment_marker(
        self, tools, tmp_path
    ) -> None:
        tools.lsof_out = "p900\nn*:5173\np901\nn*:9998\n"
        tools.ps_tree = _tree((900, 1), (901, 1))
        _environ(tmp_path, 900, "PATH=/usr/bin", f"SKULD__SESSION__ID={SESSION_ID}")
        _environ(tmp_path, 901, "PATH=/usr/bin")
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == {ListeningPort(5173, "127.0.0.1")}

    async def test_the_marker_variable_name_is_configurable(self, tools, tmp_path) -> None:
        tools.lsof_out = "p900\nn*:5173\n"
        tools.ps_tree = _tree((900, 1))
        _environ(tmp_path, 900, f"MY_SESSION={SESSION_ID}")
        scanner = _lsof_scanner(tools, tmp_path, session_env_var="MY_SESSION")

        assert await scanner.scan() == {ListeningPort(5173, "127.0.0.1")}

    async def test_a_process_whose_environ_cannot_be_read_is_not_the_sessions(
        self, tools, tmp_path
    ) -> None:
        tools.lsof_out = "p900\nn*:5173\n"
        tools.ps_tree = _tree((900, 1))
        scanner = _lsof_scanner(tools, tmp_path)  # /proc exists but has no 900/environ

        assert await scanner.scan() == set()

    async def test_without_proc_the_marker_comes_from_ps_dash_e(self, tools, tmp_path) -> None:
        tools.lsof_out = "p900\nn*:5173\np901\nn*:9998\np902\nn*:9997\n"
        tools.ps_tree = _tree((900, 1), (901, 1), (902, 1))
        tools.ps_env = (
            f"  900 /usr/bin/node server.js HOME=/Users/x SKULD__SESSION__ID={SESSION_ID} A=b\n"
            "  901 /usr/bin/node other.js SKULD__SESSION__ID=another HOME=/Users/x\n"
            f"  902 /usr/bin/node x.js XSKULD__SESSION__ID={SESSION_ID}\n"
            "garbage line\n"
        )
        scanner = _lsof_scanner(tools, tmp_path, with_proc=False)

        assert await scanner.scan() == {ListeningPort(5173, "127.0.0.1")}
        env_call = [c for c in tools.ps_calls.read_text().splitlines() if "-wwE" in c]
        assert env_call == ["-wwE -o pid=,command= -p 900,901,902"]

    async def test_ps_dash_e_finding_nothing_is_not_an_error(self, tools, tmp_path) -> None:
        tools.lsof_out = "p900\nn*:5173\n"
        tools.ps_tree = _tree((900, 1))
        tools.ps_env_exit = 1
        scanner = _lsof_scanner(tools, tmp_path, with_proc=False)

        assert await scanner.scan() == set()

    async def test_skulds_own_listeners_never_count(self, tools, tmp_path) -> None:
        me = os.getpid()
        tools.lsof_out = f"p{me}\nn*:8081\n"
        tools.ps_tree = ""
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == set()
        assert not tools.ps_calls.exists()  # nothing left to attribute: ps never ran

    async def test_no_listeners_at_all_is_an_empty_scan(self, tools, tmp_path) -> None:
        tools.lsof_exit = 1
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == set()
        assert not tools.ps_calls.exists()

    async def test_address_forms(self, tools, tmp_path) -> None:
        me = os.getpid()
        tools.lsof_out = (
            "nstray:1\n"  # an address line before any pid
            "p101\nn*:3000\nn127.0.0.1:3001\nn[::1]:3002\nn[::]:3003\n"
            "n10.0.0.5:3004\nn[2001:db8::1]:3005\nnhost.example:3006\nn*:notaport\nn\n"
            "n127.0.0.1:3007->127.0.0.1:55000\n"
        )
        tools.ps_tree = _tree((101, me))
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == {
            ListeningPort(3000, "127.0.0.1"),
            ListeningPort(3001, "127.0.0.1"),
            ListeningPort(3002, "::1"),
            ListeningPort(3003, "::1"),
        }

    async def test_a_port_on_both_families_prefers_ipv4_loopback(self, tools, tmp_path) -> None:
        me = os.getpid()
        tools.lsof_out = "p101\nn[::1]:3000\nn127.0.0.1:3000\n"
        tools.ps_tree = _tree((101, me))
        scanner = _lsof_scanner(tools, tmp_path)

        assert await scanner.scan() == {ListeningPort(3000, "127.0.0.1")}

    async def test_lsof_failing_is_a_scan_error_with_its_stderr(self, tools, tmp_path) -> None:
        tools.lsof_exit = 2
        tools.lsof_err = "lsof: WARNING: can't stat() fuse file system"
        scanner = _lsof_scanner(tools, tmp_path)

        with pytest.raises(PortScanError, match=r"lsof exited 2: lsof: WARNING"):
            await scanner.scan()

    async def test_ps_failing_is_a_scan_error(self, tools, tmp_path) -> None:
        tools.lsof_out = "p101\nn*:3000\n"
        tools.ps_exit = 3
        scanner = _lsof_scanner(tools, tmp_path)

        with pytest.raises(PortScanError, match="ps exited 3"):
            await scanner.scan()

    async def test_ps_dash_e_failing_is_a_scan_error(self, tools, tmp_path) -> None:
        tools.lsof_out = "p900\nn*:5173\n"
        tools.ps_tree = _tree((900, 1))
        tools.ps_env_exit = 2
        scanner = _lsof_scanner(tools, tmp_path, with_proc=False)

        with pytest.raises(PortScanError, match="ps exited 2"):
            await scanner.scan()

    async def test_a_missing_lsof_binary_is_a_scan_error(self, tmp_path) -> None:
        scanner = LsofSessionPortScanner(
            session_id=SESSION_ID, lsof_path=str(tmp_path / "no-such-lsof")
        )

        with pytest.raises(PortScanError, match="Cannot run"):
            await scanner.scan()

    async def test_a_missing_ps_binary_is_a_scan_error(self, tools, tmp_path) -> None:
        tools.lsof_out = "p101\nn*:3000\n"
        _, lsof = tools.install()
        scanner = LsofSessionPortScanner(
            session_id=SESSION_ID, lsof_path=lsof, ps_path=str(tmp_path / "no-such-ps")
        )

        with pytest.raises(PortScanError, match="Cannot run"):
            await scanner.scan()

    def test_a_session_id_is_required(self) -> None:
        with pytest.raises(ValueError, match="needs session_id"):
            LsofSessionPortScanner(session_id="")


# --------------------------------------------------------------------------- router auth

ISSUER = "https://forge.niuu.test/api/v1/tokens/workload"
AUDIENCE = "volundr-api"
SESSION = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _SigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


def _auth(signing_key: rsa.RSAPrivateKey, monkeypatch, **kwargs: Any) -> WorkloadJwtRouterAuth:
    auth = WorkloadJwtRouterAuth(
        issuer=ISSUER, audience=AUDIENCE, jwks_uri="https://forge.niuu.test/jwks", **kwargs
    )
    monkeypatch.setattr(
        auth._jwks,
        "get_signing_key_from_jwt",
        lambda token: _SigningKey(signing_key.public_key()),
    )
    return auth


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": [AUDIENCE],
        "sub": "user-1",
        "iat": now,
        "nbf": now - 5,
        "exp": now + 60,
        "token_use": VALKYRIE_BUILD_TOKEN_USE,
        "scopes": [FORGE_SESSION_PREVIEW_SCOPE],
        "workload_session_id": SESSION,
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def _token(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    return jwt.encode(_claims(**overrides), key, algorithm="RS256", headers={"kid": "k"})


class TestWorkloadJwtRouterAuth:
    async def test_a_scoped_credential_bound_to_this_session_is_admitted(
        self, signing_key, monkeypatch
    ) -> None:
        auth = _auth(signing_key, monkeypatch)

        await auth.verify(f"Bearer {_token(signing_key)}", SESSION)

    async def test_the_scheme_is_case_insensitive_and_whitespace_trimmed(
        self, signing_key, monkeypatch
    ) -> None:
        auth = _auth(signing_key, monkeypatch)

        await auth.verify(f"bearer   {_token(signing_key)}  ", SESSION)

    @pytest.mark.parametrize(
        "header", ["", "Bearer", "Bearer ", "Bearer    ", "Basic abc", "abc", "Token abc"]
    )
    async def test_missing_or_non_bearer_headers_are_refused(
        self, signing_key, monkeypatch, header
    ) -> None:
        auth = _auth(signing_key, monkeypatch)

        with pytest.raises(PreviewAuthError, match="missing router credential"):
            await auth.verify(header, SESSION)

    async def test_garbage_is_refused(self, signing_key, monkeypatch) -> None:
        auth = WorkloadJwtRouterAuth(issuer=ISSUER, audience=AUDIENCE, jwks_uri="https://x/jwks")

        with pytest.raises(PreviewAuthError, match="invalid router credential"):
            await auth.verify("Bearer not-a-jwt", SESSION)

    @pytest.mark.parametrize(
        ("overrides", "fragment"),
        [
            ({"iss": "https://evil.test"}, "Invalid issuer"),
            ({"aud": ["someone-else"]}, "Audience doesn't match"),
            ({"exp": 1}, "expired"),
            ({"exp": None}, "exp"),
            ({"iat": None}, "iat"),
            ({"sub": None}, "sub"),
        ],
    )
    async def test_invalid_claims_are_refused(
        self, signing_key, monkeypatch, overrides, fragment
    ) -> None:
        auth = _auth(signing_key, monkeypatch)

        with pytest.raises(PreviewAuthError, match="invalid router credential") as raised:
            await auth.verify(f"Bearer {_token(signing_key, **overrides)}", SESSION)
        assert fragment.lower() in str(raised.value).lower()

    async def test_a_token_signed_by_another_key_is_refused(self, signing_key, monkeypatch) -> None:
        auth = _auth(signing_key, monkeypatch)
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        with pytest.raises(PreviewAuthError, match="invalid router credential"):
            await auth.verify(f"Bearer {_token(other)}", SESSION)

    async def test_a_symmetric_algorithm_is_refused(self, signing_key, monkeypatch) -> None:
        auth = _auth(signing_key, monkeypatch)
        forged = jwt.encode(_claims(), "shared-secret-shared-secret-32-bytes!", algorithm="HS256")

        with pytest.raises(PreviewAuthError, match="invalid router credential"):
            await auth.verify(f"Bearer {forged}", SESSION)

    async def test_an_unscoped_token_is_refused(self, signing_key, monkeypatch) -> None:
        auth = _auth(signing_key, monkeypatch)
        token = _token(signing_key, token_use=None, scopes=None)

        with pytest.raises(PreviewAuthError, match="not a scoped workload credential"):
            await auth.verify(f"Bearer {token}", SESSION)

    async def test_a_forge_session_credential_is_refused(self, signing_key, monkeypatch) -> None:
        auth = _auth(signing_key, monkeypatch)
        token = _token(
            signing_key,
            token_use=FORGE_SESSION_TOKEN_USE,
            scopes=["forge:session:read", FORGE_SESSION_PREVIEW_SCOPE],
        )

        with pytest.raises(PreviewAuthError, match="not a scoped workload credential"):
            await auth.verify(f"Bearer {token}", SESSION)

    @pytest.mark.parametrize("scopes", [[], ["forge:session:create"], ["forge:notify"]])
    async def test_a_credential_without_the_preview_scope_is_refused(
        self, signing_key, monkeypatch, scopes
    ) -> None:
        auth = _auth(signing_key, monkeypatch)
        token = _token(signing_key, scopes=scopes)

        with pytest.raises(PreviewAuthError, match="lacks forge:session:preview"):
            await auth.verify(f"Bearer {token}", SESSION)

    async def test_a_credential_bound_to_another_session_is_refused(
        self, signing_key, monkeypatch
    ) -> None:
        auth = _auth(signing_key, monkeypatch)
        token = _token(signing_key, workload_session_id="ffffffff-0000-0000-0000-000000000000")

        with pytest.raises(PreviewAuthError, match="bound to another session"):
            await auth.verify(f"Bearer {token}", SESSION)

    async def test_a_credential_bound_to_no_session_is_refused(
        self, signing_key, monkeypatch
    ) -> None:
        auth = _auth(signing_key, monkeypatch)
        token = _token(signing_key, workload_session_id=None)

        with pytest.raises(PreviewAuthError, match="bound to another session"):
            await auth.verify(f"Bearer {token}", SESSION)

    async def test_unreachable_jwks_is_refused_and_logged_at_error(
        self, signing_key, monkeypatch, caplog
    ) -> None:
        auth = WorkloadJwtRouterAuth(issuer=ISSUER, audience=AUDIENCE, jwks_uri="https://x/jwks")

        def unreachable(token: str) -> None:
            raise jwt.PyJWKClientConnectionError("connection refused")

        monkeypatch.setattr(auth._jwks, "get_signing_key_from_jwt", unreachable)
        with caplog.at_level(logging.ERROR, logger="skuld.preview_adapters"):
            with pytest.raises(PreviewAuthError, match="keys unavailable"):
                await auth.verify(f"Bearer {_token(signing_key)}", SESSION)

        assert any(
            r.levelno == logging.ERROR and "JWKS unreachable" in r.getMessage()
            for r in caplog.records
        )

    async def test_a_token_naming_an_unknown_key_is_refused_not_a_server_error(
        self, signing_key, monkeypatch
    ) -> None:
        auth = WorkloadJwtRouterAuth(issuer=ISSUER, audience=AUDIENCE, jwks_uri="https://x/jwks")

        def unknown_key(token: str) -> None:
            raise jwt.PyJWKClientError("Unable to find a signing key that matches: 'nope'")

        monkeypatch.setattr(auth._jwks, "get_signing_key_from_jwt", unknown_key)

        with pytest.raises(PreviewAuthError, match="invalid router credential"):
            await auth.verify(f"Bearer {_token(signing_key)}", SESSION)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"issuer": "", "audience": AUDIENCE, "jwks_uri": "https://x/jwks"},
            {"issuer": ISSUER, "audience": "", "jwks_uri": "https://x/jwks"},
            {"issuer": ISSUER, "audience": AUDIENCE, "jwks_uri": ""},
        ],
    )
    def test_the_constructor_requires_issuer_audience_and_jwks_uri(self, kwargs) -> None:
        with pytest.raises(ValueError, match="needs issuer, audience and jwks_uri"):
            WorkloadJwtRouterAuth(**kwargs)

    def test_a_ca_file_builds_the_jwks_ssl_context(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        def fake_context(*, cafile: str) -> str:
            seen["cafile"] = cafile
            return "ctx"

        monkeypatch.setattr(preview_adapters.ssl, "create_default_context", fake_context)
        auth = WorkloadJwtRouterAuth(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_uri="https://x/jwks",
            ca_file="/etc/ssl/forge-ca.pem",
        )

        assert seen == {"cafile": "/etc/ssl/forge-ca.pem"}
        assert auth._jwks.ssl_context == "ctx"


class TestNoRouterAuth:
    async def test_admits_everything(self) -> None:
        await NoRouterAuth().verify("", SESSION)
        await NoRouterAuth().verify("Bearer anything", "any-session")

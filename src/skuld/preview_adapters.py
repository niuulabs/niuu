"""Adapters for live preview: socket-table scanners and router-credential checks."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import ssl
from pathlib import Path

import jwt

from niuu.domain.services.token_scope import (
    FORGE_SESSION_PREVIEW_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
    token_has_scope,
    token_requires_scope_check,
)
from skuld.preview_port import (
    ListeningPort,
    ListeningPortScanner,
    PortScanError,
    PreviewAuthError,
    PreviewRouterAuth,
)

logger = logging.getLogger(__name__)

#: ``st`` column value of a listening socket in ``/proc/net/tcp*``.
_TCP_LISTEN = "0A"
_IPV4_LOOPBACK = "127.0.0.1"
_IPV6_LOOPBACK = "::1"


def _loopback_host(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """The loopback address that reaches a listener bound to *address*, if any.

    A wildcard or loopback IPv4 bind answers on ``127.0.0.1``; an IPv6 wildcard
    or ``::1`` bind on ``::1``. A listener bound to one specific non-loopback
    address cannot be reached on loopback, so it is not offered as a preview.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv4Address):
        return _IPV4_LOOPBACK if address.is_unspecified or address.is_loopback else None
    return _IPV6_LOOPBACK if address.is_unspecified or address.is_loopback else None


def _merge(listeners: list[tuple[int, str]]) -> set[ListeningPort]:
    """One entry per port, preferring IPv4 loopback when both families listen."""
    hosts: dict[int, str] = {}
    for port, host in listeners:
        if hosts.get(port) != _IPV4_LOOPBACK:
            hosts[port] = host
    return {ListeningPort(port=port, host=host) for port, host in hosts.items()}


def _proc_ipv4(hex_address: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(bytes.fromhex(hex_address)[::-1])


def _proc_ipv6(hex_address: str) -> ipaddress.IPv6Address:
    raw = bytes.fromhex(hex_address)
    # /proc prints the address as four host-order 32-bit words.
    return ipaddress.IPv6Address(b"".join(raw[i : i + 4][::-1] for i in range(0, 16, 4)))


class ProcNetListeningPortScanner(ListeningPortScanner):
    """Listening sockets of this network namespace, from ``/proc/net/tcp{,6}``.

    For a session that owns its network namespace (a Kubernetes pod, a session
    VM): every listener in it is the session's, except Skuld's own sockets
    (found through ``/proc/self/fd``) and *excluded_ports* — the pod's other
    infrastructure containers (Envoy, nginx, terminal, editor), whose processes
    Skuld cannot see.
    """

    def __init__(self, *, excluded_ports: list[int] | None = None, proc_root: str = "/proc"):
        self._excluded = frozenset(int(p) for p in excluded_ports or [])
        self._proc = Path(proc_root)

    async def scan(self) -> set[ListeningPort]:
        return await asyncio.to_thread(self._scan)

    def _scan(self) -> set[ListeningPort]:
        own = self._own_socket_inodes()
        listeners: list[tuple[int, str]] = []
        for table, parse in (("tcp", _proc_ipv4), ("tcp6", _proc_ipv6)):
            path = self._proc / "net" / table
            try:
                lines = path.read_text(encoding="ascii").splitlines()[1:]
            except FileNotFoundError:
                if table == "tcp6":
                    continue  # IPv6 disabled in this kernel: no IPv6 listeners
                raise PortScanError(f"{path} does not exist: not a Linux /proc") from None
            except OSError as exc:
                raise PortScanError(f"Cannot read {path}: {exc}") from exc
            for line in lines:
                fields = line.split()
                if len(fields) < 10 or fields[3] != _TCP_LISTEN or fields[9] in own:
                    continue
                hex_address, hex_port = fields[1].split(":")
                port = int(hex_port, 16)
                host = _loopback_host(parse(hex_address))
                if host is not None and port not in self._excluded:
                    listeners.append((port, host))
        return _merge(listeners)

    def _own_socket_inodes(self) -> set[str]:
        fd_dir = self._proc / str(os.getpid()) / "fd"
        try:
            names = os.listdir(fd_dir)
        except OSError as exc:
            raise PortScanError(f"Cannot list {fd_dir}: {exc}") from exc
        inodes: set[str] = set()
        for name in names:
            try:
                target = os.readlink(fd_dir / name)
            except OSError:
                continue  # the descriptor closed between listdir and readlink
            if target.startswith("socket:["):
                inodes.add(target[len("socket:[") : -1])
        return inodes


class LsofSessionPortScanner(ListeningPortScanner):
    """Listening sockets of this session's processes on a shared host, via ``lsof``.

    For a session that shares its host with everything else (mini mode on a
    laptop), a listener is the session's when its process descends from Skuld
    (the agent and whatever it ran) or carries ``<session_env_var>=<session_id>``
    in its environment. The second covers a server the agent started detached
    (``nohup … &``): its shell exits and the OS re-parents it, but it keeps the
    environment Skuld gave the agent. Skuld's own sockets never count. Works on
    macOS and Linux.
    """

    def __init__(
        self,
        *,
        session_id: str,
        session_env_var: str = "SKULD__SESSION__ID",
        ps_path: str = "ps",
        lsof_path: str = "lsof",
        proc_root: str = "/proc",
    ):
        if not session_id:
            raise ValueError(
                "LsofSessionPortScanner needs session_id: the id its agent's processes "
                "carry in their environment"
            )
        self._marker = f"{session_env_var}={session_id}"
        self._ps = ps_path
        self._lsof = lsof_path
        self._proc = Path(proc_root)

    async def scan(self) -> set[ListeningPort]:
        output = await self._run(
            self._lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn", nothing_found_ok=True
        )
        by_pid: dict[int, list[tuple[int, str]]] = {}
        pid: int | None = None
        for line in output.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                pid = int(line[1:])
                continue
            if not line.startswith("n") or pid is None:
                continue
            host_part, _, port_part = line[1:].rpartition(":")
            if not port_part.isdigit():
                continue
            host = self._host(host_part)
            if host is not None:
                by_pid.setdefault(pid, []).append((int(port_part), host))
        by_pid.pop(os.getpid(), None)
        if not by_pid:
            return set()
        descendants = self._descendants(await self._run(self._ps, "-A", "-o", "pid=,ppid="))
        others = sorted(set(by_pid) - descendants)
        marked = await self._marked(others) if others else set()
        return _merge(
            [
                listener
                for owner, listeners in by_pid.items()
                if owner in descendants or owner in marked
                for listener in listeners
            ]
        )

    async def _marked(self, pids: list[int]) -> set[int]:
        """The *pids* whose environment holds this session's marker."""
        if self._proc.is_dir():
            return {pid for pid in pids if self._proc_environ_marked(pid)}
        # macOS: -E appends each process's environment to its command line.
        output = await self._run(
            self._ps,
            "-wwE",
            "-o",
            "pid=,command=",
            "-p",
            ",".join(str(pid) for pid in pids),
            nothing_found_ok=True,
        )
        marked: set[int] = set()
        for line in output.splitlines():
            first, _, rest = line.strip().partition(" ")
            if first.isdigit() and f" {self._marker} " in f" {rest} ":
                marked.add(int(first))
        return marked

    def _proc_environ_marked(self, pid: int) -> bool:
        try:
            raw = (self._proc / str(pid) / "environ").read_bytes()
        except OSError:
            return False  # gone, or another user's process: not this session's
        return self._marker.encode() in raw.split(b"\0")

    @staticmethod
    def _host(bound: str) -> str | None:
        if bound == "*":
            return _IPV4_LOOPBACK  # lsof prints a wildcard bind of either family as "*"
        try:
            return _loopback_host(ipaddress.ip_address(bound.strip("[]")))
        except ValueError:
            return None

    @staticmethod
    def _descendants(ps_output: str) -> set[int]:
        children: dict[int, list[int]] = {}
        for line in ps_output.splitlines():
            parts = line.split()
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                continue
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
        found: set[int] = set()
        stack = list(children.get(os.getpid(), []))
        while stack:
            pid = stack.pop()
            if pid in found:
                continue
            found.add(pid)
            stack.extend(children.get(pid, []))
        return found

    @staticmethod
    async def _run(*argv: str, nothing_found_ok: bool = False) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise PortScanError(f"Cannot run {argv[0]}: {exc}") from exc
        stdout, stderr = await process.communicate()
        # lsof exits 1 when no listed process has a matching socket.
        if process.returncode == 0 or (nothing_found_ok and process.returncode == 1):
            return stdout.decode("utf-8", errors="replace")
        raise PortScanError(
            f"{argv[0]} exited {process.returncode}: "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )


class WorkloadJwtRouterAuth(PreviewRouterAuth):
    """Admit Forge's preview router by its workload credential.

    The router presents a ``valkyrie_build`` workload JWT carrying the
    ``forge:session:preview`` scope and ``workload_session_id`` naming this
    session. The workload-identity exchange never grants that scope, so only
    the issuer in Forge can mint one. The signature is checked against
    *jwks_uri* here, whether or not an Envoy sits in front.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str,
        jwks_cache_seconds: float = 300,
        ca_file: str = "",
    ):
        if not issuer or not audience or not jwks_uri:
            raise ValueError(
                "WorkloadJwtRouterAuth needs issuer, audience and jwks_uri: set them under "
                "preview.router_auth.kwargs (the workload identity issuer Forge signs with)"
            )
        self._issuer = issuer
        self._audience = audience
        context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        self._jwks = jwt.PyJWKClient(
            jwks_uri, cache_keys=True, lifespan=jwks_cache_seconds, ssl_context=context
        )

    async def verify(self, authorization: str, session_id: str) -> None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise PreviewAuthError("missing router credential")
        token = token.strip()
        try:
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWKClientConnectionError as exc:
            logger.error("Preview router JWKS unreachable: %s", exc)
            raise PreviewAuthError("router credential keys unavailable") from exc
        except jwt.PyJWKClientError as exc:
            # A token naming a key the issuer does not publish: not the router's.
            raise PreviewAuthError(f"invalid router credential: {exc}") from exc
        except jwt.InvalidTokenError as exc:
            raise PreviewAuthError(f"invalid router credential: {exc}") from exc
        if claims.get("token_use") != VALKYRIE_BUILD_TOKEN_USE or not token_requires_scope_check(
            claims
        ):
            raise PreviewAuthError("router credential is not a scoped workload credential")
        if not token_has_scope(token, FORGE_SESSION_PREVIEW_SCOPE):
            raise PreviewAuthError(f"router credential lacks {FORGE_SESSION_PREVIEW_SCOPE}")
        if claims.get("workload_session_id") != session_id:
            raise PreviewAuthError("router credential is bound to another session")


class NoRouterAuth(PreviewRouterAuth):
    """Admit every preview request: for a host with no identity provider at all.

    Only a single-user local host whose platform runs without authentication
    (mini mode, ``host_auth.mode: none``) may configure this, where the browser
    reaches this broker only through the local root app, whose ``/s/{id}`` proxy
    never forwards to ``/preview``.
    """

    async def verify(self, authorization: str, session_id: str) -> None:
        del authorization, session_id

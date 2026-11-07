"""Run a session directly on a Mac registered as a host, as a fresh macOS user.

A Mac is an exclusive one-slot host: macOS has no network namespaces or cgroups, so the
only boundary is a user. Each session gets ``niuu-slot-0`` (uid ``uid_base``), created
when it starts and deleted, provably, when it stops; the darwin audit
(``host_checks.DARWIN_AUDIT_SH``) is what ``down`` checks before the Mac is offered again.

Everything the Linux runtimes take from the Skuld image comes from the **session bundle**
(``containers/skuld-darwin``): a relocatable CPython with niuu, Node and the agent CLIs,
OpenBao's agent, built on macOS in CI and pinned by digest like the image. Forge fetches
it onto the Mac once per digest and the session sees it read-only.

What the Linux runtimes run as containers runs natively:

* the session is the launchd job ``world.niuu.slot.0`` in the system domain, which drops
  to the slot's user and runs Skuld with the session's environment;
* the pod's init containers run once, as root, before it, and its sidecars (the guest
  credential agent, OpenBao's agent) are launchd jobs of their own, as root, next to it.
  Only containers whose image is the bundle itself (the credential delivery's agent) or
  listed in ``native_images`` (whose commands the bundle carries, such as OpenBao's) are
  accepted; anything else is refused when the session is created;
* Skuld's calls back to Forge use the same SSH reverse tunnel as on a VM.

Without mount namespaces nothing can appear at the paths a container would see, so the
runtime translates them: every mount target in the environment, command and arguments of
the session and its agents, and in the guest agent's configuration, becomes the place
the mount's source has on the Mac (``/home/skuld`` is the slot's home, ``/workspace``
its workspace). Secret files the session expects in its home or workspace become links
there; the few tools that read a fixed path outside them (a workload token under
``/var/run/secrets``) get a link of root's at that path, removed at stop.

What a session can see differs from Linux in one way: its own credential files (its
ServiceAccount tokens, the guest agent's configuration) are readable by its group, where
Linux hides them behind a mount; they are its own and nobody else's.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from volundr.adapters.outbound import darwin_guest_programs as programs
from volundr.adapters.outbound.host_checks import DARWIN_AUDIT_SH
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.adapters.outbound.vm_slot_layout import (
    FORGE_SESSION_DIRECTORY,
    GUEST_AGENT_DIRECTORY,
    SESSION_DATA,
    SESSION_LAUNCH,
    SESSION_RUNTIME,
    SESSION_SECRETS,
    SLOT_DATA_BASE,
    slot_index,
)
from volundr.domain.compute import AllocationRef, BootstrapFile, MachineBootstrap, SlotLease
from volundr.domain.compute_hosts import HostPlatform
from volundr.domain.models import Session, SessionSpec
from volundr.domain.session_runtimes import SessionRuntimeKind
from volundr.domain.vm_runtime import SessionImages, SlotLimits

#: Where bundles are unpacked on the Mac, one directory per digest.
BUNDLE_CACHE = "/var/lib/niuu/host-bundles"
#: The bundle's layer in its OCI artifact (the ``darwin-bundle`` jobs of ``.github/workflows/dev.yaml``).
BUNDLE_MEDIA_TYPE = "application/vnd.niuu.skuld-darwin.bundle.v1.tar+gzip"
#: Root-owned per slot: what root and Forge's agents write for the session.
SLOT_RUNTIME_BASE = "/var/run/niuu/slots"
#: Root-only per slot: the session job's spec, the agents' homes, the guest agent's config.
SLOT_STATE_BASE = "/var/run/niuu/host-sessions"
#: Root-only logs of Skuld and the agents, kept after the slot is gone.
SLOT_LOG_BASE = "/var/log/niuu/host-sessions"
SESSION_LABEL = "world.niuu.slot"
CONTAINER_HOME = "/home/skuld"
CONTAINER_WORKSPACE = "/workspace"
#: Where OpenBao's agent renders a session's secrets, as the session sees them on Linux.
SECRETS_TARGET = "/run/secrets"
_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_BUNDLE = re.compile(r"[^/\s]+/[^@\s]+@sha256:([a-f0-9]{64})")
#: Session mount targets a tool reads at a fixed place outside the session's storage: root
#: links them to their source (one session per Mac, so the place is the session's alone).
_LINKED_TARGET_PREFIXES = ("/var/run/", "/private/var/run/")

MAC_HINT = (
    "You are running directly on a Mac (macOS) as the unprivileged user {user}, not in a "
    "container; sudo and Homebrew are not available."
)
MAC_NIX_HINT = (
    "Install software with Nix: `nix profile install nixpkgs#<package>` keeps it on your "
    "PATH, `nix shell nixpkgs#<package> -c <command>` uses it once, and "
    "`nix search nixpkgs <name>` finds packages. Language tools install into your home as "
    "usual (npm prefixes, `uv tool`, `go install`, `pip --user`)."
)


def translate(text: str, places: dict[str, str]) -> str:
    """``text`` with every path at or below a key of ``places`` moved to its value.

    A key matches only as a whole path: at the start of the text or after a character
    that cannot be part of a path, and followed by ``/``, the end or such a character, so
    ``/workspace`` in ``https://x/workspace`` stays. One pass, longest key first, so a
    translated path is never translated again.
    """
    if not places:
        return text
    keys = sorted(places, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w.\-/~])(" + "|".join(re.escape(key) for key in keys) + r")(?=$|/|[^\w.\-])"
    )
    return pattern.sub(lambda match: places[match.group(1)], text)


@dataclass(frozen=True)
class DarwinSlotLayout:
    """The one slot of a Mac, at the paths the darwin audit checks."""

    allocation_id: UUID
    slot: int
    uid_base: int

    def __post_init__(self) -> None:
        if self.slot != 0:
            raise ValueError(
                f"A Mac runs one session at a time, but slot {self.slot} was asked for; "
                "set the host's slots to 1"
            )

    @property
    def name(self) -> str:
        return f"niuu-slot-{self.slot}"

    @property
    def uid(self) -> int:
        return self.uid_base + self.slot

    @property
    def key(self) -> str:
        # Slot 0 keeps the allocation's own key (vm_slot_layout.state_key).
        return str(self.allocation_id)

    @property
    def label(self) -> str:
        return f"{SESSION_LABEL}.{self.slot}"

    @property
    def data_root(self) -> str:
        return f"{SLOT_DATA_BASE}/{self.slot}"

    @property
    def session_data(self) -> str:
        return f"{self.data_root}/session"

    @property
    def home(self) -> str:
        return f"{self.session_data}/home"

    @property
    def workspace(self) -> str:
        return f"{self.session_data}/workspace"

    @property
    def tmp(self) -> str:
        return f"{self.data_root}/tmp"

    @property
    def run_root(self) -> str:
        return f"{SLOT_RUNTIME_BASE}/{self.slot}"

    @property
    def state_root(self) -> str:
        return f"{SLOT_STATE_BASE}/{self.slot}"

    @property
    def log_root(self) -> str:
        return f"{SLOT_LOG_BASE}/{self.slot}"

    def _roots(self) -> tuple[tuple[str, str], ...]:
        return (
            (SESSION_DATA, self.session_data),
            (SESSION_LAUNCH, f"{self.run_root}/session-launch.json"),
            (SESSION_SECRETS, f"{self.run_root}/session-secrets"),
            (FORGE_SESSION_DIRECTORY, f"{self.run_root}/forge-session"),
            # Out of the slot's reach: a slot that could rewrite the agent's configuration
            # could make it fetch for another allocation.
            (GUEST_AGENT_DIRECTORY, f"{self.state_root}/guest-agent"),
            (SESSION_RUNTIME, f"{self.run_root}/run"),
        )

    def relocate(self, path: str) -> str:
        """The slot's place for a session guest path; other paths are the Mac's own."""
        candidate = PurePosixPath(path)
        for legacy, slot in self._roots():
            root = PurePosixPath(legacy)
            if candidate == root or root in candidate.parents:
                return str(PurePosixPath(slot) / candidate.relative_to(root))
        return path

    def launch(self, payload: dict, *, platform_port: int) -> dict:
        """The payload as the shared start hands it over; ``_place`` gives it its places."""
        return payload

    def relocations(self) -> dict[str, str]:
        return dict(self._roots())

    def session_places(self) -> dict[str, str]:
        """Where the container paths of the session's own storage are on the Mac."""
        return {CONTAINER_HOME: self.home, CONTAINER_WORKSPACE: self.workspace}

    def spec(self, *, stop_timeout: float, poll: float, links=(), metadata_address=None):
        """The slot as ``darwin_guest_programs.SLOT`` creates and removes it."""
        return {
            "slot": self.slot,
            "name": self.name,
            "uid": self.uid,
            "uid_base": self.uid_base,
            "data_root": self.data_root,
            "session_data": self.session_data,
            "home": self.home,
            "tmp": self.tmp,
            "run_root": self.run_root,
            "state_root": self.state_root,
            "labels": [self.label],
            "links": list(links),
            "metadata_address": metadata_address,
            "stop_timeout": stop_timeout,
            "poll": poll,
            "audit": DARWIN_AUDIT_SH,
        }


def _group_mode(mode: str) -> str:
    """A directory's mode with the world's access moved to the slot's group alone."""
    value = int(mode, 8)
    group = (value >> 3 | value) & 0o7
    return f"0{(value & 0o700) | group << 3:o}"


class SshDarwinHostRuntime(SshContainerVmRuntime):
    """Run each session on a Mac as a fresh macOS user, from the pinned session bundle.

    ``skuld_bundle`` is the digest-pinned bundle (``ghcr.io/niuulabs/skuld-darwin@sha256:…``),
    which replaces the Linux runtimes' ``skuld_image``. ``native_images`` names pod images
    whose containers run natively from the bundle's commands instead (OpenBao's agent
    image, whose ``bao`` the bundle carries). ``uid_base`` is the slot user's uid, free on
    the Mac (macOS gives its own users 501 and up, Nix's build users 351 and up).

    ``nix`` says the host recipe installs a multi-user Nix: the session gets Nix's profiles
    on its PATH and the agent is told how to install with it. A session is refused when
    the daemon is missing.
    """

    _runs_pod_containers = True

    def __init__(
        self,
        *,
        skuld_bundle: str | None = None,
        session_images: SessionImages | None = None,
        native_images: list[str] | None = None,
        uid_base: int = 61000,
        nix: bool = True,
        session_start_timeout_seconds: float = 30,
        **kwargs,
    ):
        if "slot_isolation" in kwargs:
            raise ValueError(
                "A Mac runs one session at a time and its boundary is the slot user; remove "
                "slot_isolation (set uid_base instead)"
            )
        if "skuld_image" in kwargs:
            raise ValueError(
                "Macs run Skuld from the session bundle, not an image; set skuld_bundle"
            )
        if session_images is not None and session_images.darwin:
            if skuld_bundle is not None:
                raise ValueError(
                    "skuld_bundle is set and Forge hands this runtime the release's session "
                    "bundle too; remove skuld_bundle to run the release's"
                )
            skuld_bundle = session_images.darwin
        if skuld_bundle is None:
            raise ValueError(
                "SshDarwinHostRuntime needs a session bundle: set skuld_bundle, or "
                "compute.session_images.darwin for the built-in host execution"
            )
        match = _BUNDLE.fullmatch(skuld_bundle)
        if match is None:
            raise ValueError(
                "skuld_bundle must be a digest-pinned reference, "
                "ghcr.io/niuulabs/skuld-darwin@sha256:<digest>"
            )
        if not 1000 <= uid_base <= 2**31 - 2:
            raise ValueError("uid_base must lie between 1000 and 2147483646")
        if session_start_timeout_seconds <= 0:
            raise ValueError("session_start_timeout_seconds must be positive")
        if any(not image or _BUNDLE.fullmatch(image) is None for image in native_images or ()):
            raise ValueError("native_images must be digest-pinned image references")
        super().__init__(skuld_image=skuld_bundle, **kwargs)
        self._bundle_digest = match.group(1)
        self._bundle_root = f"{BUNDLE_CACHE}/{self._bundle_digest}"
        self._native_images = frozenset(native_images or ())
        self._uid_base = uid_base
        self._nix = nix
        self._session_start_timeout = session_start_timeout_seconds

    @property
    def session_runtime(self) -> SessionRuntimeKind | None:
        return SessionRuntimeKind.HOST

    @property
    def requires_root_daemon(self) -> bool:
        return False

    @property
    def host_platforms(self) -> frozenset[HostPlatform]:
        return frozenset({HostPlatform.DARWIN})

    @property
    def supports_slots(self) -> bool:
        # One session per Mac: nothing but a user keeps two sessions apart there.
        return False

    @property
    def supports_reuse(self) -> bool:
        """``stop`` deletes the slot user and fails unless the darwin audit finds nothing."""
        return True

    async def recycle(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """Audit the slot again before the Mac takes its next session; any finding raises."""
        await self._audit_slot(lease, bootstrap, DARWIN_AUDIT_SH, self._layout(lease).slot)

    @property
    def slot_limits(self) -> SlotLimits | None:
        # No cgroups: the session has the Mac's CPUs and memory. The uid is what the audit
        # checks for leftovers.
        return SlotLimits(uid_base=self._uid_base, cpu=None, memory_mib=None, pids=None)

    def _layout(self, lease: AllocationRef) -> DarwinSlotLayout:  # type: ignore[override]
        return DarwinSlotLayout(
            allocation_id=lease.id, slot=slot_index(lease), uid_base=self._uid_base
        )

    def _python(self, program: str, *args: str) -> str:  # type: ignore[override]
        """A guest program under the bundle's Python, as root; isolated from the Mac's."""
        _, *program_args = shlex.split(
            SshGuestAccess.python_command(program, *args, principal="user")
        )
        return shlex.join(["sudo", "-n", self._python_binary, "-I", *program_args])

    @property
    def _python_binary(self) -> str:
        return f"{self._bundle_root}/python/bin/python3"

    def _native_path(self) -> str:
        bundle = self._bundle_root
        return f"{bundle}/libexec:{bundle}/bin:{bundle}/fallback:{_SYSTEM_PATH}"

    def _session_path(self) -> str:
        bundle = self._bundle_root
        nix = [f"{CONTAINER_HOME}/.nix-profile/bin", "/nix/var/nix/profiles/default/bin"]
        return ":".join(
            [f"{bundle}/bin", *(nix if self._nix else []), _SYSTEM_PATH, f"{bundle}/fallback"]
        )

    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        placed = super().session_bootstrap(session, spec, machine)
        files = []
        for item in placed.files:
            if item.path == SESSION_LAUNCH:
                launch = json.loads(item.content)
                self._require_native(launch)
                launch["environment"] = self._mac_environment(session, launch["environment"])
                item = BootstrapFile(
                    path=item.path, content=json.dumps(launch), permissions=item.permissions
                )
            files.append(item)
        return placed.model_copy(update={"files": tuple(files)})

    def _require_native(self, launch: dict) -> None:
        """Refuse a container the Mac cannot run natively, before anything is stored."""
        allowed = {self._image, *self._native_images}
        for container in (*launch["init_containers"], *launch["sidecars"]):
            if container["image"] not in allowed:
                raise ValueError(
                    f"Container {container['name']!r} runs image {container['image']}, which "
                    "a Mac cannot run; list it in compute.runtime.kwargs.native_images if "
                    "the session bundle carries its commands, or run this session on Linux"
                )

    def _mac_environment(self, session: Session, environment: dict[str, str]) -> dict[str, str]:
        """The container session's environment, for a session on the Mac itself."""
        updated = {
            **environment,
            "PATH": self._session_path(),
            "SHELL": "/bin/zsh",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "NIUU_PYTHON": self._python_binary,
            "NIUU_SKULD": f"{self._bundle_root}/libexec/skuld",
        }
        if self._nix:
            updated.update(
                {
                    "NIX_REMOTE": "daemon",
                    "NIX_PROFILES": f"/nix/var/nix/profiles/default {CONTAINER_HOME}/.nix-profile",
                    "NIX_SSL_CERT_FILE": "/etc/ssl/cert.pem",
                }
            )
        if updated.get("SKULD__PREVIEW__ENABLED") == "true":
            # /proc does not exist on macOS: the session's listeners come from lsof.
            updated["SKULD__PREVIEW__PORT_SCANNER"] = json.dumps(
                {
                    "adapter": "skuld.preview_adapters.LsofSessionPortScanner",
                    "kwargs": {
                        "session_id": str(session.id),
                        "ps_path": "/bin/ps",
                        "lsof_path": "/usr/sbin/lsof",
                    },
                }
            )
        hint = " ".join([MAC_HINT, *([MAC_NIX_HINT] if self._nix else [])])
        hint = hint.format(user="{slot_user}")
        prompt = updated.get("SKULD__SESSION__SYSTEM_PROMPT", "")
        updated["SKULD__SESSION__SYSTEM_PROMPT"] = f"{prompt}\n\n{hint}" if prompt else hint
        return updated

    def _prepare_arguments(self, lease: SlotLease, mode: str) -> tuple[str, ...]:
        # The session's links into its secrets are absolute by design: restore them as left.
        return (*super()._prepare_arguments(lease, mode), "absolute-links")

    async def _install(self, lease, bootstrap: MachineBootstrap) -> dict:
        """Unpack the pinned bundle on the Mac (once per digest) and describe it."""
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            SshGuestAccess.python_command(
                programs.INSTALL, self._image, BUNDLE_CACHE, BUNDLE_MEDIA_TYPE
            ),
            capture_stdout=True,
        )
        return json.loads(result.stdout)

    async def warm(self, lease, bootstrap: MachineBootstrap) -> None:
        await self.prepare(lease, bootstrap)
        await self._install(lease, bootstrap)

    async def _supervise(  # type: ignore[override]
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: DarwinSlotLayout, action: str
    ) -> bytes:
        launch = self._launch(bootstrap) or {}
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.SLOT, action),
            data=json.dumps(self._slot_spec(layout, launch)).encode(),
            capture_stdout=True,
            safe_error_prefix="NIUU_SLOT_STAGE:",
            safe_detail_prefix="NIUU_SLOT_DETAIL:",
        )
        return result.stdout

    def _slot_spec(self, layout: DarwinSlotLayout, launch: dict) -> dict:
        spec = layout.spec(
            stop_timeout=self._stop_timeout,
            poll=self._poll,
            links=[item["target"] for item in self._root_links(layout, launch)],
            metadata_address=launch.get("metadata_address"),
        )
        spec["labels"] = self._labels(layout, launch)
        return spec

    @staticmethod
    def _labels(layout: DarwinSlotLayout, launch: dict) -> list[str]:
        return [
            layout.label,
            *(f"{layout.label}.{container['name']}" for container in launch.get("sidecars", [])),
        ]

    def _session_mounts(self, layout: DarwinSlotLayout, launch: dict) -> list[dict]:
        return [
            {**mount, "source": layout.relocate(mount["source"])}
            for mount in (*launch.get("secret_mounts", []), *launch.get("volume_mounts", []))
        ]

    def _root_links(self, layout: DarwinSlotLayout, launch: dict) -> list[dict]:
        return [
            {"target": mount["target"], "source": mount["source"]}
            for mount in self._session_mounts(layout, launch)
            if mount["target"].startswith(_LINKED_TARGET_PREFIXES)
        ]

    async def start(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        layout = self._layout(lease)
        self._step(lease, "guest_bundle", "Fetching the session bundle onto the Mac")
        await self._install(lease, bootstrap)
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.PRUNE, BUNDLE_CACHE, self._bundle_digest, layout.label),
        )
        await super().start(lease, bootstrap)

    async def write_files(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, items: list[dict]
    ) -> None:
        # The guest agent reads the session's paths from its configuration: give it the
        # places they have on this Mac, as its own command gets.
        layout = self._layout(lease)
        relocations = layout.relocations()
        items = [
            {**item, "content": translate(item["content"], relocations)}
            if item["directory"] == GUEST_AGENT_DIRECTORY
            else item
            for item in items
        ]
        await super().write_files(lease, bootstrap, items)

    async def target(self, lease: AllocationRef, bootstrap: MachineBootstrap):
        layout = self._layout(lease)
        # One session per Mac and no namespaces: the broker listens on the Mac's loopback
        # and its callbacks reach the Mac's loopback port Forge forwards.
        target = await self._guest_access.open_tunnel(
            lease,
            bootstrap,
            remote_port=self._broker_port,
            reverse_bind_host="127.0.0.1",
            reverse_port=self._guest_platform_port,
            controller_host=self._platform_host,
            controller_port=self._platform_port,
            key=layout.key,
        )
        await self._resume_delivery(lease, bootstrap)
        return target

    def _place(self, layout: DarwinSlotLayout, payload: dict) -> dict:
        """The launch payload with every container path given its place on the Mac."""
        placed = json.loads(json.dumps(payload))
        session_mounts = self._session_mounts(layout, placed)
        seen = [PurePosixPath(mount["source"]) for mount in session_mounts]
        for directory in placed.get("directories", []):
            path = PurePosixPath(layout.relocate(directory["path"]))
            directory["path"] = str(path)
            # What the session mounts is its group's to read (and write, where a pod gives
            # everyone write); what only Forge's agents use stays root's.
            shared = any(path == s or s in path.parents or path in s.parents for s in seen)
            directory["mode"] = _group_mode(directory["mode"]) if shared else "0700"
        places = {
            **{mount["target"]: mount["source"] for mount in session_mounts},
            **layout.session_places(),
        }
        environment = {
            key: translate(value, places) for key, value in placed["environment"].items()
        }
        environment["SKULD__SESSION__SYSTEM_PROMPT"] = environment.get(
            "SKULD__SESSION__SYSTEM_PROMPT", ""
        ).replace("{slot_user}", layout.name)
        environment["TMPDIR"] = layout.tmp
        if SECRETS_TARGET in places:
            environment["NIUU_SECRETS_DIR"] = places[SECRETS_TARGET]
        environment["NIUU_SESSION_PLACES"] = json.dumps(layout.session_places())
        environment["NIUU_SESSION_LINKS"] = json.dumps(
            [
                {
                    "target": translate(mount["target"], layout.session_places()),
                    "source": mount["source"],
                }
                for mount in session_mounts
                if translate(mount["target"], layout.session_places()) != mount["target"]
            ]
        )
        placed["environment"] = environment
        for container in (*placed.get("init_containers", []), *placed.get("sidecars", [])):
            mounts = {
                mount["target"]: layout.relocate(mount["source"]) for mount in container["mounts"]
            }
            container["environment"] = {
                key: translate(value, mounts) for key, value in container["environment"].items()
            }
            container["command"] = [translate(part, mounts) for part in container["command"]]
            container["args"] = [translate(part, mounts) for part in container["args"]]
        placed["slot"] = {
            "index": layout.slot,
            "uid": layout.uid,
            "data_root": layout.session_data,
            "marker": layout.key,
        }
        placed["darwin"] = {
            "label": layout.label,
            "python": self._python_binary,
            "native_path": self._native_path(),
            "agent_home": f"{layout.state_root}/agent-home",
            "agent_tmp": f"{layout.state_root}/agent-tmp",
            "state_root": layout.state_root,
            "log_root": layout.log_root,
            "links": self._root_links(layout, payload),
            "nix": self._nix,
            "init": programs.INIT,
            "boot": programs.BOOT,
            "stop_timeout": self._stop_timeout,
            "poll": self._poll,
            "start_timeout": self._session_start_timeout,
        }
        return placed

    async def _launch_skuld(
        self, lease: SlotLease, bootstrap: MachineBootstrap, payload: dict
    ) -> None:
        layout = self._layout(lease)
        self._require_native(payload)
        placed = self._place(layout, payload)
        self._step(lease, "guest_host", f"Starting Skuld as {layout.name}")
        await self._guest_access.execute(
            lease, bootstrap, self._python(programs.START), data=json.dumps(placed).encode()
        )

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        layout = self._layout(lease)
        launch = self._launch(bootstrap) or {}
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                programs.STOP,
                json.dumps(
                    {
                        "labels": self._labels(layout, launch),
                        "state_root": layout.state_root,
                        "data_root": layout.session_data,
                        "marker": layout.key,
                        "stop_timeout": self._stop_timeout,
                        "poll": self._poll,
                    }
                ),
            ),
            expected_exit_codes=(0, 42),
        )
        if self._credential_delivery is not None:
            await self._credential_delivery.stop(lease, bootstrap)
        # Exit 42: cancelled before storage restore; retain any previous local archive.
        if result.exit_code != 42:
            await self._archive_data(lease, bootstrap)
        # Archived: the slot's user, jobs, files and directories go, and the slot program
        # fails unless the darwin audit finds nothing of them left.
        await self._supervise(lease, bootstrap, layout, "down")
        await self._close_tunnel(layout.key)

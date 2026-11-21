"""Run a session directly on a Mac registered as a host, as a fresh macOS user.

macOS has no network namespaces or cgroups, so the only boundary between sessions is a
user. Slot ``n`` is the user ``niuu-slot-<n>`` (uid ``uid_base + n``), created when its
session starts and deleted, provably, when it stops; the darwin audit
(``host_checks.DARWIN_AUDIT_SH``) is what ``down`` checks before the slot is offered again.

By default a Mac is an exclusive one-slot host. ``shared_slots`` is the operator's
decision to run several sessions on one Mac with the user as their only boundary: their
files, homes and processes are apart, but they share the Mac's network (each can reach
the others' loopback listeners) and its CPUs and memory, with no limits. Each slot then
has its own ports on the Mac's loopback (Skuld's, and where its callbacks to Forge
arrive) and its own metadata address; fixed-path links outside a session's storage are
not made, since only one session could own such a path.

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
  is listed in ``compute.session_images.native`` (whose commands the bundle carries, such
  as OpenBao's) are accepted; anything else is refused when the session is created;
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
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

from niuu.cap_telemetry import record_degraded
from volundr.adapters.outbound import darwin_guest_programs as programs
from volundr.adapters.outbound.guest_credential_delivery import AGENT_CONFIG_FILE
from volundr.adapters.outbound.host_checks import DARWIN_AUDIT_SH
from volundr.adapters.outbound.host_session_programs import SESSION_BOOT
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.ssh_vm_runtime import HostModeRuntime
from volundr.adapters.outbound.vm_slot_layout import (
    FORGE_SESSION_DIRECTORY,
    GUEST_AGENT_DIRECTORY,
    SESSION_DATA,
    SESSION_LAUNCH,
    SESSION_RUNTIME,
    SESSION_SECRETS,
    SlotPlace,
    slot_index,
)
from volundr.adapters.outbound.vm_slot_supervisor import DETAIL_PREFIX, STAGE_PREFIX
from volundr.domain.compute import AllocationRef, MachineBootstrap, SlotLease
from volundr.domain.compute_hosts import HostPlatform
from volundr.domain.models import Session
from volundr.domain.ports import HostedUnitError
from volundr.domain.vm_runtime import SessionImages, SlotLimits

#: Where bundles are unpacked on the Mac, one directory per digest.
BUNDLE_CACHE = "/var/lib/niuu/host-bundles"
#: The bundle's layer in its OCI artifact (the ``darwin-bundle`` jobs of the Dev workflow).
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
#: links them to their source on a one-slot Mac, where the place is the session's alone.
_LINKED_TARGET_PREFIXES = ("/var/run/", "/private/var/run/")
#: The last octet of slot 0's metadata address (169.254.169.254); slot n has 254 - n.
_METADATA_LAST_OCTET = 254

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


@dataclass(frozen=True, kw_only=True)
class DarwinSlotLayout(SlotPlace):
    """A slot of a Mac, at the paths the darwin audit checks.

    ``shared`` is the Forge's ``shared_slots``: without it a Mac has only slot 0. Slot
    ``n`` has its own loopback ports, ``broker_port + n`` and ``platform_port + n``.
    """

    uid_base: int
    shared: bool = False
    broker_port: int
    platform_port: int

    def __post_init__(self) -> None:
        if self.slot != 0 and not self.shared:
            raise ValueError(
                f"A Mac runs one session at a time, but slot {self.slot} was asked for; "
                "set the host's slots to 1, or computeHosts.darwin.sharedSlots to run "
                "several sessions on one Mac"
            )
        if self.slot >= _METADATA_LAST_OCTET:
            raise ValueError(f"A Mac has at most {_METADATA_LAST_OCTET} slots, not {self.slot + 1}")
        low, high = sorted((self.broker_port, self.platform_port))
        if low + self.slot >= high:
            raise ValueError(
                f"Slot {self.slot}'s ports overlap: Skuld's run from {self.broker_port} and "
                f"its callbacks' from {self.platform_port}, one per slot; move broker_port "
                "or guest_platform_port further apart"
            )

    @property
    def skuld_port(self) -> int:
        return self.broker_port + self.slot

    @property
    def callback_port(self) -> int:
        """Where the slot's calls back to Forge arrive on the Mac's loopback."""
        return self.platform_port + self.slot

    @property
    def metadata_address(self) -> str:
        """The slot's own guest agent address (slot 0 has the usual 169.254.169.254)."""
        return f"169.254.169.{_METADATA_LAST_OCTET - self.slot}"

    @property
    def label(self) -> str:
        return f"{SESSION_LABEL}.{self.slot}"

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
            "label": self.label,
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


def _summary(paths: list[str]) -> str:
    """Every path's folder, three levels deep, with how many paths it holds."""
    folders: dict[str, int] = {}
    for path in paths:
        folder = "/".join(PurePosixPath(path).parts[:3])
        folders[folder] = folders.get(folder, 0) + 1
    return "; ".join(f"{folder} ({count})" for folder, count in sorted(folders.items()))


class SshDarwinHostRuntime(HostModeRuntime):
    """Run each session on a Mac as a fresh macOS user, from the pinned session bundle.

    ``skuld_bundle`` is the digest-pinned bundle (``ghcr.io/niuulabs/skuld-darwin@sha256:…``),
    which replaces the Linux runtimes' ``skuld_image``; Forge hands the built-in host
    execution the release's (``session_images``). ``session_images.native`` names pod
    images whose containers run natively from the bundle's commands instead (OpenBao's
    agent image, whose ``bao`` the bundle carries). ``uid_base`` is the slot user's uid,
    free on the Mac (macOS gives its own users 501 and up, Nix's build users 351 and up).
    """

    _audit_program = DARWIN_AUDIT_SH
    # START reads the attached integration's token where ``_place_launch`` puts it and
    # clones with it as the slot's user; the session's Git reads its file there too.
    _clones_with_integration_token = True

    def __init__(
        self,
        *,
        skuld_bundle: str | None = None,
        session_images: SessionImages | None = None,
        uid_base: int = 61000,
        shared_slots: bool = False,
        **kwargs,
    ):
        if "slot_isolation" in kwargs:
            raise ValueError(
                "A Mac's only boundary is the slot user; remove slot_isolation (set uid_base "
                "instead, and shared_slots to run several sessions on one Mac)"
            )
        if "skuld_image" in kwargs:
            raise ValueError(
                "Macs run Skuld from the session bundle, not an image; set skuld_bundle"
            )
        bundle = self._release_image(
            skuld_bundle,
            session_images.darwin if session_images is not None else None,
            kwarg="skuld_bundle",
            setting="compute.session_images.darwin",
        )
        match = _BUNDLE.fullmatch(bundle)
        if match is None:
            raise ValueError(
                "skuld_bundle must be a digest-pinned reference, "
                "ghcr.io/niuulabs/skuld-darwin@sha256:<digest>"
            )
        if not 1000 <= uid_base <= 2**31 - 2:
            raise ValueError("uid_base must lie between 1000 and 2147483646")
        native = session_images.native if session_images is not None else ()
        if any(not image or _BUNDLE.fullmatch(image) is None for image in native):
            raise ValueError("compute.session_images.native must be digest-pinned image references")
        super().__init__(skuld_image=bundle, **kwargs)
        self._bundle_digest = match.group(1)
        self._bundle_root = f"{BUNDLE_CACHE}/{self._bundle_digest}"
        self._native_images = frozenset(native)
        self._uid_base = uid_base
        self._shared_slots = shared_slots

    @property
    def host_platforms(self) -> frozenset[HostPlatform]:
        return frozenset({HostPlatform.DARWIN})

    @property
    def supports_slots(self) -> bool:
        """Only with ``shared_slots``, and a delivery each slot receives its own credentials from.

        The slot user is the only boundary: the operator decides that is enough.
        """
        if not self._shared_slots:
            return False
        delivery = self._credential_delivery
        return delivery is None or delivery.serves_slots

    @property
    def slot_limits(self) -> SlotLimits | None:
        # No cgroups: the session has the Mac's CPUs and memory. The uid is what the audit
        # checks for leftovers.
        return SlotLimits(uid_base=self._uid_base, cpu=None, memory_mib=None, pids=None)

    def _layout(self, lease: AllocationRef) -> DarwinSlotLayout:  # type: ignore[override]
        return DarwinSlotLayout(
            allocation_id=lease.id,
            slot=slot_index(lease),
            uid_base=self._uid_base,
            shared=self._shared_slots,
            broker_port=self._broker_port,
            platform_port=self._guest_platform_port,
        )

    def _python(self, program: str, *args: str) -> str:  # type: ignore[override]
        """A guest program under the bundle's Python, as root; isolated from the Mac's."""
        return SshGuestAccess.python_command(
            program, *args, principal="user", interpreter=("sudo", "-n", self._python_binary, "-I")
        )

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

    def _shape_launch(self, session: Session, launch: dict) -> dict:
        self._require_native(launch)
        return {**launch, "environment": self._mac_environment(session, launch["environment"])}

    def _require_native(self, launch: dict) -> None:
        """Refuse a container the Mac cannot run natively, before anything is stored."""
        allowed = {self._image, *self._native_images}
        for container in (*launch["init_containers"], *launch["sidecars"]):
            if container["image"] not in allowed:
                raise ValueError(
                    f"Container {container['name']!r} runs image {container['image']}, which "
                    "a Mac cannot run; list it in compute.session_images.native if "
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
            # The bundle's Python with the bundle's packages (its libexec wrapper).
            "NIUU_SKULD_PYTHON": f"{self._bundle_root}/libexec/python",
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

    async def _install(self, lease, bootstrap: MachineBootstrap) -> None:
        """Unpack the pinned bundle on the Mac, once per digest."""
        await self._guest_access.execute(
            lease,
            bootstrap,
            SshGuestAccess.python_command(
                programs.INSTALL, self._image, BUNDLE_CACHE, BUNDLE_MEDIA_TYPE
            ),
        )

    async def warm(self, lease, bootstrap: MachineBootstrap) -> None:
        await self.prepare(lease, bootstrap)
        await self._install(lease, bootstrap)

    async def _supervise(  # type: ignore[override]
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: DarwinSlotLayout, action: str
    ) -> bytes:
        launch = self._launch(bootstrap)
        if launch is None:
            raise ValueError(
                f"Allocation {lease.id} has no session launch in its bootstrap, so the Mac's "
                "slot cannot be set up or removed with what the session was given"
            )
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.SLOT, action),
            data=json.dumps(self._slot_spec(layout, launch)).encode(),
            capture_stdout=True,
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )
        return result.stdout

    def _slot_spec(self, layout: DarwinSlotLayout, launch: dict) -> dict:
        return layout.spec(
            stop_timeout=self._stop_timeout,
            poll=self._poll,
            links=[item["target"] for item in self._root_links(layout, launch)],
            metadata_address=layout.metadata_address if launch.get("metadata_address") else None,
        )

    def _session_mounts(self, layout: DarwinSlotLayout, launch: dict) -> list[dict]:
        return [
            {**mount, "source": layout.relocate(mount["source"])}
            for mount in (*launch.get("secret_mounts", []), *launch.get("volume_mounts", []))
        ]

    def _root_links(self, layout: DarwinSlotLayout, launch: dict) -> list[dict]:
        # On a Mac with shared slots a fixed path could be only one session's: the session
        # reads its own copy where its environment says, and no link is made.
        if self._shared_slots:
            return []
        return [
            {"target": mount["target"], "source": mount["source"]}
            for mount in self._session_mounts(layout, launch)
            if mount["target"].startswith(_LINKED_TARGET_PREFIXES)
        ]

    async def start(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        self._step(lease, "guest_bundle", "Fetching the session bundle onto the Mac")
        await self._install(lease, bootstrap)
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.PRUNE, BUNDLE_CACHE, self._bundle_digest, SESSION_LABEL),
        )
        await super().start(lease, bootstrap)

    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """End the session and remove its slot, with the bundle's programs.

        Every program that ends a session runs under the bundle's Python, so the bundle is
        fetched first: a start that failed before it arrived (or a Mac whose bundle cache
        was emptied) would otherwise leave a slot no stop can ever release. The install is
        a no-op when the digest is already unpacked, and a failed fetch raises.
        """
        await self._install(lease, bootstrap)
        await super().stop(lease, bootstrap)

    async def _write_archive(  # type: ignore[override]
        self,
        lease: SlotLease,
        bootstrap: MachineBootstrap,
        layout: DarwinSlotLayout,
        dest: BinaryIO,
    ) -> None:
        """Archive the slot's storage with the bundle's program, which says what failed.

        macOS keeps some of a user's files even from root over SSH (TCC-guarded app
        containers, data vaults), and bsdtar fails the whole archive on one of them. Raising
        would keep the session's work on the Mac forever and its host closed to new
        sessions, so those files are left out, and that is an error log naming them and a
        count on ``ravn.degraded``. Anything else tar cannot archive fails the stop.
        """
        report = f"{layout.state_root}/archive-unreadable.json"
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                programs.ARCHIVE,
                json.dumps(
                    {
                        "root": layout.session_data,
                        "excludes": [".allocation", *self._archive_excludes],
                        "report": report,
                    }
                ),
            ),
            stdout=dest,
            expected_exit_codes=(0, programs.ARCHIVE_SKIPPED),
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )
        if result.exit_code != programs.ARCHIVE_SKIPPED:
            return
        listed = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.ARCHIVE_REPORT, report),
            capture_stdout=True,
        )
        unreadable = json.loads(listed.stdout)
        record_degraded(
            "compute.host.archive.unreadable",
            f"Session {lease.session_id} on slot {layout.slot} of allocation {lease.id}: its "
            f"workspace archive leaves out {len(unreadable)} paths macOS does not let root read "
            f"over SSH, so they are not restored: {_summary(unreadable)}. Give sshd Full Disk "
            "Access on the Mac to keep files TCC guards; data-vault files stay unreadable.",
        )

    async def write_files(
        self, lease: AllocationRef, bootstrap: MachineBootstrap, items: list[dict]
    ) -> None:
        # The guest agent reads the session's paths from its configuration: give it the
        # places they have on this Mac, as its own command gets.
        layout = self._layout(lease)
        relocations = layout.relocations()
        items = [
            {**item, "content": self._agent_file(layout, item, relocations)}
            if item["directory"] == GUEST_AGENT_DIRECTORY
            else item
            for item in items
        ]
        await super().write_files(lease, bootstrap, items)

    def _callbacks(self, layout: DarwinSlotLayout, text: str) -> str:
        """``text`` with Forge's loopback callback address moved to the slot's own port."""
        if layout.callback_port == self._guest_platform_port:
            return text
        return text.replace(
            f"127.0.0.1:{self._guest_platform_port}", f"127.0.0.1:{layout.callback_port}"
        )

    def _agent_file(self, layout: DarwinSlotLayout, item: dict, relocations: dict) -> str:
        """A guest agent file at the Mac's places; its configuration names the slot's address."""
        content = self._callbacks(layout, translate(item["content"], relocations))
        if item["name"] != AGENT_CONFIG_FILE:
            return content
        config = json.loads(content)
        config["listen"] = {**config["listen"], "address": layout.metadata_address}
        return json.dumps(config)

    def _tunnel_ends(self, layout: DarwinSlotLayout) -> dict:  # type: ignore[override]
        # No namespaces: the slot's broker listens on the Mac's loopback at its own port, and
        # its callbacks reach the slot's own loopback port Forge forwards.
        return {
            "remote_port": layout.skuld_port,
            "reverse_bind_host": self._guest_platform_bind_host,
            "reverse_port": layout.callback_port,
            "key": layout.key,
        }

    def _place_launch(self, layout: DarwinSlotLayout, payload: dict) -> dict:  # type: ignore[override]
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
            key: self._callbacks(layout, translate(value, places))
            for key, value in placed["environment"].items()
        }
        environment["SKULD__SESSION__SYSTEM_PROMPT"] = environment.get(
            "SKULD__SESSION__SYSTEM_PROMPT", ""
        ).replace("{slot_user}", layout.name)
        environment["TMPDIR"] = layout.tmp
        # Skuld's own idea of the home (present-file staging, the Files tab) would otherwise
        # be the cluster layout under persistence_mount_path, which does not exist here.
        environment["SKULD__HOME_DIR"] = layout.home
        # The slot's own loopback port: every slot shares the Mac's network.
        environment["SKULD__PORT"] = str(layout.skuld_port)
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
        if git := placed.get("git"):
            # Where root reads the token the slot's agent rendered, and where the session's
            # Git reads it: the mount's place on the Mac, as every other path of the session.
            mount = next((m for m in session_mounts if m["target"] == git["token_mount"]), None)
            if mount is None:
                raise ValueError(
                    f"The session's Git integration token {git['token_file']!r} is on no "
                    "mount of the Mac session"
                )
            git["token_source"] = str(PurePosixPath(mount["source"], git["token_path"]))
            git["token_file"] = translate(git["token_file"], places)
        for container in (*placed.get("init_containers", []), *placed.get("sidecars", [])):
            mounts = {
                mount["target"]: layout.relocate(mount["source"]) for mount in container["mounts"]
            }
            container["environment"] = {
                key: self._callbacks(layout, translate(value, mounts))
                for key, value in container["environment"].items()
            }
            container["command"] = [translate(part, mounts) for part in container["command"]]
            container["args"] = [translate(part, mounts) for part in container["args"]]
        placed["slot"] = {
            "index": layout.slot,
            "uid": layout.uid,
            "data_root": layout.session_data,
            "marker": layout.key,
        }
        if placed.get("metadata_address"):
            placed["metadata_address"] = layout.metadata_address
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
            "boot": SESSION_BOOT,
            "stop_timeout": self._stop_timeout,
            "poll": self._poll,
            "start_timeout": self._session_start_timeout,
        }
        return placed

    def _add_members(self, session: Session, launch: dict, entries: list[dict]) -> None:
        if self._shared_slots:
            raise HostedUnitError(
                f"Unit {session.id} hosts a workflow run, whose members listen on ports the "
                "unit picks; on a Mac with shared slots those ports are every slot's. Run "
                "the run on Linux hosts, or turn computeHosts.darwin.sharedSlots off"
            )
        super()._add_members(session, launch, entries)

    async def _launch_skuld(
        self, lease: SlotLease, bootstrap: MachineBootstrap, payload: dict
    ) -> None:
        self._require_native(payload)
        self._step(lease, "guest_host", f"Starting Skuld as {self._layout(lease).name}")
        await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(programs.START),
            data=json.dumps(payload).encode(),
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )

    async def _stop_session(  # type: ignore[override]
        self, lease: SlotLease, bootstrap: MachineBootstrap, layout: DarwinSlotLayout
    ) -> int:
        """The session's job, then its agents' jobs, whatever the launch still names."""
        result = await self._guest_access.execute(
            lease,
            bootstrap,
            self._python(
                programs.STOP,
                json.dumps(
                    {
                        "label": layout.label,
                        "state_root": layout.state_root,
                        "data_root": layout.session_data,
                        "marker": layout.key,
                        "stop_timeout": self._stop_timeout,
                        "poll": self._poll,
                    }
                ),
            ),
            expected_exit_codes=(0, 42),
            safe_error_prefix=STAGE_PREFIX,
            safe_detail_prefix=DETAIL_PREFIX,
        )
        return result.exit_code

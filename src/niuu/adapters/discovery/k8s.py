"""K8sDiscoveryAdapter — infra-mode cold-start flock discovery via Kubernetes labels (NIU-538).

Lists pods with ``ravn.niuu.world/realm=<realm_id>`` label selector and reads
peer identity from pod annotations.  Trust is delegated to K8s RBAC + SPIFFE
for subsequent Sleipnir communication — no handshake socket is opened.

**Pod annotations**::

    ravn.niuu.world/peer-id: "<uuid>"
    ravn.niuu.world/persona: "coding-agent"
    ravn.niuu.world/capabilities: "bash,file,git,terminal,web"
    ravn.niuu.world/permission-mode: "workspace-write"
    ravn.niuu.world/role: "reviewer"                  # optional
    ravn.niuu.world/description: "Reviews pull requests."   # optional
    ravn.niuu.world/consumes: "review.requested,code.changed"   # optional, comma separated
    ravn.niuu.world/emits: "review.completed"                   # optional, comma separated

Annotations are written by whatever deploys the pod and are read when the peer is first
seen, so the live facts a peer would announce itself (its activity state and busy flag) can
not be carried here: they are reported as not announced. A fact whose annotation is absent
is reported as not announced too.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from niuu.mesh.identity import MeshCandidate, MeshIdentity, MeshPeer, unannounced_facts
from niuu.ports.discovery import PeerCallback

if TYPE_CHECKING:
    from niuu.mesh.config import DiscoveryConfig

try:
    from kubernetes import client as k8s_client  # type: ignore[import-untyped]
    from kubernetes import config as k8s_config  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    k8s_client = None  # type: ignore[assignment]
    k8s_config = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_ANN_PEER_ID = "ravn.niuu.world/peer-id"
_ANN_PERSONA = "ravn.niuu.world/persona"
_ANN_CAPABILITIES = "ravn.niuu.world/capabilities"
_ANN_PERMISSION_MODE = "ravn.niuu.world/permission-mode"
_ANN_ROLE = "ravn.niuu.world/role"
_ANN_DESCRIPTION = "ravn.niuu.world/description"
_ANN_CONSUMES = "ravn.niuu.world/consumes"
_ANN_EMITS = "ravn.niuu.world/emits"
#: The chooser facts a pod annotation can carry; the rest change while the peer runs.
_CARRIED_FACTS = ("role", "description", "consumes_event_types", "emits_event_types")
_LABEL_REALM = "ravn.niuu.world/realm"


def _split_topics(value: object) -> list[str]:
    return [topic.strip() for topic in str(value or "").split(",") if topic.strip()]


class K8sDiscoveryAdapter:
    """Kubernetes pod label-based flock discovery for infra cold-start.

    Parameters
    ----------
    own_identity:
        Pre-built ``MeshIdentity`` for this instance.
    namespace:
        K8s namespace to query (empty = all namespaces).
    label_selector:
        Label selector used to list Ravn pods.
    heartbeat_interval_s:
        Seconds between pod list refreshes.
    **kwargs:
        Ignored — allows forward compatibility with new config fields.
    """

    def __init__(
        self,
        own_identity: MeshIdentity,
        *,
        namespace: str = "",
        label_selector: str = "ravn.niuu.world/role=agent",
        heartbeat_interval_s: float = 30.0,
        # Legacy: accept config object for backward compatibility
        config: DiscoveryConfig | None = None,
        **kwargs: Any,
    ) -> None:
        self._identity = own_identity
        self._namespace = namespace
        self._label_selector = label_selector
        self._heartbeat_interval_s = heartbeat_interval_s

        # Legacy config support — extract values if config object provided
        if config is not None:
            self._namespace = config.k8s.namespace
            self._label_selector = config.k8s.label_selector
            self._heartbeat_interval_s = config.heartbeat_interval_s

        self._peers: dict[str, MeshPeer] = {}
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []
        self._poll_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # DiscoveryPort interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load K8s in-cluster config and run initial scan."""
        if k8s_client is None:  # pragma: no cover
            logger.warning("k8s_discovery: kubernetes not installed — discovery disabled")
            return

        try:
            k8s_config.load_incluster_config()
        except Exception:
            try:
                k8s_config.load_kube_config()
            except Exception as exc:
                logger.debug("k8s_discovery: could not load kubeconfig: %s", exc)

        await self._do_scan()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="k8s_discovery_poll")
        logger.info("k8s_discovery: started peer=%s", self._identity.peer_id)

    async def stop(self) -> None:
        """Cancel background poll task."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

    async def announce(self) -> None:
        """No-op — K8s identity is declared via pod labels/annotations."""

    async def scan(self) -> list[MeshCandidate]:
        """Query K8s for Ravn pods and return as candidates (no handshake)."""
        return await asyncio.get_running_loop().run_in_executor(None, self._list_candidates)

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register join/leave callbacks (non-blocking)."""
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: MeshCandidate) -> MeshPeer | None:
        """No handshake — trust is via K8s RBAC + SPIFFE."""
        return None

    def peers(self) -> dict[str, MeshPeer]:
        """Return the cached verified peer table (synchronous)."""
        return dict(self._peers)

    async def own_identity(self) -> MeshIdentity:
        """Return this Ravn's identity."""
        return self._identity

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _do_scan(self) -> None:
        loop = asyncio.get_running_loop()
        candidates = await loop.run_in_executor(None, self._list_candidates)
        now = datetime.now(UTC)
        seen_ids: set[str] = set()

        for candidate in candidates:
            peer_id = candidate.peer_id
            seen_ids.add(peer_id)
            if peer_id in self._peers:
                self._peers[peer_id].last_seen = now
                continue

            peer = MeshPeer(
                peer_id=peer_id,
                realm_id=candidate.metadata.get("realm_id", ""),
                persona=candidate.metadata.get("persona", ""),
                capabilities=candidate.metadata.get("capabilities", "").split(",")
                if candidate.metadata.get("capabilities")
                else [],
                permission_mode=candidate.metadata.get("permission_mode", ""),
                version="",
                role=candidate.metadata.get("role", ""),
                description=candidate.metadata.get("description", ""),
                consumes_event_types=_split_topics(candidate.metadata.get("consumes_event_types")),
                emits_event_types=_split_topics(candidate.metadata.get("emits_event_types")),
                not_announced=unannounced_facts(candidate.metadata, carries=_CARRIED_FACTS),
                rep_address=candidate.rep_address,
                pub_address=candidate.pub_address,
                trust_level="verified",
                first_seen=now,
                last_seen=now,
                last_heartbeat=now,
            )
            self._peers[peer_id] = peer
            for cb in self._on_join:
                with suppress(Exception):
                    cb(peer)

        # Evict peers no longer visible
        to_remove = [pid for pid in self._peers if pid not in seen_ids]
        for pid in to_remove:
            peer = self._peers.pop(pid, None)
            if peer is not None:
                for cb in self._on_leave:
                    with suppress(Exception):
                        cb(peer)

    def _list_candidates(self) -> list[MeshCandidate]:
        if k8s_client is None:
            return []
        try:
            v1 = k8s_client.CoreV1Api()
            label_selector = f"{_LABEL_REALM}={self._identity.realm_id}," + self._label_selector
            namespace = self._namespace or None
            if namespace:
                pods = v1.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=label_selector,
                )
            else:
                pods = v1.list_pod_for_all_namespaces(label_selector=label_selector)

            candidates = []
            for pod in pods.items:
                annotations = pod.metadata.annotations or {}
                peer_id = annotations.get(_ANN_PEER_ID, "")
                if not peer_id or peer_id == self._identity.peer_id:
                    continue
                candidate = MeshCandidate(
                    peer_id=peer_id,
                    realm_id_hash="",  # K8s doesn't transmit the hash
                    host=pod.status.pod_ip or "",
                    rep_address=None,
                    pub_address=None,
                    handshake_port=None,
                    metadata={
                        "persona": annotations.get(_ANN_PERSONA, ""),
                        "capabilities": annotations.get(_ANN_CAPABILITIES, ""),
                        "permission_mode": annotations.get(_ANN_PERMISSION_MODE, ""),
                        "realm_id": self._identity.realm_id,
                        **{
                            key: annotations[annotation]
                            for key, annotation in (
                                ("role", _ANN_ROLE),
                                ("description", _ANN_DESCRIPTION),
                                ("consumes_event_types", _ANN_CONSUMES),
                                ("emits_event_types", _ANN_EMITS),
                            )
                            if annotation in annotations
                        },
                    },
                )
                candidates.append(candidate)
            return candidates
        except Exception as exc:
            logger.debug("k8s_discovery: list_candidates failed: %s", exc)
            return []

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval_s)
                await self._do_scan()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("k8s_discovery: poll_loop error: %s", exc)

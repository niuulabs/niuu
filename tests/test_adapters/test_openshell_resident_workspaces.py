"""OpenShell residents' own workspace claims, and the one-time copy onto them."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import os
import re
import subprocess
import tarfile
import time
import types

import pytest

from tests.test_adapters.test_openshell_gateway import (
    _codex_auth_document,
    _FakeCredentialStore,
    _FakeOpenShellGatewayClient,
    _FakeWorkloadTokenIssuer,
    _hermes_profile,
    _hermes_runtime,
    _import_adapter,
    _resident_profile,
    _resident_runtime,
)
from volundr.adapters.outbound import openshell_resident_workspace as workspace_module
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentObservedState,
)

NAMESPACE = "openshell-sandboxes"
WORKSPACE = "/sandbox/workspace"
VOLUME = "/resident-workspace"
OLD_IMAGE = "ghcr.io/niuulabs/openshell:dev-old"
TARGET_IMAGE = "ghcr.io/niuulabs/openshell:niu-1099-openshell-resident"


class _ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def _fake_manifest(*, name, namespace, storage, storage_class, access_mode, labels):
    return types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            name=name,
            namespace=namespace,
            labels=dict(labels),
            annotations=None,
            resource_version=None,
        ),
        spec=types.SimpleNamespace(
            access_modes=[access_mode],
            storage_class_name=storage_class,
            resources=types.SimpleNamespace(requests={"storage": storage}),
        ),
    )


class _FakeCoreApi:
    """PVCs with resourceVersion compare-and-swap; reads return copies, like the API."""

    def __init__(self, events: list[str]) -> None:
        self.pvcs: dict[str, types.SimpleNamespace] = {}
        self.events = events
        self._version = 0

    def _bump(self, pvc) -> types.SimpleNamespace:
        self._version += 1
        pvc.metadata.resource_version = str(self._version)
        return copy.deepcopy(pvc)

    async def create_namespaced_persistent_volume_claim(self, *, namespace, body):
        assert namespace == NAMESPACE
        if body.metadata.name in self.pvcs:
            raise _ApiError(409)
        stored = copy.deepcopy(body)
        stored.metadata.annotations = dict(stored.metadata.annotations or {})
        self.pvcs[body.metadata.name] = stored
        self.events.append(f"pvc-created:{body.metadata.name}")
        return self._bump(stored)

    async def read_namespaced_persistent_volume_claim(self, *, name, namespace):
        assert namespace == NAMESPACE
        if name not in self.pvcs:
            raise _ApiError(404)
        return copy.deepcopy(self.pvcs[name])

    async def patch_namespaced_persistent_volume_claim(self, *, name, namespace, body):
        if name not in self.pvcs:
            raise _ApiError(404)
        stored = self.pvcs[name]
        metadata = body.get("metadata", {})
        expected = metadata.get("resourceVersion")
        if expected is not None and expected != stored.metadata.resource_version:
            raise _ApiError(409)
        for key, value in (metadata.get("annotations") or {}).items():
            if value is None:
                stored.metadata.annotations.pop(key, None)
            else:
                stored.metadata.annotations[key] = value
        stored.metadata.labels.update(metadata.get("labels") or {})
        storage = (body.get("spec") or {}).get("resources", {}).get("requests", {}).get("storage")
        if storage:
            stored.spec.resources.requests["storage"] = storage
        return self._bump(stored)

    async def delete_namespaced_persistent_volume_claim(self, *, name, namespace):
        if name not in self.pvcs:
            raise _ApiError(404)
        del self.pvcs[name]
        self.events.append(f"pvc-deleted:{name}")

    def phase(self, name: str) -> str | None:
        return (self.pvcs[name].metadata.annotations or {}).get(
            workspace_module.ANNOTATION_MIGRATION_PHASE
        )


class _Sandbox:
    def __init__(self, name, *, labels, image, annotations, providers=(), workspace=None):
        self.name = name
        self.id = f"id-{name}"
        self.labels = dict(labels)
        self.image = image
        self.annotations = dict(annotations)
        self.providers = tuple(providers)
        #: /sandbox outside the workspace: absolute path -> bytes.
        self.home: dict[str, bytes] = {}
        #: The workspace: relative path -> bytes (the claim's dict when mounted).
        self.workspace: dict[str, bytes] = workspace if workspace is not None else {}
        self.running = False
        self.links: dict[str, str] = {}
        #: A plain directory at the workspace path, off the claim (relative path -> bytes).
        self.stray: dict[str, bytes] | None = None
        #: What the link step moved aside: path -> its files.
        self.aside: dict[str, dict[str, bytes]] = {}


class _SandboxFleetClient(_FakeOpenShellGatewayClient):
    """A gateway with several sandboxes, their files, and claims they mount."""

    def __init__(self, adapter, events: list[str]) -> None:
        super().__init__(adapter)
        self.sandboxes: dict[str, _Sandbox] = {}
        self.claim_files: dict[str, dict[str, bytes]] = {}
        self.events = events
        self.corrupt_restore = False
        self.fail_restore_with: BaseException | None = None
        #: Deletions the gateway accepts and finishes this many seconds later.
        self.delete_seconds = 0.0
        self.deleting: dict[str, float] = {}
        #: name -> how many delete calls the gateway rejects first.
        self.fail_deletes: dict[str, int] = {}
        #: name -> how many file projections into that sandbox fail first.
        self.fail_writes: dict[str, int] = {}
        #: name -> how many health checks of that sandbox's processes fail first.
        self.unhealthy: dict[str, int] = {}

    # -- sandboxes ---------------------------------------------------------------
    def add(self, sandbox: _Sandbox) -> _Sandbox:
        self.sandboxes[sandbox.name] = sandbox
        return sandbox

    def by_name(self, name: str) -> _Sandbox:
        """The gateway addresses exec, files and forwards by sandbox name."""
        return self.sandboxes[name]

    def create_sandbox(self, **kwargs):
        self.created = kwargs
        claim = _mounted_claim(kwargs.get("driver_config") or {})
        workspace = self.claim_files.setdefault(claim, {}) if claim else None
        sandbox = self.add(
            _Sandbox(
                kwargs["name"],
                labels=kwargs.get("labels") or {},
                image=kwargs.get("image") or "",
                annotations=kwargs.get("annotations") or {},
                providers=kwargs.get("providers") or (),
                workspace=workspace,
            )
        )
        self.events.append(f"created:{sandbox.name}")
        return self._proto(sandbox, ready=False)

    def get_sandbox(self, name: str):
        due = self.deleting.get(name)
        if due is not None and time.monotonic() >= due:
            del self.deleting[name]
            self.sandboxes.pop(name, None)
        sandbox = self.sandboxes.get(name)
        return None if sandbox is None else self._proto(sandbox, ready=True)

    def _proto(self, sandbox: _Sandbox, *, ready: bool):
        phases = self._adapter.openshell_pb2
        return self._adapter.OpenShellSandbox(
            id=sandbox.id,
            name=sandbox.name,
            phase=(
                phases.SANDBOX_PHASE_DELETING
                if sandbox.name in self.deleting
                else phases.SANDBOX_PHASE_READY
                if ready
                else phases.SANDBOX_PHASE_PROVISIONING
            ),
            ready=ready,
            labels=sandbox.labels,
            providers=sandbox.providers,
            image=sandbox.image,
            annotations=sandbox.annotations,
        )

    def delete_sandbox(self, name: str) -> bool:
        if self.fail_deletes.get(name):
            self.fail_deletes[name] -= 1
            raise RuntimeError(f"gateway could not delete {name} now")
        self.deleted.append(name)
        self.events.append(f"deleted:{name}")
        if name in self.sandboxes and self.delete_seconds:
            self.deleting.setdefault(name, time.monotonic() + self.delete_seconds)
            return True
        return self.sandboxes.pop(name, None) is not None

    # -- files and processes ---------------------------------------------------------
    def _resolve(self, sandbox: _Sandbox, path: str) -> tuple[dict[str, bytes], str]:
        if path == WORKSPACE or path.startswith(f"{WORKSPACE}/"):
            store = sandbox.stray if sandbox.stray is not None else sandbox.workspace
            return store, path[len(WORKSPACE) :].strip("/")
        return sandbox.home, path

    def _extract_like_gnu_tar(self, sandbox: _Sandbox, files: dict[str, bytes]) -> None:
        """GNU tar replaces a symlink with a new directory for a directory entry of its name."""
        for root, data in self._adapter._credential_file_archives(files):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
                for member in tar.getmembers():
                    path = f"{root.rstrip('/')}/{member.name}"
                    if member.isdir() and path in sandbox.links:
                        del sandbox.links[path]
                        if path == WORKSPACE:
                            sandbox.stray = {}

    def write_files(self, *, sandbox: str, files: dict[str, bytes]) -> None:
        self.written_files.append({"sandbox": sandbox, "files": files})
        sandbox = self.by_name(sandbox)
        self.events.append(f"written:{sandbox.name}")
        self._extract_like_gnu_tar(sandbox, files)
        if self.fail_writes.get(sandbox.name):
            self.fail_writes[sandbox.name] -= 1
            raise RuntimeError(
                "OpenShell credential file projection failed for /sandbox with exit 2: "
                "tar: .volundr: Cannot mkdir: Permission denied"
            )
        for path, content in files.items():
            store, key = self._resolve(sandbox, path)
            store[key] = content

    def exec_detached(self, **kwargs) -> int:
        self.execs.append(kwargs)
        sandbox = self.by_name(kwargs["sandbox"])
        sandbox.running = True
        self.events.append(f"launched:{sandbox.name}")
        return 0

    def exec_script(self, *, sandbox: str, script: str, env: dict) -> tuple[int, str]:
        self.bootstrap_execs.append({"sandbox": sandbox, "script": script, "env": env})
        sandbox = self.by_name(sandbox)
        if "du -sk" in script:
            lines = []
            for source in re.findall(r"(/sandbox/[\w./-]+)", script.split("do")[0]):
                entries = self._tree(sandbox, source)
                if entries is not None:
                    size = sum(len(content) for content in entries.values())
                    lines.append(f"{max(1, size // 1024)}\t{source}")
            return 0, "\n".join(lines) + "\n"
        if "kill -9" in script:
            sandbox.running = False
            self.events.append(f"stopped:{sandbox.name}")
            return 0, ""
        if "kill -0" in script:
            if self.unhealthy.get(sandbox.name):
                self.unhealthy[sandbox.name] -= 1
                return 1, ""
            return (0 if sandbox.running else 1), ""
        if "find . -mindepth 1" in script:
            sandbox.workspace.clear()
            self.events.append(f"cleared:{sandbox.name}")
            return 0, ""
        if "echo mounted" in script:  # the workspace link's state
            if sandbox.links.get(WORKSPACE) == VOLUME:
                return 0, "linked\n"
            return 0, "directory\n" if sandbox.stray is not None else "missing\n"
        if "ln -s" in script:
            link = re.search(r"link=(\S+)", script).group(1)
            target = re.search(r"target=(\S+)", script).group(1)
            output = ""
            if link == WORKSPACE and sandbox.stray is not None and link not in sandbox.links:
                aside = f"{WORKSPACE}.pre-link-20261017T172900Z"
                sandbox.aside[aside] = sandbox.stray
                sandbox.stray = None
                output = f"moved-aside={aside}\n"
            sandbox.links[link] = target
            self.events.append(f"linked:{sandbox.name}:{link}")
            return 0, output
        return 0, ""

    def _tree(self, sandbox: _Sandbox, source: str) -> dict[str, bytes] | None:
        if source == WORKSPACE:
            return dict(sandbox.workspace)
        prefix = f"{source}/"
        entries = {
            path[len(prefix) :]: content
            for path, content in sandbox.home.items()
            if path.startswith(prefix)
        }
        return entries or None

    def exec_to_file(self, *, sandbox, script, destination, max_bytes, timeout_seconds):
        sandbox = self.by_name(sandbox)
        source = re.search(r"cd (\S+)", script).group(1)
        archive = _tar(self._tree(sandbox, source) or {})
        self.events.append(f"archived:{sandbox.name}:{source}")
        if len(archive) > max_bytes:
            destination.write(archive[:max_bytes])
            return self._adapter.OpenShellTransfer(None, "", max_bytes, truncated=True)
        destination.write(archive)
        return self._adapter.OpenShellTransfer(0, "", len(archive))

    def exec_from_file(self, *, sandbox, command, source, timeout_seconds):
        if self.fail_restore_with is not None:
            raise self.fail_restore_with
        sandbox = self.by_name(sandbox)
        target = re.search(r"target=(\S+)", command[-1]).group(1)
        # Extracted into a fresh directory on the claim, never onto the target.
        assert '-C "$stage"' in command[-1]
        prefix = target[len(WORKSPACE) :].strip("/")
        with tarfile.open(fileobj=io.BytesIO(source.read()), mode="r") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.removeprefix("./")
                if self.corrupt_restore and name.endswith("memory.db"):
                    continue
                key = f"{prefix}/{name}" if prefix else name
                sandbox.workspace[key] = tar.extractfile(member).read()
        self.events.append(f"restored:{sandbox.name}:{target}")
        return self._adapter.OpenShellTransfer(0, "", 0)


def _mounted_claim(driver_config: dict) -> str:
    config = driver_config.get("kubernetes", driver_config)
    for volume in config.get("volumes", []):
        claim = volume.get("persistent_volume_claim")
        if claim:
            return claim["claim_name"]
    return ""


def _tar(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        seen: set[str] = set()
        for path in sorted(entries):
            parts = path.split("/")
            for depth in range(1, len(parts)):
                parent = "/".join(parts[:depth])
                if parent not in seen:
                    seen.add(parent)
                    info = tarfile.TarInfo(f"./{parent}")
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
            info = tarfile.TarInfo(f"./{path}")
            info.size = len(entries[path])
            tar.addfile(info, io.BytesIO(entries[path]))
    return buffer.getvalue()


class _Harness:
    def __init__(self, monkeypatch, **kwargs) -> None:
        self.events: list[str] = []
        self.adapter = _import_adapter(monkeypatch)
        self.client = _SandboxFleetClient(self.adapter, self.events)
        self.core = _FakeCoreApi(self.events)
        self.credentials = _FakeCredentialStore(
            {"codex-credentials": {"auth.json": _codex_auth_document()}}
        )
        monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
        core = self.core

        async def get_core_api(_self):
            return core

        monkeypatch.setattr(self.adapter.OpenShellGatewayPodManager, "_get_core_api", get_core_api)
        self.manager = self.manager_for(**kwargs)

    def manager_for(self, **kwargs):
        settings = {
            "resident_workspace_namespace": NAMESPACE,
            "resident_workspace_migration_holder": "volundr-a",
            **kwargs,
        }
        manager = self.adapter.OpenShellGatewayPodManager(
            client=self.client,
            volundr_api_url="https://volundr.example.test",
            workload_audiences=["volundr-api", "mimir", "guild"],
            ready_timeout=1.0,
            **settings,
        )
        manager.set_credential_store(self.credentials)
        manager.set_workload_token_issuer(_FakeWorkloadTokenIssuer())
        return manager

    def old_sandbox(self, runtime, *, image: str = OLD_IMAGE) -> _Sandbox:
        sandbox = self.client.add(
            _Sandbox(
                f"r-{runtime.id.hex[:17]}",
                labels={"volundr.niuu.io/resident": str(runtime.id)},
                image=image,
                annotations={"volundr.niuu.io/image": image},
            )
        )
        sandbox.workspace.update(
            {
                ".ravn/openclaw/state.db": b"notification outbox",
                ".skuld/conversation_1.json": b'{"turns": 3}',
                "notes/plan.md": b"# plan",
            }
        )
        sandbox.home.update(
            {
                "/sandbox/.ravn/memory.db": b"episodes",
                "/sandbox/.ravn/daemon/queue.json": b"[]",
                "/sandbox/.volundr/ravn.yaml": b"rendered",
            }
        )
        sandbox.running = True
        return sandbox


def _running(runtime, sandbox_name: str):
    return runtime.model_copy(
        update={
            "observed_state": ResidentObservedState.ACTIVE,
            "backend_ref": {
                "kind": "OpenShellSandbox",
                "id": f"id-{sandbox_name}",
                "name": sandbox_name,
                "service_url": "http://openshell.example/proxy/session-1",
                "service_name": "skuld",
            },
        }
    )


def _awaiting_deployment(runtime):
    return runtime.model_copy(
        update={"observed_state": ResidentObservedState.PENDING, "backend_ref": {}}
    )


def _condition(observation, kind: str):
    return next((item for item in observation.conditions if item.type == kind), None)


def _claim_name(runtime) -> str:
    return f"resident-{runtime.id}-workspace"


def _profile_with(values: dict) -> ResidentDeploymentProfile:
    profile = _resident_profile()
    merged = {**profile.deployment["values"], **values}
    return profile.model_copy(update={"deployment": {**profile.deployment, "values": merged}})


@pytest.mark.asyncio
async def test_new_resident_gets_its_own_claim_mounted_at_the_workspace(monkeypatch):
    harness = _Harness(
        monkeypatch,
        sandbox_policy={
            "version": 1,
            "filesystem": {"read_only": ["/usr"], "read_write": ["/sandbox", "/tmp"]},
        },
    )
    runtime = _resident_runtime()

    observation = await harness.manager.deploy(runtime, _resident_profile())

    claim = harness.core.pvcs[_claim_name(runtime)]
    assert claim.metadata.labels == {
        "app.kubernetes.io/managed-by": "volundr",
        "volundr/pvc-type": "resident-workspace",
        "niuu.world/resident-id": str(runtime.id),
        "niuu.world/owner-id": runtime.owner_id,
        "niuu.world/tenant-id": runtime.tenant_id,
        # OpenShell's resource admission mounts only claims approved for its workspace.
        "openshell.ai/sandbox-attachable": "true",
        "openshell.ai/sandbox-attachable-workspace": "default",
    }
    assert claim.spec.resources.requests == {"storage": "2Gi"}
    assert claim.spec.access_modes == ["ReadWriteOnce"]
    assert workspace_module.workspace_migration_state(claim) is None
    created = harness.client.created
    assert created["name"] == f"r-{runtime.id.hex[:17]}"
    assert created["annotations"]["volundr.niuu.io/workspace-claim"] == _claim_name(runtime)
    # Mounted outside /sandbox, so the gateway keeps /sandbox its own writable
    # volume; the workspace path links to the claim.
    assert created["driver_config"]["volumes"] == [
        {
            "name": "resident-workspace",
            "persistent_volume_claim": {"claim_name": _claim_name(runtime), "read_only": False},
        }
    ]
    assert created["driver_config"]["containers"]["agent"]["volume_mounts"] == [
        {"name": "resident-workspace", "mount_path": VOLUME, "read_only": False}
    ]
    assert created["policy"].filesystem.read_write == ["/sandbox", "/tmp", VOLUME]
    # Ravn's ~/.ravn state (memory.db, the queue, cron) is kept on the claim too.
    sandbox = harness.client.sandboxes[created["name"]]
    assert sandbox.links == {WORKSPACE: VOLUME, "/sandbox/.ravn": f"{WORKSPACE}/.ravn-home"}
    events = harness.events
    assert events.index(f"linked:{sandbox.name}:{WORKSPACE}") < events.index(
        f"written:{sandbox.name}"
    )
    assert events.index(f"linked:{sandbox.name}:/sandbox/.ravn") < events.index(
        f"launched:{sandbox.name}"
    )
    assert observation.backend_ref["workspace_claim"] == _claim_name(runtime)
    assert observation.observed_state is ResidentObservedState.ACTIVE


@pytest.mark.asyncio
async def test_claim_size_and_class_come_from_settings_and_the_profile(monkeypatch):
    harness = _Harness(
        monkeypatch, resident_workspace_size="5Gi", resident_storage_class="harvester"
    )
    runtime = _resident_runtime()
    profile = _resident_profile().model_copy(
        update={
            "deployment": {
                **_resident_profile().deployment,
                "volumes": {"workspace": {"size": "8Gi"}},
            }
        }
    )

    await harness.manager.deploy(runtime, profile)

    claim = harness.core.pvcs[_claim_name(runtime)]
    assert claim.spec.resources.requests == {"storage": "8Gi"}
    assert claim.spec.storage_class_name == "harvester"


@pytest.mark.asyncio
async def test_delete_removes_the_sandbox_then_the_claim(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    observation = await harness.manager.deploy(runtime, _resident_profile())
    deployed = runtime.model_copy(update={"backend_ref": observation.backend_ref})

    await harness.manager.delete(deployed)

    assert _claim_name(runtime) not in harness.core.pvcs
    assert harness.events.index(f"deleted:{observation.backend_ref['name']}") < (
        harness.events.index(f"pvc-deleted:{_claim_name(runtime)}")
    )


@pytest.mark.asyncio
async def test_delete_leaves_a_claim_that_is_not_the_residents(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    foreign = _fake_manifest(
        name=_claim_name(runtime),
        namespace=NAMESPACE,
        storage="1Gi",
        storage_class=None,
        access_mode="ReadWriteOnce",
        labels={"app": "something-else"},
    )
    await harness.core.create_namespaced_persistent_volume_claim(namespace=NAMESPACE, body=foreign)

    await harness.manager.delete(runtime)

    assert _claim_name(runtime) in harness.core.pvcs


@pytest.mark.asyncio
async def test_existing_resident_is_migrated_verified_then_started_on_its_claim(
    monkeypatch, caplog
):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    caplog.set_level(logging.INFO)

    # Reconcile changes nothing itself: it hands the resident to a deployment.
    pending = await harness.manager.reconcile(runtime, profile)
    assert pending.observed_state is ResidentObservedState.PENDING
    assert pending.backend_ref == {}
    assert _condition(pending, "WorkspaceMigration").reason == "Migrating"
    assert harness.core.pvcs == {}
    assert old.running

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), profile)

    new_name = f"m-{runtime.id.hex[:17]}"
    events = harness.events
    # Archive with the processes stopped, restore and verify, start the resident
    # in the new sandbox, and only once it is healthy remove the old one.
    assert events.index(f"stopped:{old.name}") < events.index(f"archived:{old.name}:{WORKSPACE}")
    assert events.index(f"archived:{old.name}:/sandbox/.ravn") < events.index(f"created:{new_name}")
    assert events.index(f"linked:{new_name}:{WORKSPACE}") < events.index(
        f"restored:{new_name}:{WORKSPACE}"
    )
    assert events.index(f"restored:{new_name}:{WORKSPACE}") < events.index(
        f"restored:{new_name}:{WORKSPACE}/.ravn-home"
    )
    assert events.index(f"restored:{new_name}:{WORKSPACE}/.ravn-home") < events.index(
        f"launched:{new_name}"
    )
    assert events.index(f"launched:{new_name}") < events.index(f"deleted:{old.name}")
    assert old.name not in harness.client.sandboxes
    files = harness.client.claim_files[_claim_name(runtime)]
    assert files[".ravn/openclaw/state.db"] == b"notification outbox"
    assert files[".skuld/conversation_1.json"] == b'{"turns": 3}'
    assert files[".ravn-home/memory.db"] == b"episodes"
    assert files[".ravn-home/daemon/queue.json"] == b"[]"
    claim = harness.core.pvcs[_claim_name(runtime)]
    annotations = claim.metadata.annotations
    assert annotations[workspace_module.ANNOTATION_MIGRATION_PHASE] == "complete"
    assert annotations[workspace_module.ANNOTATION_MIGRATION_SOURCE] == old.name
    assert annotations[workspace_module.ANNOTATION_MIGRATION_DIGEST].startswith("files=5 ")
    assert workspace_module.ANNOTATION_MIGRATION_HOLDER not in annotations
    new = harness.client.sandboxes[new_name]
    assert new.image == TARGET_IMAGE
    assert new.annotations["volundr.niuu.io/replaced-image"] == OLD_IMAGE
    assert new.links == {WORKSPACE: VOLUME, "/sandbox/.ravn": f"{WORKSPACE}/.ravn-home"}
    # The resident's grants stayed in place for both sandboxes; nothing was revoked.
    assert harness.client.deleted_grants == []
    assert harness.credentials.deletes == []
    assert observation.backend_ref["name"] == new_name
    assert observation.backend_ref["workspace_claim"] == _claim_name(runtime)
    assert observation.backend_ref["image"] == TARGET_IMAGE
    assert observation.observed_state is ResidentObservedState.ACTIVE
    messages = [record.getMessage() for record in caplog.records]
    assert sum("workspace to a persistent volume" in message for message in messages) == 1
    assert any("verified; sandbox" in message for message in messages)

    # Afterwards the resident is an ordinary persisted resident: nothing more to do.
    settled = await harness.manager.reconcile(
        runtime.model_copy(update={"backend_ref": observation.backend_ref}), profile
    )
    assert _condition(settled, "WorkspaceMigration") is None
    assert settled.backend_ref["name"] == new_name


@pytest.mark.asyncio
async def test_failed_copy_keeps_the_old_sandbox_and_reports_why(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    harness.client.corrupt_restore = True

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), profile)

    new_name = f"m-{runtime.id.hex[:17]}"
    assert old.name in harness.client.sandboxes
    assert new_name not in harness.client.sandboxes
    assert old.home["/sandbox/.ravn/memory.db"] == b"episodes"
    assert old.running  # its processes were started again
    assert harness.client.deleted_grants == []
    assert harness.credentials.deletes == []
    condition = _condition(observation, "WorkspaceMigration")
    assert condition.reason == "VerificationFailed"
    assert "keeps running on its old sandbox" in condition.message
    assert observation.backend_ref["name"] == old.name
    assert "workspace_claim" not in observation.backend_ref
    assert observation.observed_state is ResidentObservedState.ACTIVE
    claim = harness.core.pvcs[_claim_name(runtime)]
    assert harness.core.phase(_claim_name(runtime)) == "copying"
    assert workspace_module.ANNOTATION_MIGRATION_HOLDER not in claim.metadata.annotations
    upgrade = _condition(observation, "PlatformUpgrade")
    assert upgrade.reason == "UpgradeHeld"

    # Reconcile reports it and leaves the sandbox alone: no retry on every pass.
    harness.client.corrupt_restore = False
    created_before = harness.events.count(f"created:{new_name}")
    reconciled = await harness.manager.reconcile(
        runtime.model_copy(update={"backend_ref": observation.backend_ref}), profile
    )
    assert _condition(reconciled, "WorkspaceMigration").reason == "VerificationFailed"
    assert harness.events.count(f"created:{new_name}") == created_before
    assert old.name in harness.client.sandboxes

    # Restart retries, and this time the copy verifies.
    restarted = await harness.manager.restart(
        runtime.model_copy(update={"backend_ref": observation.backend_ref}), profile
    )
    assert restarted.backend_ref["name"] == new_name
    assert old.name not in harness.client.sandboxes
    assert harness.core.phase(_claim_name(runtime)) == "complete"


@pytest.mark.asyncio
async def test_workspace_over_the_limit_changes_nothing(monkeypatch):
    harness = _Harness(monkeypatch, resident_workspace_migration_max_size="1Ki")
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    old.workspace["big.bin"] = b"x" * 4096
    runtime = _running(runtime, old.name)

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    condition = _condition(observation, "WorkspaceMigration")
    assert condition.reason == "WorkspaceTooLarge"
    assert "over the 1Ki migration limit; nothing was changed" in condition.message
    assert harness.core.pvcs == {}
    assert f"stopped:{old.name}" not in harness.events
    assert not any(event.startswith("archived:") for event in harness.events)
    assert set(harness.client.sandboxes) == {old.name}
    assert observation.backend_ref["name"] == old.name


@pytest.mark.asyncio
async def test_workspace_larger_than_its_claim_changes_nothing(monkeypatch):
    harness = _Harness(monkeypatch, resident_workspace_size="1Ki")
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    old.workspace["big.bin"] = b"x" * 4096
    runtime = _running(runtime, old.name)

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    condition = _condition(observation, "WorkspaceMigration")
    assert condition.reason == "WorkspaceTooLarge"
    assert "more than its 1Ki volume" in condition.message
    assert harness.core.pvcs == {}


@pytest.mark.asyncio
async def test_migration_resumes_after_volundr_restarts_mid_copy(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    # Völundr stops (its pod is replaced) while it restores the copy.
    harness.client.fail_restore_with = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await harness.manager.deploy(_awaiting_deployment(runtime), profile)

    assert harness.core.phase(_claim_name(runtime)) == "copying"
    assert {old.name, new_name} <= set(harness.client.sandboxes)
    harness.client.fail_restore_with = None

    # Another Völundr finds the lease still held by the first: it does not touch it.
    successor = harness.manager_for(resident_workspace_migration_holder="volundr-b")
    with pytest.raises(workspace_module.WorkspaceMigrationHeldError):
        await successor.deploy(_awaiting_deployment(runtime), profile)
    assert old.name in harness.client.sandboxes

    # Once the lease expires it takes over: the stale sandbox goes, the copy restarts.
    harness.core.pvcs[_claim_name(runtime)].metadata.annotations[
        workspace_module.ANNOTATION_MIGRATION_EXPIRES
    ] = "0"
    observation = await successor.deploy(_awaiting_deployment(runtime), profile)

    assert observation.backend_ref["name"] == new_name
    assert set(harness.client.sandboxes) == {new_name}
    assert harness.events.count(f"deleted:{new_name}") == 1
    assert harness.core.phase(_claim_name(runtime)) == "complete"
    files = harness.client.claim_files[_claim_name(runtime)]
    assert files[".ravn-home/memory.db"] == b"episodes"
    assert files["notes/plan.md"] == b"# plan"


@pytest.mark.asyncio
async def test_migration_finishes_when_only_the_verified_copy_is_left(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    claim_name = _claim_name(runtime)
    await harness.core.create_namespaced_persistent_volume_claim(
        namespace=NAMESPACE,
        body=_fake_manifest(
            name=claim_name,
            namespace=NAMESPACE,
            storage="2Gi",
            storage_class=None,
            access_mode="ReadWriteOnce",
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/pvc-type": "resident-workspace",
                "niuu.world/resident-id": str(runtime.id),
            },
        ),
    )
    harness.core.pvcs[claim_name].metadata.annotations = {
        workspace_module.ANNOTATION_MIGRATION_PHASE: "verified",
        workspace_module.ANNOTATION_MIGRATION_SOURCE: f"r-{runtime.id.hex[:17]}",
    }

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    assert harness.core.phase(claim_name) == "complete"
    assert observation.backend_ref["workspace_claim"] == claim_name


@pytest.mark.asyncio
async def test_partial_copy_without_its_source_is_never_started(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    claim_name = _claim_name(runtime)
    await harness.core.create_namespaced_persistent_volume_claim(
        namespace=NAMESPACE,
        body=_fake_manifest(
            name=claim_name,
            namespace=NAMESPACE,
            storage="2Gi",
            storage_class=None,
            access_mode="ReadWriteOnce",
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/pvc-type": "resident-workspace",
                "niuu.world/resident-id": str(runtime.id),
            },
        ),
    )
    harness.core.pvcs[claim_name].metadata.annotations = {
        workspace_module.ANNOTATION_MIGRATION_PHASE: "copying",
        workspace_module.ANNOTATION_MIGRATION_SOURCE: f"r-{runtime.id.hex[:17]}",
    }

    with pytest.raises(workspace_module.WorkspaceMigrationError, match="partial copy"):
        await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    assert harness.client.sandboxes == {}


@pytest.mark.asyncio
async def test_a_claim_that_already_holds_a_workspace_is_never_overwritten(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    claim_name = _claim_name(runtime)
    await harness.core.create_namespaced_persistent_volume_claim(
        namespace=NAMESPACE,
        body=_fake_manifest(
            name=claim_name,
            namespace=NAMESPACE,
            storage="2Gi",
            storage_class=None,
            access_mode="ReadWriteOnce",
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/pvc-type": "resident-workspace",
                "niuu.world/resident-id": str(runtime.id),
            },
        ),
    )
    harness.client.claim_files[claim_name] = {"precious.txt": b"newer state"}

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    assert _condition(observation, "WorkspaceMigration").reason == "ClaimInUse"
    assert harness.client.claim_files[claim_name] == {"precious.txt": b"newer state"}
    assert f"stopped:{old.name}" not in harness.events
    assert old.name in harness.client.sandboxes


@pytest.mark.asyncio
async def test_resident_on_its_own_existing_claim_is_untouched(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime, image=TARGET_IMAGE)
    runtime = _running(runtime, old.name)
    profile = _profile_with(
        {"persistence": {"existingClaim": "volundr-sessions", "mountPath": WORKSPACE}}
    )

    observation = await harness.manager.reconcile(runtime, profile)

    assert harness.core.pvcs == {}
    assert _condition(observation, "WorkspaceMigration") is None
    assert observation.backend_ref["name"] == old.name
    assert not any(event.startswith(("stopped:", "archived:")) for event in harness.events)


@pytest.mark.asyncio
async def test_resident_already_on_its_claim_is_untouched(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    sandbox = harness.old_sandbox(runtime, image=TARGET_IMAGE)
    sandbox.annotations["volundr.niuu.io/workspace-claim"] = _claim_name(runtime)
    sandbox.links[WORKSPACE] = VOLUME
    runtime = _running(runtime, sandbox.name)

    observation = await harness.manager.reconcile(runtime, _resident_profile())

    assert _condition(observation, "WorkspaceMigration") is None
    assert observation.backend_ref["workspace_claim"] == _claim_name(runtime)
    assert not any(
        event.startswith(("stopped:", "archived:", "created:")) for event in harness.events
    )


@pytest.mark.asyncio
async def test_profile_that_turns_persistence_off_keeps_the_held_upgrade(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)

    observation = await harness.manager.reconcile(
        runtime, _profile_with({"persistence": {"enabled": False}})
    )

    assert harness.core.pvcs == {}
    assert _condition(observation, "PlatformUpgrade").reason == "UpgradeHeld"
    assert old.name in harness.client.sandboxes


@pytest.mark.asyncio
async def test_upgrade_proceeds_once_the_resident_is_on_its_claim(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    migrated = await harness.manager.deploy(_awaiting_deployment(runtime), profile)
    on_claim = runtime.model_copy(update={"backend_ref": migrated.backend_ref})
    # The platform moves on to a newer image.
    newer = _profile_with(
        {"image": {"repository": "ghcr.io/niuulabs/openshell", "tag": "dev-newer"}}
    )

    upgrading = await harness.manager.reconcile(on_claim, newer)

    assert upgrading.observed_state is ResidentObservedState.PENDING
    assert upgrading.conditions[0].reason == "Upgrading"
    assert migrated.backend_ref["name"] not in harness.client.sandboxes
    # Its state is on the claim, so the recreated sandbox finds it there.
    redeployed = await harness.manager.deploy(_awaiting_deployment(runtime), newer)
    assert redeployed.backend_ref["image"] == "ghcr.io/niuulabs/openshell:dev-newer"
    assert redeployed.backend_ref["name"] == f"r-{runtime.id.hex[:17]}"
    assert harness.client.claim_files[_claim_name(runtime)][".ravn-home/memory.db"] == b"episodes"
    assert _claim_name(runtime) in harness.core.pvcs


@pytest.mark.asyncio
async def test_without_a_sandbox_namespace_residents_get_no_claim(monkeypatch):
    harness = _Harness(monkeypatch)
    manager = harness.manager_for(resident_workspace_namespace="")
    runtime = _resident_runtime()

    observation = await manager.deploy(runtime, _resident_profile())

    assert harness.core.pvcs == {}
    assert "volundr.niuu.io/workspace-claim" not in harness.client.created["annotations"]
    assert "workspace_claim" not in observation.backend_ref


def test_profile_cannot_point_the_managed_claim_elsewhere():
    runtime = _resident_runtime()
    profile = _profile_with({"persistence": {"mountPath": "/sandbox/elsewhere"}})

    with pytest.raises(ValueError, match="existingClaim"):
        workspace_module.resident_workspace_claim(
            runtime,
            profile,
            size="2Gi",
            storage_class="",
            access_mode="ReadWriteOnce",
            mount_path=WORKSPACE,
        )


def test_manifest_resolves_hard_links_and_ignores_the_root():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        info = tarfile.TarInfo("./a.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"hi"))
        link = tarfile.TarInfo("./b.txt")
        link.type = tarfile.LNKTYPE
        link.linkname = "./a.txt"
        tar.addfile(link)
    buffer.seek(0)

    manifest = workspace_module.read_workspace_manifest(buffer)

    assert set(manifest.entries) == {"a.txt", "b.txt"}
    assert manifest.entries["a.txt"] == manifest.entries["b.txt"]
    assert manifest.files == 2


def test_manifest_rejects_a_stream_that_is_not_a_tar():
    with pytest.raises(workspace_module.WorkspaceVerificationError):
        workspace_module.read_workspace_manifest(io.BytesIO(b"profile banner\n" * 100))


RELAY_CLOSED = "exec relay closed before the command reported an exit status"


def _exec_event(proto, **payload):
    return proto.ExecSandboxRequest(**{"stdout": None, "stderr": None, "exit": None, **payload})


class _RelayedInteractiveExec:
    """ExecSandboxInteractive as the OpenShell gateway (b1780123) relays it.

    The input stream carries no stdin EOF of its own. When the request stream
    ends, the gateway sends the sandbox's SSH channel EOF and CLOSE together;
    russh answers the CLOSE and drops the channel, so the command's exit status
    is never delivered and the call fails UNAVAILABLE (grpc/sandbox.rs,
    run_interactive_exec_with_russh). A command that has read all it needs
    exits first; then the gateway reports the exit and stops reading stdin.

    The remote command needs stdin EOF unless it reads a fixed ``head -c N``.
    ``exit_after`` makes it exit with ``exit_code`` once that many bytes arrived.
    """

    def __init__(self, adapter, *, exit_after: int | None = None, exit_code: int = 0) -> None:
        self._adapter = adapter
        self._exit_after = exit_after
        self._exit_code = exit_code
        self.start = None
        self.received = bytearray()
        self.chunks: list[int] = []
        self.half_closed = False

    def _needs(self, command: list[str]) -> int | None:
        if self._exit_after is not None:
            return self._exit_after
        match = re.match(r"head -c (\d+) \| ", command[-1])
        return int(match.group(1)) if match else None

    def __call__(self, frames, **_kwargs):
        proto = self._adapter.openshell_pb2
        frames = iter(frames)
        self.start = next(frames).start
        needs = self._needs(list(self.start.command))
        while needs is None or len(self.received) < needs:
            try:
                frame = next(frames)
            except StopIteration:
                # EOF and CLOSE together: the channel is gone before the exit.
                self.half_closed = True
                error = self._adapter.grpc.RpcError(RELAY_CLOSED)
                error.code = lambda: types.SimpleNamespace(name="UNAVAILABLE")
                error.details = lambda: RELAY_CLOSED
                raise error
            self.chunks.append(len(frame.stdin))
            self.received.extend(frame.stdin)
        return [_exec_event(proto, exit=types.SimpleNamespace(exit_code=self._exit_code))]


def _relay_client(adapter, interactive):
    client = object.__new__(adapter.OpenShellGatewayClient)
    client._stub = types.SimpleNamespace(ExecSandboxInteractive=interactive)
    client._timeout = 30.0
    client._token_provider = types.SimpleNamespace(token=lambda: "token")
    client._workspace_scope = types.SimpleNamespace(workspace="default")
    return client


def test_relay_closes_without_an_exit_when_the_input_stream_ends_first(monkeypatch):
    """The failure on Noatun: a restore that ends its stream to send stdin EOF."""
    adapter = _import_adapter(monkeypatch)
    proto = adapter.openshell_pb2
    proto.ExecSandboxInput = proto.ExecSandboxRequest
    relay = _RelayedInteractiveExec(adapter)
    start = proto.ExecSandboxRequest(command=["sh", "-c", "tar -xf -"])

    def frames():  # how exec_from_file streamed before this fix
        yield proto.ExecSandboxInput(start=start)
        yield proto.ExecSandboxInput(stdin=b"archive")

    with pytest.raises(adapter.grpc.RpcError, match=RELAY_CLOSED):
        relay(frames())
    assert relay.half_closed


def test_gateway_client_streams_archives_in_bounded_chunks(monkeypatch):
    adapter = _import_adapter(monkeypatch)
    proto = adapter.openshell_pb2
    proto.ExecSandboxInput = proto.ExecSandboxRequest
    sent: list = []
    relay = _RelayedInteractiveExec(adapter)

    def event(**payload):
        return _exec_event(proto, **payload)

    class _Stub:
        ExecSandboxInteractive = relay

        def ExecSandbox(self, request, **_kwargs):  # noqa: N802
            sent.append(request)
            return [
                event(stdout=types.SimpleNamespace(data=b"x" * 10)),
                event(stdout=types.SimpleNamespace(data=b"y" * 10)),
                event(exit=types.SimpleNamespace(exit_code=0)),
            ]

    client = _relay_client(adapter, relay)
    client._stub = _Stub()
    payload = bytes(range(256)) * (adapter.EXEC_STDIN_CHUNK_BYTES // 128) + b"tail!"
    source = io.BytesIO(b"skipped" + payload)
    source.seek(len(b"skipped"))

    restored = client.exec_from_file(
        sandbox="sandbox-1",
        command=["sh", "-c", "exec tar -C '/sandbox/my dir' -xf -"],
        source=source,
        timeout_seconds=60,
    )

    assert restored.exit_code == 0
    assert restored.size == len(payload)
    assert relay.start.no_login_shell is True
    assert relay.start.command == [
        "sh",
        "-c",
        f"head -c {len(payload)} | sh -c 'exec tar -C '\"'\"'/sandbox/my dir'\"'\"' -xf -'",
    ]
    # The command got every byte, in gateway-sized chunks, and the stream never
    # ended before the exit was reported.
    assert bytes(relay.received) == payload
    assert relay.chunks == [
        adapter.EXEC_STDIN_CHUNK_BYTES,
        adapter.EXEC_STDIN_CHUNK_BYTES,
        5,
    ]
    assert not relay.half_closed

    destination = io.BytesIO()
    truncated = client.exec_to_file(
        sandbox="sandbox-1",
        script="tar -cf - .",
        destination=destination,
        max_bytes=15,
        timeout_seconds=60,
    )
    assert truncated.truncated is True
    assert destination.getvalue() == b"x" * 10


def test_gateway_client_stops_streaming_once_the_command_exits(monkeypatch):
    adapter = _import_adapter(monkeypatch)
    proto = adapter.openshell_pb2
    proto.ExecSandboxInput = proto.ExecSandboxRequest
    # tar gives up after the first chunk (say, the disk is full).
    relay = _RelayedInteractiveExec(adapter, exit_after=1, exit_code=2)
    client = _relay_client(adapter, relay)
    source = io.BytesIO(b"z" * (adapter.EXEC_STDIN_CHUNK_BYTES * 3))

    restored = client.exec_from_file(
        sandbox="sandbox-1", command=["tar", "-xf", "-"], source=source, timeout_seconds=60
    )

    assert restored.exit_code == 2
    assert relay.chunks == [adapter.EXEC_STDIN_CHUNK_BYTES]
    assert not relay.half_closed


def test_gateway_client_restores_an_empty_archive_without_stdin(monkeypatch):
    adapter = _import_adapter(monkeypatch)
    proto = adapter.openshell_pb2
    proto.ExecSandboxInput = proto.ExecSandboxRequest
    relay = _RelayedInteractiveExec(adapter)
    client = _relay_client(adapter, relay)

    restored = client.exec_from_file(
        sandbox="sandbox-1", command=["true"], source=io.BytesIO(), timeout_seconds=60
    )

    assert restored.exit_code == 0
    assert relay.start.command == ["sh", "-c", "head -c 0 | true"]
    assert relay.chunks == []
    assert not relay.half_closed


@pytest.mark.asyncio
async def test_a_relay_failure_while_restoring_keeps_the_old_sandbox(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    error = harness.adapter.grpc.RpcError("<_MultiThreadedRendezvous ...>")
    error.code = lambda: types.SimpleNamespace(name="UNAVAILABLE")
    error.details = lambda: RELAY_CLOSED
    harness.client.fail_restore_with = error

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), profile)

    assert old.name in harness.client.sandboxes
    assert new_name not in harness.client.sandboxes
    assert old.running  # its processes were started again
    condition = _condition(observation, "WorkspaceMigration")
    assert condition.reason == "MigrationFailed"
    # It names the step that failed, not the copy out of the old sandbox.
    assert (
        f"Restoring into {WORKSPACE} in {new_name} failed: UNAVAILABLE: {RELAY_CLOSED}"
        in condition.message
    )
    assert "_MultiThreadedRendezvous" not in condition.message
    assert observation.backend_ref["name"] == old.name

    # Restart retries the copy from the start, and it verifies.
    harness.client.fail_restore_with = None
    restarted = await harness.manager.restart(
        runtime.model_copy(update={"backend_ref": observation.backend_ref}), profile
    )
    assert restarted.backend_ref["name"] == new_name
    assert old.name not in harness.client.sandboxes
    assert harness.core.phase(_claim_name(runtime)) == "complete"
    files = harness.client.claim_files[_claim_name(runtime)]
    assert files[".ravn-home/memory.db"] == b"episodes"


def _relay_error(harness):
    error = harness.adapter.grpc.RpcError(RELAY_CLOSED)
    error.code = lambda: types.SimpleNamespace(name="UNAVAILABLE")
    error.details = lambda: RELAY_CLOSED
    return error


async def _failed_migration(harness, runtime, profile):
    """Deploy a resident whose migration fails while restoring; return its observation."""
    harness.client.fail_restore_with = _relay_error(harness)
    observation = await harness.manager.deploy(_awaiting_deployment(runtime), profile)
    harness.client.fail_restore_with = None
    assert _condition(observation, "WorkspaceMigration").reason == "MigrationFailed"
    return observation


@pytest.mark.asyncio
async def test_rollback_waits_for_a_slow_sandbox_deletion(monkeypatch, caplog):
    # Sessions keep the short delete wait; a resident's rollback has its own, longer one.
    harness = _Harness(monkeypatch, resource_delete_timeout=0.05)
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    new_name = f"m-{runtime.id.hex[:17]}"
    harness.client.delete_seconds = 0.3  # Noatun took over 30s

    await _failed_migration(harness, runtime, _resident_profile())

    assert new_name not in harness.client.sandboxes
    assert not any("rollback failed" in record.getMessage() for record in caplog.records)
    assert harness.manager._abandoned_migration_sandboxes == {}
    assert old.name in harness.client.sandboxes
    assert old.running


@pytest.mark.asyncio
@pytest.mark.parametrize("left_by", ["rejected delete", "slow delete"])
async def test_reconcile_removes_the_sandbox_a_failed_migration_left(monkeypatch, left_by):
    harness = _Harness(monkeypatch, resident_sandbox_delete_timeout_seconds=0.05)
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    if left_by == "rejected delete":
        harness.client.fail_deletes[new_name] = 1
    else:
        harness.client.delete_seconds = 0.3  # longer than this Völundr waits

    observation = await _failed_migration(harness, runtime, profile)

    # The rollback gave up on the new sandbox; the old one runs the resident again.
    assert new_name in harness.client.sandboxes
    assert harness.manager._abandoned_migration_sandboxes == {str(runtime.id): new_name}
    assert old.running
    events_before = list(harness.events)
    running = runtime.model_copy(update={"backend_ref": observation.backend_ref})

    reconciled = await harness.manager.reconcile(running, profile)
    if left_by == "slow delete":
        await asyncio.sleep(0.35)
    settled = await harness.manager.reconcile(running, profile)

    assert new_name not in harness.client.sandboxes
    assert harness.events.count(f"deleted:{new_name}") == 1
    assert harness.manager._abandoned_migration_sandboxes == {}
    # The old sandbox and its processes are left exactly as they were.
    later = harness.events[len(events_before) :]
    assert not any(old.name in event for event in later)
    assert old.name in harness.client.sandboxes
    assert old.running
    for observed in (reconciled, settled):
        assert observed.backend_ref["name"] == old.name
        assert _condition(observed, "WorkspaceMigration").reason == "MigrationFailed"
    # The claim still records the unfinished copy, and nobody holds its lease.
    claim = harness.core.pvcs[_claim_name(runtime)]
    assert harness.core.phase(_claim_name(runtime)) == "copying"
    assert workspace_module.ANNOTATION_MIGRATION_HOLDER not in claim.metadata.annotations


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["another resident's", "not on the claim", "another Völundr's"])
async def test_reconcile_never_removes_a_sandbox_it_did_not_leave(monkeypatch, why):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    harness.client.fail_deletes[new_name] = 1
    observation = await _failed_migration(harness, runtime, profile)
    leftover = harness.client.sandboxes[new_name]
    if why == "another resident's":
        leftover.labels["volundr.niuu.io/resident"] = "00000000-0000-0000-0000-000000000001"
    elif why == "not on the claim":
        leftover.annotations.pop("volundr.niuu.io/workspace-claim")
    else:
        # Another Völundr is migrating the resident now: the sandbox is its copy.
        annotations = harness.core.pvcs[_claim_name(runtime)].metadata.annotations
        annotations[workspace_module.ANNOTATION_MIGRATION_HOLDER] = "volundr-b"
        annotations[workspace_module.ANNOTATION_MIGRATION_EXPIRES] = f"{time.time() + 600:.0f}"

    await harness.manager.reconcile(
        runtime.model_copy(update={"backend_ref": observation.backend_ref}), profile
    )

    assert new_name in harness.client.sandboxes
    assert f"deleted:{new_name}" not in harness.events
    assert old.name in harness.client.sandboxes
    assert old.running


@pytest.mark.parametrize(
    ("deployment", "message"),
    [
        ({"values": {"persistence": "yes"}}, "persistence must be an object"),
        ({"values": [], "volumes": {}}, "deployment.values must be an object"),
        ({"values": {}, "volumes": []}, "deployment.volumes must be an object"),
        ({"values": {}, "volumes": {"home": {}}}, "only a workspace volume"),
        ({"values": {}, "volumes": {"workspace": []}}, "workspace must be an object"),
        ({"values": {}, "volumes": {"workspace": {"iops": 3}}}, "unknown settings"),
        ({"values": {}, "volumes": {"workspace": {"size": ""}}}, "size must not be empty"),
    ],
)
def test_profile_workspace_settings_are_validated(deployment, message):
    profile = _resident_profile().model_copy(update={"deployment": deployment})

    with pytest.raises(ValueError, match=message):
        workspace_module.resident_workspace_claim(
            _resident_runtime(),
            profile,
            size="2Gi",
            storage_class="",
            access_mode="ReadWriteOnce",
            mount_path=WORKSPACE,
        )


@pytest.mark.parametrize("persistence", [{"enabled": False}, {"emptyDir": True}])
def test_profile_can_keep_the_workspace_in_the_sandbox(persistence):
    claim = workspace_module.resident_workspace_claim(
        _resident_runtime(),
        _profile_with({"persistence": persistence}),
        size="2Gi",
        storage_class="",
        access_mode="ReadWriteOnce",
        mount_path=WORKSPACE,
    )

    assert claim is None


def _claims(events=None):
    core = _FakeCoreApi(events if events is not None else [])

    async def api():
        return core

    return core, workspace_module.ResidentWorkspaceClaims(api, NAMESPACE)


def _claim(size: str = "2Gi"):
    return workspace_module.ResidentWorkspaceClaim(
        name=_claim_name(_RUNTIME),
        size=size,
        storage_class="",
        access_mode="ReadWriteOnce",
        mount_path=WORKSPACE,
    )


_RUNTIME = _resident_runtime()


@pytest.mark.asyncio
async def test_claims_need_a_namespace():
    with pytest.raises(ValueError, match="sandbox namespace"):
        workspace_module.ResidentWorkspaceClaims(lambda: None, "")


@pytest.mark.asyncio
async def test_a_smaller_existing_claim_is_expanded_never_shrunk(monkeypatch):
    monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
    core, claims = _claims()
    await claims.ensure(_RUNTIME, _claim("1Gi"))

    _, created = await claims.ensure(_RUNTIME, _claim("4Gi"))
    await claims.ensure(_RUNTIME, _claim("2Gi"))

    assert created is False
    assert core.pvcs[_claim_name(_RUNTIME)].spec.resources.requests == {"storage": "4Gi"}


@pytest.mark.asyncio
async def test_a_claim_of_the_same_name_that_is_not_the_residents_is_refused(monkeypatch):
    monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
    core, claims = _claims()
    await core.create_namespaced_persistent_volume_claim(
        namespace=NAMESPACE,
        body=_fake_manifest(
            name=_claim_name(_RUNTIME),
            namespace=NAMESPACE,
            storage="2Gi",
            storage_class=None,
            access_mode="ReadWriteOnce",
            labels={},
        ),
    )

    with pytest.raises(ValueError, match="is not resident"):
        await claims.ensure(_RUNTIME, _claim())


@pytest.mark.asyncio
async def test_lease_rereads_the_claim_after_a_concurrent_change(monkeypatch):
    monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
    core, claims = _claims()
    pvc, _ = await claims.ensure(_RUNTIME, _claim())
    lease = workspace_module.WorkspaceMigrationLease(claims, pvc, "volundr-a", 60)
    await lease.acquire("r-old")
    # The PV controller annotates the claim after the lease last read it.
    await core.patch_namespaced_persistent_volume_claim(
        name=pvc.metadata.name,
        namespace=NAMESPACE,
        body={"metadata": {"annotations": {"pv.kubernetes.io/bind-completed": "yes"}}},
    )

    await lease.set_phase(workspace_module.PHASE_VERIFIED)

    annotations = core.pvcs[pvc.metadata.name].metadata.annotations
    assert annotations[workspace_module.ANNOTATION_MIGRATION_PHASE] == "verified"
    assert annotations["pv.kubernetes.io/bind-completed"] == "yes"


@pytest.mark.asyncio
async def test_lease_taken_over_elsewhere_stops_the_migration(monkeypatch):
    monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
    core, claims = _claims()
    pvc, _ = await claims.ensure(_RUNTIME, _claim())
    lease = workspace_module.WorkspaceMigrationLease(claims, pvc, "volundr-a", 60)
    await lease.acquire("r-old")
    core.pvcs[pvc.metadata.name].metadata.annotations[
        workspace_module.ANNOTATION_MIGRATION_HOLDER
    ] = "volundr-b"
    core._bump(core.pvcs[pvc.metadata.name])

    with pytest.raises(workspace_module.WorkspaceMigrationLeaseLostError):
        await lease.renew()
    with pytest.raises(workspace_module.WorkspaceMigrationLeaseLostError):
        await lease.set_phase(workspace_module.PHASE_COMPLETE)
    assert lease.lost


@pytest.mark.asyncio
async def test_lease_on_a_deleted_claim_is_lost(monkeypatch):
    monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
    core, claims = _claims()
    pvc, _ = await claims.ensure(_RUNTIME, _claim())
    lease = workspace_module.WorkspaceMigrationLease(claims, pvc, "volundr-a", 60)
    await lease.acquire("r-old")
    await core.patch_namespaced_persistent_volume_claim(
        name=pvc.metadata.name, namespace=NAMESPACE, body={"metadata": {}}
    )
    del core.pvcs[pvc.metadata.name]

    with pytest.raises(workspace_module.WorkspaceMigrationLeaseLostError):
        await lease.renew()


@pytest.mark.asyncio
async def test_a_complete_claim_is_never_leased_for_a_copy(monkeypatch):
    monkeypatch.setattr(workspace_module, "build_pvc_manifest", _fake_manifest)
    _, claims = _claims()
    pvc, _ = await claims.ensure(
        _RUNTIME,
        _claim(),
        annotations={workspace_module.ANNOTATION_MIGRATION_PHASE: "complete"},
    )
    lease = workspace_module.WorkspaceMigrationLease(claims, pvc, "volundr-a", 60)

    with pytest.raises(workspace_module.WorkspaceClaimInUseError):
        await lease.acquire("r-old")


def test_manifest_records_symlinks_and_refuses_overlapping_sources():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        link = tarfile.TarInfo("./current")
        link.type = tarfile.SYMTYPE
        link.linkname = "releases/1"
        tar.addfile(link)
        fifo = tarfile.TarInfo("./pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
    buffer.seek(0)

    manifest = workspace_module.read_workspace_manifest(buffer)

    assert manifest.entries["current"] == ("symlink", 0, "releases/1")
    assert manifest.entries["pipe"] == ("special", 0, "")
    with pytest.raises(workspace_module.WorkspaceMigrationError, match="overlap"):
        manifest.merged(manifest)


def test_manifest_rejects_a_hard_link_to_nothing():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        link = tarfile.TarInfo("./b.txt")
        link.type = tarfile.LNKTYPE
        link.linkname = "./missing.txt"
        tar.addfile(link)
    buffer.seek(0)

    with pytest.raises(workspace_module.WorkspaceVerificationError, match="hard link"):
        workspace_module.read_workspace_manifest(buffer)


@pytest.mark.asyncio
async def test_restart_on_the_claim_keeps_ravn_home_on_it(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    sandbox = harness.old_sandbox(runtime, image=TARGET_IMAGE)
    sandbox.annotations["volundr.niuu.io/workspace-claim"] = _claim_name(runtime)
    sandbox.links[WORKSPACE] = VOLUME
    runtime = _running(runtime, sandbox.name)

    observation = await harness.manager.restart(runtime, _resident_profile())

    assert sandbox.links == {WORKSPACE: VOLUME, "/sandbox/.ravn": f"{WORKSPACE}/.ravn-home"}
    assert harness.events.index(f"stopped:{sandbox.name}") < harness.events.index(
        f"linked:{sandbox.name}:/sandbox/.ravn"
    )
    assert observation.backend_ref["name"] == sandbox.name
    assert set(harness.client.sandboxes) == {sandbox.name}


# -- the order: the old sandbox goes only once the resident runs on its claim ---------


async def _failed_after_the_copy(harness, runtime, profile, *, at: str):
    """Deploy a resident whose new sandbox fails after the copy; return its observation."""
    new_name = f"m-{runtime.id.hex[:17]}"
    if at == "file projection":
        # What Noatun hit: the copy verified, then /sandbox was not writable.
        harness.client.fail_writes[new_name] = 1
    else:
        harness.client.unhealthy[new_name] = 10_000
    observation = await harness.manager.deploy(_awaiting_deployment(runtime), profile)
    harness.client.fail_writes.pop(new_name, None)
    harness.client.unhealthy.pop(new_name, None)
    return observation


@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["file projection", "health check"])
async def test_a_failure_after_the_copy_keeps_the_old_sandbox_and_its_processes(monkeypatch, at):
    harness = _Harness(monkeypatch)
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"

    observation = await _failed_after_the_copy(harness, runtime, profile, at=at)

    events = harness.events
    assert f"restored:{new_name}:{WORKSPACE}" in events
    # The old sandbox was never asked to go; the new one was rolled back.
    assert f"deleted:{old.name}" not in events
    assert old.name in harness.client.sandboxes
    assert new_name not in harness.client.sandboxes
    assert old.running  # its processes were started again
    assert events.index(f"deleted:{new_name}") < len(events) - 1 - events[::-1].index(
        f"launched:{old.name}"
    )
    assert old.home["/sandbox/.ravn/memory.db"] == b"episodes"
    assert observation.backend_ref["name"] == old.name
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert _condition(observation, "WorkspaceMigration").reason == "MigrationFailed"
    # The verified copy stays complete on the claim, and the resident keeps its identity.
    claim = harness.core.pvcs[_claim_name(runtime)]
    assert harness.core.phase(_claim_name(runtime)) == "complete"
    annotations = claim.metadata.annotations
    assert annotations[workspace_module.ANNOTATION_MIGRATION_SOURCE] == old.name
    assert annotations[workspace_module.ANNOTATION_MIGRATION_DIGEST].startswith("files=5 ")
    assert workspace_module.ANNOTATION_MIGRATION_HOLDER not in annotations
    assert harness.client.claim_files[_claim_name(runtime)][".ravn-home/memory.db"] == b"episodes"
    assert harness.client.deleted_grants == []
    assert harness.credentials.deletes == []


@pytest.mark.asyncio
async def test_a_complete_claim_is_handed_over_without_a_copy_and_the_old_sandbox_goes_last(
    monkeypatch,
):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    failed = await _failed_after_the_copy(harness, runtime, profile, at="file projection")
    digest = harness.core.pvcs[_claim_name(runtime)].metadata.annotations[
        workspace_module.ANNOTATION_MIGRATION_DIGEST
    ]
    retry_from = len(harness.events)

    restarted = await harness.manager.restart(
        runtime.model_copy(update={"backend_ref": failed.backend_ref}), profile
    )

    retry = harness.events[retry_from:]
    # The claim already holds exactly what the old sandbox holds: nothing is copied.
    assert not any(event.startswith(("restored:", "cleared:")) for event in retry)
    assert retry.index(f"stopped:{old.name}") < retry.index(f"created:{new_name}")
    assert retry.index(f"launched:{new_name}") < retry.index(f"deleted:{old.name}")
    assert old.name not in harness.client.sandboxes
    assert restarted.backend_ref["name"] == new_name
    assert restarted.backend_ref["workspace_claim"] == _claim_name(runtime)
    assert restarted.observed_state is ResidentObservedState.ACTIVE
    claim = harness.core.pvcs[_claim_name(runtime)]
    assert harness.core.phase(_claim_name(runtime)) == "complete"
    assert claim.metadata.annotations[workspace_module.ANNOTATION_MIGRATION_DIGEST] == digest
    assert workspace_module.ANNOTATION_MIGRATION_HOLDER not in claim.metadata.annotations
    assert harness.client.claim_files[_claim_name(runtime)][".ravn-home/memory.db"] == b"episodes"


@pytest.mark.asyncio
async def test_a_handover_copies_again_when_the_old_sandbox_ran_on(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    failed = await _failed_after_the_copy(harness, runtime, profile, at="file projection")
    # Back on its old sandbox, the resident remembers more before the retry.
    old.home["/sandbox/.ravn/memory.db"] = b"episodes and one more"
    retry_from = len(harness.events)

    restarted = await harness.manager.restart(
        runtime.model_copy(update={"backend_ref": failed.backend_ref}), profile
    )

    retry = harness.events[retry_from:]
    assert retry.index(f"stopped:{old.name}") < retry.index(f"restored:{new_name}:{WORKSPACE}")
    assert retry.index(f"launched:{new_name}") < retry.index(f"deleted:{old.name}")
    files = harness.client.claim_files[_claim_name(runtime)]
    assert files[".ravn-home/memory.db"] == b"episodes and one more"
    assert restarted.backend_ref["name"] == new_name
    assert harness.core.phase(_claim_name(runtime)) == "complete"


@pytest.mark.asyncio
async def test_a_rollback_never_deletes_the_last_running_sandbox(monkeypatch):
    harness = _Harness(monkeypatch, resident_sandbox_delete_timeout_seconds=0.05)
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    profile = _resident_profile()
    new_name = f"m-{runtime.id.hex[:17]}"
    # The new sandbox fails after the copy, and the gateway rejects its removal too.
    harness.client.fail_deletes[new_name] = 1

    observation = await _failed_after_the_copy(harness, runtime, profile, at="file projection")

    assert old.name in harness.client.sandboxes
    assert old.running
    assert harness.client.deleted == []  # the rejected delete of the new one only
    assert harness.manager._abandoned_migration_sandboxes == {str(runtime.id): new_name}
    events_before = list(harness.events)
    running = runtime.model_copy(update={"backend_ref": observation.backend_ref})

    # Reconcile removes the leftover beside the claim's copy, never the old sandbox.
    reconciled = await harness.manager.reconcile(running, profile)

    assert new_name not in harness.client.sandboxes
    later = harness.events[len(events_before) :]
    assert not any(old.name in event for event in later)
    assert old.name in harness.client.sandboxes
    assert old.running
    assert reconciled.backend_ref["name"] == old.name
    assert harness.core.phase(_claim_name(runtime)) == "complete"

    # Even a migration asked to deploy into its own source refuses before touching it.
    progress = harness.adapter._WorkspaceMigrationProgress(source=old.name)
    values, claim = harness.manager._resident_values(running, profile)
    with pytest.raises(RuntimeError, match="its source sandbox"):
        await harness.manager._deploy_sandbox(
            running, values, sandbox_name=old.name, sandbox=None, claim=claim, migration=progress
        )
    assert old.name in harness.client.sandboxes


@pytest.mark.asyncio
async def test_the_new_sandbox_stays_when_the_old_one_cannot_be_removed(monkeypatch):
    harness = _Harness(monkeypatch, resident_sandbox_delete_timeout_seconds=0.05)
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime = _resident_runtime()
    old = harness.old_sandbox(runtime)
    runtime = _running(runtime, old.name)
    new_name = f"m-{runtime.id.hex[:17]}"
    harness.client.fail_deletes[old.name] = 1

    observation = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    # The resident runs on its claim; the old sandbox, its processes stopped, waits.
    assert observation.backend_ref["name"] == new_name
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert harness.client.sandboxes[new_name].running
    assert old.name in harness.client.sandboxes
    assert not old.running
    assert harness.core.phase(_claim_name(runtime)) == "complete"
    claim = harness.core.pvcs[_claim_name(runtime)]
    assert workspace_module.ANNOTATION_MIGRATION_HOLDER not in claim.metadata.annotations


@pytest.mark.asyncio
async def test_a_complete_claim_without_its_old_sandbox_is_deployed_without_a_copy(monkeypatch):
    # Heimdall, Bragi and Kvasir on Noatun: copied and verified, the old sandbox
    # removed, and the new one rolled back. The claim holds their state.
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    claim_name = _claim_name(runtime)
    await harness.core.create_namespaced_persistent_volume_claim(
        namespace=NAMESPACE,
        body=_fake_manifest(
            name=claim_name,
            namespace=NAMESPACE,
            storage="2Gi",
            storage_class=None,
            access_mode="ReadWriteOnce",
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/pvc-type": "resident-workspace",
                "niuu.world/resident-id": str(runtime.id),
            },
        ),
    )
    annotations = {
        workspace_module.ANNOTATION_MIGRATION_PHASE: "complete",
        workspace_module.ANNOTATION_MIGRATION_SOURCE: f"r-{runtime.id.hex[:17]}",
        workspace_module.ANNOTATION_MIGRATION_DIGEST: "files=2 bytes=14 sha256=abc",
    }
    harness.core.pvcs[claim_name].metadata.annotations = dict(annotations)
    state = {".ravn-home/memory.db": b"episodes", "notes/plan.md": b"# plan"}
    harness.client.claim_files[claim_name] = dict(state)
    failed = runtime.model_copy(
        update={"observed_state": ResidentObservedState.FAILED, "backend_ref": {}}
    )

    # Reconcile hands it to a deployment, which runs it on its claim as it is.
    pending = await harness.manager.reconcile(failed, _resident_profile())
    assert pending.observed_state is ResidentObservedState.PENDING
    observation = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())

    assert observation.backend_ref["name"] == f"r-{runtime.id.hex[:17]}"
    assert observation.backend_ref["workspace_claim"] == claim_name
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert not any(
        event.startswith(("restored:", "cleared:", "archived:")) for event in harness.events
    )
    assert harness.client.claim_files[claim_name] == state
    assert harness.core.pvcs[claim_name].metadata.annotations == annotations


# -- /sandbox stays writable: the claim is mounted outside it --------------------------


def test_a_resident_claim_is_never_mounted_under_sandbox():
    from volundr.adapters.outbound import openshell_gateway as gateway

    with pytest.raises(ValueError, match="outside /sandbox"):
        gateway._driver_config_from_values(
            {"residentWorkspace": {"existingClaim": "c", "mountPath": "/sandbox/workspace"}}
        )


def _run_script(script: str, *, stdin: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", script], input=stdin, capture_output=True, check=False)


def test_restore_never_changes_the_claims_own_root(monkeypatch, tmp_path):
    adapter = _import_adapter(monkeypatch)
    root = tmp_path / "claim"
    root.mkdir()
    root.chmod(0o2775)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        top = tarfile.TarInfo(".")
        top.type = tarfile.DIRTYPE
        top.mode = 0o700  # restoring this onto the root is what failed for NemoClaw
        tar.addfile(top)
        for name, data in {"./notes/plan.md": b"# plan", "./.ravn/state.db": b"db"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    result = _run_script(adapter._workspace_extract_script(str(root)), stdin=archive.getvalue())

    assert result.returncode == 0, result.stderr
    assert (root / "notes/plan.md").read_bytes() == b"# plan"
    assert (root / ".ravn/state.db").read_bytes() == b"db"
    assert sorted(path.name for path in root.iterdir()) == [".ravn", "notes"]
    assert root.stat().st_mode & 0o7777 == 0o2775


def _gnu_stat() -> bool:
    return _run_script("stat -c %d /").returncode == 0


@pytest.mark.skipif(not _gnu_stat(), reason="the sandbox images ship GNU stat")
def test_workspace_link_points_at_the_volume_and_never_replaces_another_link(monkeypatch, tmp_path):
    adapter = _import_adapter(monkeypatch)
    volume = tmp_path / "volume"
    volume.mkdir()
    link = tmp_path / "home" / "workspace"
    link.parent.mkdir()
    script = adapter._workspace_link_script(str(link), str(volume))

    assert _run_script(script).returncode == 0
    assert link.is_symlink() and os.readlink(link) == str(volume)
    assert _run_script(script).returncode == 0  # again: already in place

    link.unlink()
    link.mkdir()  # an empty directory the image left
    assert _run_script(script).returncode == 0
    assert link.is_symlink()

    # A link somewhere else is never replaced.
    link.unlink()
    link.symlink_to(tmp_path)
    result = _run_script(script)
    assert result.returncode == 1
    assert b"not " in result.stderr
    assert os.readlink(link) == str(tmp_path)


# -- every engine runs on its volume: the workspace link survives file projection ------

OPENCLAW_CONFIG = f"{WORKSPACE}/.openclaw/openclaw.json"


def _openclaw_runtime():
    return _resident_runtime().model_copy(
        update={
            "engine": ResidentEngine.OPENCLAW,
            "profile_id": "nemoclaw-openshell",
            "model": "niuu/nvidia/nemotron-3-super",
        }
    )


def _openclaw_profile() -> ResidentDeploymentProfile:
    config = {
        "gateway": {"bind": "loopback", "mode": "local"},
        "agents": {"defaults": {"workspace": WORKSPACE}},
        "models": {"providers": {"niuu": {"baseUrl": "http://bifrost.example.test/v1"}}},
    }
    return ResidentDeploymentProfile(
        id="nemoclaw-openshell",
        display_name="NemoClaw",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.OPENCLAW,
        capabilities=[ResidentCapability.CHAT, ResidentCapability.RUNTIME_RESTART],
        deployment={
            "values": {
                "image": "ghcr.io/nvidia/openshell-community/sandboxes/openclaw@sha256:abc",
                "openshell": {
                    "processMode": "replace",
                    "service": {"name": "openclaw", "port": 18789},
                    "processes": [
                        {
                            "name": "openclaw",
                            "command": ["openclaw", "gateway", "run"],
                            "logPath": "/sandbox/.volundr/openclaw.log",
                            "files": {OPENCLAW_CONFIG: json.dumps(config)},
                        }
                    ],
                },
            }
        },
    )


_ENGINES = {
    "ravn": (_resident_runtime, _resident_profile, "/sandbox/.volundr/ravn.yaml"),
    "openclaw": (_openclaw_runtime, _openclaw_profile, OPENCLAW_CONFIG),
    "hermes": (_hermes_runtime, _hermes_profile, f"{WORKSPACE}/.hermes/config.yaml"),
}


def test_file_projection_never_carries_a_directory_entry_for_a_link(monkeypatch):
    adapter = _import_adapter(monkeypatch)
    archives = adapter._credential_file_archives(
        {
            OPENCLAW_CONFIG: b"{}",
            f"{WORKSPACE}/.hermes/config.yaml": b"model: x",
            "/sandbox/.ravn/skills/note.md": b"skill",
            "/sandbox/.volundr/ravn.yaml": b"rendered",
        }
    )

    ((root, data),) = archives
    assert root == "/sandbox"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        directories = {member.name for member in tar.getmembers() if member.isdir()}
        files = {member.name for member in tar.getmembers() if member.isfile()}
    # GNU tar would replace the workspace and ~/.ravn links with new directories.
    assert "workspace" not in directories
    assert ".ravn" not in directories
    assert {"workspace/.openclaw", "workspace/.hermes", ".ravn/skills", ".volundr"} <= directories
    assert "workspace/.openclaw/openclaw.json" in files


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", sorted(_ENGINES))
async def test_every_engine_runs_on_its_volume(monkeypatch, engine):
    harness = _Harness(monkeypatch)
    make_runtime, make_profile, config = _ENGINES[engine]
    runtime = make_runtime()

    observation = await harness.manager.deploy(runtime, make_profile())

    sandbox = harness.client.sandboxes[observation.backend_ref["name"]]
    assert sandbox.links[WORKSPACE] == VOLUME
    assert sandbox.stray is None
    assert harness.events.index(f"linked:{sandbox.name}:{WORKSPACE}") < harness.events.index(
        f"written:{sandbox.name}"
    )
    files = harness.client.claim_files[_claim_name(runtime)]
    if config.startswith(f"{WORKSPACE}/"):
        # The engine's configuration landed on the volume, through the link.
        assert config.removeprefix(f"{WORKSPACE}/") in files
    assert observation.backend_ref["workspace_claim"] == _claim_name(runtime)
    assert observation.observed_state is ResidentObservedState.ACTIVE


@pytest.mark.asyncio
async def test_an_existing_directory_at_the_workspace_is_moved_aside_and_linked(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime = _openclaw_runtime()
    real = {"AGENTS.md": b"# agents", ".openclaw/agents/main/agent/models.json": b"{}"}
    harness.client.claim_files[_claim_name(runtime)] = dict(real)
    original = harness.client.create_sandbox

    def create_with_a_workspace_directory(**kwargs):
        created = original(**kwargs)
        # The image (or its runtime) made /sandbox/workspace before Völundr linked it.
        harness.client.sandboxes[kwargs["name"]].stray = {"leftover.txt": b"from the image"}
        return created

    harness.client.create_sandbox = create_with_a_workspace_directory

    observation = await harness.manager.deploy(runtime, _openclaw_profile())

    sandbox = harness.client.sandboxes[observation.backend_ref["name"]]
    assert sandbox.links[WORKSPACE] == VOLUME
    assert sandbox.aside == {
        f"{WORKSPACE}.pre-link-20261017T172900Z": {"leftover.txt": b"from the image"}
    }
    files = harness.client.claim_files[_claim_name(runtime)]
    assert files["AGENTS.md"] == b"# agents"
    assert "leftover.txt" not in files


async def _off_its_volume(harness, runtime, profile):
    """Deploy a resident, then leave its workspace a plain directory, as on Noatun."""
    observation = await harness.manager.deploy(runtime, profile)
    sandbox = harness.client.sandboxes[observation.backend_ref["name"]]
    del sandbox.links[WORKSPACE]
    sandbox.stray = {".openclaw/openclaw.json": b"{}", ".openclaw/update-check.json": b"{}"}
    harness.manager._linked_workspaces.clear()  # as after Völundr restarts
    return runtime.model_copy(update={"backend_ref": observation.backend_ref}), sandbox


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "hermes"])
async def test_reconcile_puts_a_resident_off_its_volume_back_on_it(monkeypatch, engine):
    harness = _Harness(monkeypatch)
    make_runtime, make_profile, _ = _ENGINES[engine]
    runtime, profile = make_runtime(), make_profile()
    claim = _claim_name(runtime)
    harness.client.claim_files[claim] = {"AGENTS.md": b"# agents", "memory/notes.md": b"kept"}
    running, sandbox = await _off_its_volume(harness, runtime, profile)
    state_before = {
        key: value
        for key, value in harness.client.claim_files[claim].items()
        if key in ("AGENTS.md", "memory/notes.md")
    }
    repair_from = len(harness.events)

    observation = await harness.manager.reconcile(running, profile)

    repair = harness.events[repair_from:]
    # Stop, move the stray directory aside, link, project the files, start again.
    assert repair.index(f"stopped:{sandbox.name}") < repair.index(
        f"linked:{sandbox.name}:{WORKSPACE}"
    )
    assert repair.index(f"linked:{sandbox.name}:{WORKSPACE}") < repair.index(
        f"written:{sandbox.name}"
    )
    assert repair.index(f"written:{sandbox.name}") < repair.index(f"launched:{sandbox.name}")
    assert not any(event.startswith(("archived:", "restored:", "cleared:")) for event in repair)
    assert not any(event.startswith(("created:", "deleted:")) for event in repair)
    assert sandbox.links[WORKSPACE] == VOLUME
    assert sandbox.running
    assert list(sandbox.aside.values()) == [
        {".openclaw/openclaw.json": b"{}", ".openclaw/update-check.json": b"{}"}
    ]
    files = harness.client.claim_files[claim]
    assert {key: files[key] for key in state_before} == state_before
    assert "update-check.json" not in str(sorted(files))
    assert observation.backend_ref["name"] == sandbox.name
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert (
        harness.core.pvcs[claim].metadata.annotations.get(
            workspace_module.ANNOTATION_MIGRATION_PHASE
        )
        is None
    )

    # Once on its volume, later passes only look once per sandbox.
    settled_from = len(harness.events)
    await harness.manager.reconcile(running, profile)
    assert not any(
        event.startswith(("stopped:", "linked:")) for event in harness.events[settled_from:]
    )


@pytest.mark.asyncio
async def test_restart_puts_a_resident_off_its_volume_back_on_it(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime, profile = _openclaw_runtime(), _openclaw_profile()
    running, sandbox = await _off_its_volume(harness, runtime, profile)

    await harness.manager.restart(running, profile)

    assert sandbox.links[WORKSPACE] == VOLUME
    assert len(sandbox.aside) == 1
    assert sandbox.running
    assert "AGENTS.md" not in str(sandbox.aside)


@pytest.mark.asyncio
async def test_adopting_a_sandbox_off_its_volume_restarts_all_its_processes(monkeypatch):
    harness = _Harness(monkeypatch)
    runtime, profile = _openclaw_runtime(), _openclaw_profile()
    running, sandbox = await _off_its_volume(harness, runtime, profile)
    launched_before = harness.events.count(f"launched:{sandbox.name}")
    unadopted = running.model_copy(update={"observed_state": ResidentObservedState.PENDING})

    observation = await harness.manager.deploy(unadopted, profile)

    assert observation.backend_ref["name"] == sandbox.name
    assert sandbox.links[WORKSPACE] == VOLUME
    assert harness.events.count(f"launched:{sandbox.name}") > launched_before
    assert f"stopped:{sandbox.name}" in harness.events
    assert len(sandbox.aside) == 1


@pytest.mark.skipif(not _gnu_stat(), reason="the sandbox images ship GNU stat")
def test_workspace_link_moves_a_directory_with_state_aside(monkeypatch, tmp_path):
    adapter = _import_adapter(monkeypatch)
    volume = tmp_path / "volume"
    volume.mkdir()
    link = tmp_path / "home" / "workspace"
    link.mkdir(parents=True)
    (link / "state.db").write_bytes(b"fresh state")

    result = _run_script(adapter._workspace_link_script(str(link), str(volume)))

    assert result.returncode == 0, result.stderr
    assert link.is_symlink() and os.readlink(link) == str(volume)
    (aside,) = [path for path in link.parent.iterdir() if ".pre-link-" in path.name]
    assert (aside / "state.db").read_bytes() == b"fresh state"
    assert f"moved-aside={aside}" in result.stdout.decode()
    state = _run_script(adapter._workspace_link_state_script(str(link), str(volume)))
    assert state.stdout.decode().strip() == "linked"


# -- an upgrade's old sandbox that is slow to go (Heimdall on Noatun, 2026-10-17) ------

NEWER_IMAGE = "ghcr.io/niuulabs/openshell:dev-newer"
#: The resident's own state on its claim, which no upgrade may copy over or change.
HEIMDALL_STATE = {
    ".ravn-home/memory.db": b"episodes",
    ".skuld/conversation_1.json": b'{"turns": 3}',
    "notes/plan.md": b"# plan",
}


async def _heimdall(harness):
    """A resident on its own claim (copied, ``complete``) in its sandbox ``r-…``.

    As Heimdall ran on Noatun when a new Völundr brought a new sandbox image.
    Returns the resident, its running record and the platform's newer profile.
    """
    runtime = _resident_runtime()
    claim_name = _claim_name(runtime)
    await harness.core.create_namespaced_persistent_volume_claim(
        namespace=NAMESPACE,
        body=_fake_manifest(
            name=claim_name,
            namespace=NAMESPACE,
            storage="2Gi",
            storage_class=None,
            access_mode="ReadWriteOnce",
            labels={
                "app.kubernetes.io/managed-by": "volundr",
                "volundr/pvc-type": "resident-workspace",
                "niuu.world/resident-id": str(runtime.id),
            },
        ),
    )
    harness.core.pvcs[claim_name].metadata.annotations = {
        workspace_module.ANNOTATION_MIGRATION_PHASE: "complete",
        workspace_module.ANNOTATION_MIGRATION_SOURCE: f"r-{runtime.id.hex[:17]}",
        workspace_module.ANNOTATION_MIGRATION_DIGEST: "files=3 bytes=26 sha256=abc",
    }
    harness.client.claim_files[claim_name] = dict(HEIMDALL_STATE)
    deployed = await harness.manager.deploy(_awaiting_deployment(runtime), _resident_profile())
    assert deployed.backend_ref["name"] == f"r-{runtime.id.hex[:17]}"
    running = _running(runtime, deployed.backend_ref["name"]).model_copy(
        update={"backend_ref": deployed.backend_ref}
    )
    harness.manager._linked_workspaces.clear()  # the new Völundr has not checked it yet
    newer = _profile_with(
        {"image": {"repository": "ghcr.io/niuulabs/openshell", "tag": "dev-newer"}}
    )
    return runtime, running, newer


def _claim_annotations(harness, runtime) -> dict:
    return dict(harness.core.pvcs[_claim_name(runtime)].metadata.annotations)


def _assert_on_its_claim_as_it_was(harness, runtime, observation, annotations, since: int):
    """Redeployed on its claim with its state as it was: nothing copied, nothing changed."""
    claim = _claim_name(runtime)
    assert observation.observed_state is ResidentObservedState.ACTIVE
    assert observation.backend_ref["name"] == f"r-{runtime.id.hex[:17]}"
    assert observation.backend_ref["workspace_claim"] == claim
    assert observation.backend_ref["image"] == NEWER_IMAGE
    assert _condition(observation, "PlatformUpgrade") is None
    later = harness.events[since:]
    assert not any(event.startswith(("archived:", "restored:", "cleared:")) for event in later)
    assert later.count(f"created:{observation.backend_ref['name']}") == 1
    files = harness.client.claim_files[claim]
    assert {key: files.get(key) for key in HEIMDALL_STATE} == HEIMDALL_STATE
    assert _claim_annotations(harness, runtime) == annotations
    sandbox = harness.client.sandboxes[observation.backend_ref["name"]]
    assert sandbox.links[WORKSPACE] == VOLUME
    assert sandbox.running


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delete_seconds", [0.0, 0.3], ids=["fast delete (Bragi, Kvasir)", "slow delete (Heimdall)"]
)
async def test_an_upgrade_waits_as_long_as_a_resident_sandbox_takes_to_go(
    monkeypatch, caplog, delete_seconds
):
    # A session gives up after resource_delete_timeout (30s); a resident's upgrade
    # waits resident_sandbox_delete_timeout_seconds (180s) for its old sandbox.
    harness = _Harness(
        monkeypatch, resource_delete_timeout=0.05, resident_sandbox_delete_timeout_seconds=3.0
    )
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime, running, newer = await _heimdall(harness)
    annotations = _claim_annotations(harness, runtime)
    harness.client.delete_seconds = delete_seconds
    since = len(harness.events)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        upgrading = await harness.manager.reconcile(running, newer)
        assert upgrading.observed_state is ResidentObservedState.PENDING
        assert upgrading.backend_ref == {}
        assert _condition(upgrading, "SandboxReady").reason == "Upgrading"
        platform = _condition(upgrading, "PlatformUpgrade")
        assert (platform.status.value, platform.reason) == ("false", "Upgrading")
        assert NEWER_IMAGE in platform.message
        assert running.backend_ref["name"] not in harness.client.sandboxes
        assert harness.events[since:].count(f"deleted:{running.backend_ref['name']}") == 1

        redeployed = await harness.manager.deploy(_awaiting_deployment(runtime), newer)

    _assert_on_its_claim_as_it_was(harness, runtime, redeployed, annotations, since)
    assert _warnings(caplog) == []
    assert harness.manager._failed_resident_upgrades == {}


@pytest.mark.asyncio
async def test_an_old_sandbox_that_outlasts_the_wait_is_redeployed_once_it_goes(
    monkeypatch, caplog
):
    harness = _Harness(
        monkeypatch, resource_delete_timeout=0.05, resident_sandbox_delete_timeout_seconds=0.3
    )
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime, running, newer = await _heimdall(harness)
    name = running.backend_ref["name"]
    annotations = _claim_annotations(harness, runtime)
    harness.client.delete_seconds = 1.2  # longer than the upgrade and its redeployment wait
    since = len(harness.events)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        # The upgrade's wait runs out: no failure, the resident is handed to its
        # redeployment, and the upgrade says what it waits for.
        upgrading = await harness.manager.reconcile(running, newer)
        assert upgrading.observed_state is ResidentObservedState.PENDING
        assert upgrading.backend_ref == {}
        platform = _condition(upgrading, "PlatformUpgrade")
        assert platform.reason == "Upgrading"
        assert f"once OpenShell has finished deleting sandbox {name}" in platform.message
        assert name in harness.client.deleting
        assert harness.manager._failed_resident_upgrades == {}

        # The redeployment waits for it too; when OpenShell is slower still, that
        # deployment fails (the service records FAILED) and the next pass retries.
        with pytest.raises(TimeoutError, match="was not deleted within"):
            await harness.manager.deploy(_awaiting_deployment(runtime), newer)
        failed = runtime.model_copy(
            update={"observed_state": ResidentObservedState.FAILED, "backend_ref": {}}
        )
        going = await harness.manager.reconcile(failed, newer)
        assert going.observed_state is ResidentObservedState.PENDING
        assert going.backend_ref == {}
        assert _condition(going, "PlatformUpgrade").reason == "Upgrading"

        deadline = time.monotonic() + 3.0
        while harness.client.get_sandbox(name) is not None:
            assert time.monotonic() < deadline, "the fake gateway never finished the delete"
            await asyncio.sleep(0.02)
        # Gone: still an upgrade in progress, never "Restart the resident to retry".
        gone = await harness.manager.reconcile(failed, newer)
        assert gone.observed_state is ResidentObservedState.PENDING
        assert _condition(gone, "PlatformUpgrade").reason == "Upgrading"

        redeployed = await harness.manager.deploy(_awaiting_deployment(runtime), newer)

    _assert_on_its_claim_as_it_was(harness, runtime, redeployed, annotations, since)
    assert harness.events[since:].count(f"deleted:{name}") == 1
    assert redeployed.backend_ref["image"] == NEWER_IMAGE
    assert harness.client.sandboxes[name].annotations["volundr.niuu.io/replaced-image"] == (
        TARGET_IMAGE
    )
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].startswith(f"Resident OpenShell Muninn's old sandbox {name} was not gone")
    assert not any("Restart the resident" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_a_refused_delete_keeps_the_resident_running_and_is_retried(monkeypatch, caplog):
    harness = _Harness(monkeypatch)
    runtime, running, newer = await _heimdall(harness)
    name = running.backend_ref["name"]
    harness.client.fail_deletes[name] = 3
    caplog.clear()

    with caplog.at_level(logging.INFO):
        for _ in range(3):
            harness.client.exposed = None
            retrying = await harness.manager.reconcile(running, newer)
            # It still runs on its old sandbox, its service exposed again.
            assert retrying.observed_state is ResidentObservedState.ACTIVE
            assert retrying.backend_ref["name"] == name
            platform = _condition(retrying, "PlatformUpgrade")
            assert platform.reason == "UpgradeRetrying"
            assert "gateway could not delete" in platform.message
            assert harness.client.sandboxes[name].running
            assert harness.client.exposed["sandbox_name"] == name

        upgrading = await harness.manager.reconcile(running, newer)

    assert _condition(upgrading, "PlatformUpgrade").reason == "Upgrading"
    assert name not in harness.client.sandboxes
    # One line for the state, not one per pass.
    assert [message for message in _warnings(caplog) if "is retried" in message] == [
        f"Upgrading resident OpenShell Muninn from {TARGET_IMAGE} to {NEWER_IMAGE} is retried "
        f"on the next pass: its sandbox {name} could not be removed (OpenShell resource "
        f"cleanup failed for {name}: gateway could not delete {name} now)"
    ]
    redeployed = await harness.manager.deploy(_awaiting_deployment(runtime), newer)
    assert redeployed.backend_ref["image"] == NEWER_IMAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["deleted during the pass", "being deleted"])
async def test_repair_never_touches_a_missing_sandbox_and_deploys_it_instead(
    monkeypatch, caplog, how
):
    harness = _Harness(monkeypatch, resident_sandbox_delete_timeout_seconds=3.0)
    monkeypatch.setattr(harness.adapter, "READY_POLL_INTERVAL", 0.01)
    runtime, profile = _resident_runtime(), _resident_profile()
    claim = _claim_name(runtime)
    harness.client.claim_files[claim] = {"AGENTS.md": b"# agents", "memory/notes.md": b"kept"}
    running, sandbox = await _off_its_volume(harness, runtime, profile)
    annotations = _claim_annotations(harness, runtime)
    real_get = harness.client.get_sandbox
    if how == "deleted during the pass":

        def get_then_delete(name):
            found = real_get(name)
            harness.client.sandboxes.pop(name, None)  # removed right after reconcile's read
            return found

        harness.client.get_sandbox = get_then_delete
    else:
        harness.client.deleting[sandbox.name] = time.monotonic() + 0.2
    since = len(harness.events)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        observation = await harness.manager.reconcile(running, profile)
        harness.client.get_sandbox = real_get

        assert observation.observed_state is ResidentObservedState.PENDING
        assert observation.backend_ref == {}
        assert _condition(observation, "SandboxReady").reason == "SandboxGone"
        assert not any(
            event.startswith(("stopped:", "linked:", "written:", "launched:"))
            for event in harness.events[since:]
        )

        deployed = await harness.manager.deploy(_awaiting_deployment(runtime), profile)

    assert deployed.observed_state is ResidentObservedState.ACTIVE
    assert deployed.backend_ref["workspace_claim"] == claim
    assert harness.client.sandboxes[deployed.backend_ref["name"]].links[WORKSPACE] == VOLUME
    later = harness.events[since:]
    assert not any(event.startswith(("archived:", "restored:", "cleared:")) for event in later)
    files = harness.client.claim_files[claim]
    assert (files["AGENTS.md"], files["memory/notes.md"]) == (b"# agents", b"kept")
    assert _claim_annotations(harness, runtime) == annotations
    assert not any("back on its workspace volume" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_a_repair_that_keeps_failing_is_logged_once(monkeypatch, caplog):
    harness = _Harness(monkeypatch)
    runtime, profile = _resident_runtime(), _resident_profile()
    running, sandbox = await _off_its_volume(harness, runtime, profile)
    real_exec = harness.client.exec_script
    broken = {"on": True}

    def not_ready(*, sandbox, script, env):
        if broken["on"] and "echo mounted" in script:
            raise RuntimeError("sandbox is not ready")
        return real_exec(sandbox=sandbox, script=script, env=env)

    harness.client.exec_script = not_ready
    caplog.clear()

    with caplog.at_level(logging.INFO):
        for _ in range(3):
            await harness.manager.reconcile(running, profile)
        broken["on"] = False
        await harness.manager.reconcile(running, profile)

    failures = [message for message in _warnings(caplog) if message.startswith("Could not put")]
    assert failures == [
        f"Could not put resident OpenShell Muninn's sandbox {sandbox.name} back on its "
        "workspace volume; retrying on every pass: sandbox is not ready"
    ]
    assert sandbox.links[WORKSPACE] == VOLUME
    assert (
        f"Resident OpenShell Muninn runs on its workspace volume again in sandbox "
        f"{sandbox.name}" in caplog.messages
    )


@pytest.mark.asyncio
async def test_a_claim_from_before_resource_admission_gets_the_attach_labels(monkeypatch):
    """The gateway refuses to mount a claim without them, so an old claim is labelled."""
    harness = _Harness(monkeypatch)
    runtime = _resident_runtime()
    claim = harness.manager._resident_workspace_claim(runtime, _resident_profile())
    unlabelled = workspace_module.ResidentWorkspaceClaims(
        harness.manager._get_core_api, NAMESPACE, attach_labels={}
    )
    pvc, created = await unlabelled.ensure(runtime, claim)
    assert created
    assert "openshell.ai/sandbox-attachable" not in pvc.metadata.labels

    pvc, created = await harness.manager._workspace_claims.ensure(runtime, claim)

    assert not created
    labels = harness.core.pvcs[claim.name].metadata.labels
    assert labels["openshell.ai/sandbox-attachable"] == "true"
    assert labels["openshell.ai/sandbox-attachable-workspace"] == "default"
    assert labels["niuu.world/resident-id"] == str(runtime.id)
    assert harness.core.pvcs[claim.name].spec.resources.requests == {"storage": "2Gi"}


@pytest.mark.asyncio
async def test_attach_labels_follow_the_gateways_required_labels(monkeypatch):
    harness = _Harness(monkeypatch, sandbox_attachable_labels={"example.com/approved": "yes"})
    runtime = _resident_runtime()
    claim = harness.manager._resident_workspace_claim(runtime, _resident_profile())

    await harness.manager._workspace_claims.ensure(runtime, claim)

    labels = harness.core.pvcs[claim.name].metadata.labels
    assert labels["example.com/approved"] == "yes"
    assert "openshell.ai/sandbox-attachable" not in labels

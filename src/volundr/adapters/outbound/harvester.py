"""Harvester VM provider using its documented HTTP APIs.

No kubectl, cluster-admin token minting, or provider credentials in guests.
The caller supplies an HttpAuthPort and a trusted CA for private certificates.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import ipaddress
import json
import re
import ssl
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from niuu.ports.http_auth import HttpAuthPort
from volundr.domain.compute import (
    Machine,
    MachineOwnershipError,
    MachineProvider,
    MachineProviderError,
    MachineRequest,
    MachineState,
)

INSTALLATION_LABEL = "compute.niuu.io/installation"
ALLOCATION_LABEL = "compute.niuu.io/allocation"
REQUEST_ANNOTATION = "compute.niuu.io/request-sha256"
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


def _name(value: str) -> str:
    if len(value) > 63 or not _NAME.fullmatch(value):
        raise ValueError("Harvester resource names must be DNS labels of at most 63 characters")
    return value


def _reference(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if len(parts) != 2:
        raise ValueError("Harvester references must use namespace/name")
    namespace, name = parts
    # Kubernetes object names are DNS subdomains; imported image names can contain dots.
    if len(name) > 253:
        raise ValueError("Harvester object names must be DNS subdomains of at most 253 characters")
    for label in name.split("."):
        _name(label)
    return _name(namespace), name


class HarvesterMachineProfile(BaseModel):
    """Operator-owned infrastructure mapping, never supplied by a session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: str
    network: str
    cpu: int = Field(ge=1)
    memory_mib: int = Field(ge=1)
    disk_gib: int = Field(ge=1)
    architecture: str = Field(default="amd64", pattern=r"^(amd64|arm64)$")
    access_mode: str = Field(default="ReadWriteMany", pattern=r"^ReadWrite(Many|Once)$")
    cloud_init: dict[str, JsonValue] = Field(default_factory=dict, repr=False)


class HarvesterMachineProvider(MachineProvider):
    """Namespace-scoped API adapter, loaded through dynamic adapter configuration."""

    def __init__(
        self,
        *,
        base_url: str,
        namespace: str,
        installation_id: str,
        profiles: dict[str, dict],
        auth: HttpAuthPort,
        cloud_init: dict | None = None,
        ca_file: str | None = None,
        timeout_seconds: float = 30,
        page_size: int = 100,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Harvester base_url must be an HTTPS origin or trusted proxy prefix")
        if timeout_seconds <= 0 or page_size <= 0:
            raise ValueError("Harvester timeout_seconds and page_size must be positive")
        self._namespace = _name(namespace)
        self._installation = _name(installation_id)
        self._profiles = {
            name: HarvesterMachineProfile.model_validate(value) for name, value in profiles.items()
        }
        if not self._profiles:
            raise ValueError("Configure at least one Harvester machine profile")
        for profile in self._profiles.values():
            _reference(profile.image)
            _reference(profile.network)
        self._auth = auth
        self._cloud_init = TypeAdapter(dict[str, JsonValue]).validate_python(
            cloud_init if cloud_init is not None else {}
        )
        self._page_size = page_size
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            verify=ssl.create_default_context(cafile=ca_file),
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )
        self._vm_path = f"apis/kubevirt.io/v1/namespaces/{self._namespace}/virtualmachines"
        self._vmi_path = f"apis/kubevirt.io/v1/namespaces/{self._namespace}/virtualmachineinstances"
        self._core = f"api/v1/namespaces/{self._namespace}"

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        # Existing OAuth adapters may perform blocking token exchange; keep it off the event loop.
        headers = await asyncio.to_thread(self._auth.headers)
        if not any(key.lower() == "authorization" and value for key, value in headers.items()):
            raise MachineProviderError(
                "Harvester authentication is missing; configure provider auth"
            )
        try:
            response = await self._client.request(method, path, headers=headers, **kwargs)
            if response.status_code == 401 and await asyncio.to_thread(self._auth.invalidate):
                headers = await asyncio.to_thread(self._auth.headers)
                if not any(k.lower() == "authorization" and v for k, v in headers.items()):
                    raise MachineProviderError(
                        "Harvester credential refresh returned no credential"
                    )
                response = await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError:
            # Requests can contain secret bootstrap data; never include request/response bodies.
            raise MachineProviderError(
                f"Harvester {method} transport failed; reconcile the allocation before retrying"
            ) from None
        if response.status_code in {401, 403}:
            raise MachineProviderError(
                f"Harvester denied {method} (HTTP {response.status_code}); "
                "check auth and namespace RBAC"
            )
        return response

    @staticmethod
    def _body(response: httpx.Response) -> dict:
        if not response.is_success:
            raise MachineProviderError(f"Harvester API returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise MachineProviderError("Harvester API returned invalid JSON") from None
        if not isinstance(data, dict):
            raise MachineProviderError("Harvester API returned an unexpected response")
        return data

    def _labels(self, allocation_id: UUID) -> dict[str, str]:
        return {INSTALLATION_LABEL: self._installation, ALLOCATION_LABEL: str(allocation_id)}

    def _owned(self, obj: dict, allocation_id: UUID) -> None:
        metadata = obj.get("metadata", {})
        labels = metadata.get("labels", {})
        if metadata.get("namespace") != self._namespace or any(
            labels.get(key) != value for key, value in self._labels(allocation_id).items()
        ):
            raise MachineOwnershipError("Harvester resource is not owned by this allocation")

    async def _read(self, path: str) -> dict | None:
        response = await self._request("GET", path)
        if response.status_code == 404:
            return None
        return self._body(response)

    async def _ensure(self, path: str, obj: dict, allocation_id: UUID) -> dict:
        resource_path = f"{path}/{obj['metadata']['name']}"
        existing = await self._read(resource_path)
        if existing is None:
            response = await self._request("POST", path, json=obj)
            if response.status_code != 409:
                existing = self._body(response)
            else:
                existing = await self._read(resource_path)
        if existing is None:
            raise MachineProviderError(
                "Harvester create conflicted but the resource is unavailable"
            )
        self._owned(existing, allocation_id)
        if existing.get("metadata", {}).get("deletionTimestamp"):
            raise MachineProviderError("Harvester allocation is being deleted; do not reuse its ID")
        expected = obj["metadata"]["annotations"][REQUEST_ANNOTATION]
        if existing.get("metadata", {}).get("annotations", {}).get(REQUEST_ANNOTATION) != expected:
            raise MachineProviderError("Allocation ID was already used with a different request")
        return existing

    async def create(self, request: MachineRequest) -> Machine:
        profile = self._profiles.get(request.profile)
        if profile is None:
            raise ValueError(f"Unknown Harvester machine profile: {request.profile}")
        # Include infrastructure mapping in identity: a profile change cannot alter an old request.
        digest = hashlib.sha256(
            json.dumps(
                {
                    "request": request.model_dump(mode="json"),
                    "profile": profile.model_dump(),
                    "cloud_init": self._cloud_init,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        name = self._resource_name(request.allocation_id)
        metadata = {
            "namespace": self._namespace,
            "labels": self._labels(request.allocation_id),
            "annotations": {REQUEST_ANNOTATION: digest},
        }
        image_namespace, image_name = _reference(profile.image)
        image = await self._read(
            f"apis/harvesterhci.io/v1beta1/namespaces/{image_namespace}/virtualmachineimages/"
            f"{image_name}"
        )
        storage_class = (image or {}).get("status", {}).get("storageClassName")
        if not storage_class:
            raise MachineProviderError(
                "Harvester image has no ready storage class; check image import"
            )
        network_namespace, network_name = _reference(profile.network)
        network = await self._read(
            f"apis/k8s.cni.cncf.io/v1/namespaces/{network_namespace}/network-attachment-definitions/"
            f"{network_name}"
        )
        if network is None:
            raise MachineProviderError("Configured Harvester network does not exist")
        cloud_config = self._bootstrap_config(profile, request)
        user_data = "#cloud-config\n" + yaml.safe_dump(cloud_config, sort_keys=False)
        await self._ensure(
            f"{self._core}/secrets",
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {**metadata, "name": f"{name}-init"},
                "immutable": True,
                "type": "Opaque",
                "data": {"userdata": base64.b64encode(user_data.encode()).decode()},
            },
            request.allocation_id,
        )
        await self._ensure(
            f"{self._core}/persistentvolumeclaims",
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    **metadata,
                    "name": f"{name}-root",
                    "annotations": {
                        **metadata["annotations"],
                        "harvesterhci.io/imageId": profile.image,
                    },
                },
                "spec": {
                    "accessModes": [profile.access_mode],
                    "volumeMode": "Block",
                    "storageClassName": storage_class,
                    "resources": {"requests": {"storage": f"{profile.disk_gib}Gi"}},
                },
            },
            request.allocation_id,
        )
        vm = {
            "apiVersion": "kubevirt.io/v1",
            "kind": "VirtualMachine",
            "metadata": {**metadata, "name": name},
            "spec": {
                "runStrategy": "Always",
                "template": {
                    "metadata": {"labels": self._labels(request.allocation_id)},
                    "spec": {
                        "architecture": profile.architecture,
                        "domain": {
                            "cpu": {"cores": profile.cpu, "sockets": 1, "threads": 1},
                            "memory": {"guest": f"{profile.memory_mib}Mi"},
                            "resources": {"requests": {"memory": f"{profile.memory_mib}Mi"}},
                            "devices": {
                                "disks": [
                                    {"name": "root", "disk": {"bus": "virtio"}, "bootOrder": 1},
                                    {"name": "cloudinit", "disk": {"bus": "virtio"}},
                                ],
                                "interfaces": [{"name": "nic", "model": "virtio", "bridge": {}}],
                            },
                        },
                        "networks": [{"name": "nic", "multus": {"networkName": profile.network}}],
                        "volumes": [
                            {
                                "name": "root",
                                "persistentVolumeClaim": {"claimName": f"{name}-root"},
                            },
                            {
                                "name": "cloudinit",
                                "cloudInitNoCloud": {"secretRef": {"name": f"{name}-init"}},
                            },
                        ],
                    },
                },
            },
        }
        await self._ensure(self._vm_path, vm, request.allocation_id)
        machine = await self.get(request.allocation_id)
        if machine is None:
            raise MachineProviderError("Harvester allocation disappeared after create")
        return machine

    def _bootstrap_config(self, profile: HarvesterMachineProfile, request: MachineRequest) -> dict:
        """Defaults, profile overrides, then portable bootstrap in declared order.

        Ordered cloud-init lists append. Other top-level keys are replaced by
        the profile. Duplicate file paths fail instead of hiding bootstrap steps.
        """
        result = copy.deepcopy(self._cloud_init)
        append_keys = {"write_files", "runcmd", "bootcmd", "packages", "ssh_authorized_keys"}
        portable = {
            "write_files": [file.model_dump() for file in request.bootstrap.files],
            "runcmd": [list(command) for command in request.bootstrap.commands],
            "ssh_authorized_keys": list(request.bootstrap.ssh_authorized_keys),
        }
        for source in (profile.cloud_init, portable):
            for key, value in source.items():
                if key not in append_keys:
                    result[key] = copy.deepcopy(value)
                    continue
                previous = result.get(key, [])
                if not isinstance(previous, list) or not isinstance(value, list):
                    raise ValueError(f"Harvester cloud_init.{key} must be a list")
                result[key] = copy.deepcopy(previous + value)
        # Validate default-only list keys as well as those merged above.
        for key in append_keys.intersection(result):
            if not isinstance(result[key], list):
                raise ValueError(f"Harvester cloud_init.{key} must be a list")
        paths = []
        for item in result.get("write_files", []):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("Harvester cloud_init.write_files entries require a path")
            paths.append(item["path"])
        if len(paths) != len(set(paths)):
            raise ValueError("Harvester cloud_init has duplicate bootstrap file paths")
        return result

    @staticmethod
    def _resource_name(allocation_id: UUID) -> str:
        return f"niuu-{allocation_id.hex}"

    async def get(self, allocation_id: UUID) -> Machine | None:
        name = self._resource_name(allocation_id)
        vm = await self._read(f"{self._vm_path}/{name}")
        if vm is None:
            # Include partial allocation resources after a crash between API operations.
            for kind, suffix in (("secrets", "init"), ("persistentvolumeclaims", "root")):
                resource = await self._read(f"{self._core}/{kind}/{name}-{suffix}")
                if resource is not None:
                    self._owned(resource, allocation_id)
                    return Machine(
                        allocation_id=allocation_id,
                        resource_id=name,
                        state=MachineState.PROVISIONING,
                    )
            return None
        self._owned(vm, allocation_id)
        if vm.get("metadata", {}).get("deletionTimestamp"):
            return Machine(
                allocation_id=allocation_id, resource_id=name, state=MachineState.DELETING
            )
        vmi = await self._read(f"{self._vmi_path}/{name}")
        addresses: list[str] = []
        state = MachineState.PROVISIONING
        if vmi is not None:
            self._owned(vmi, allocation_id)
            status = vmi.get("status", {})
            if status.get("phase") == "Failed":
                state = MachineState.FAILED
            elif status.get("phase") == "Succeeded":
                state = MachineState.STOPPED
            elif status.get("phase") == "Running" and vm.get("status", {}).get("ready") is True:
                state = MachineState.RUNNING
            for interface in status.get("interfaces", []):
                for value in interface.get("ipAddresses", []):
                    address = ipaddress.ip_address(value)
                    if (
                        not address.is_loopback
                        and not address.is_unspecified
                        and not address.is_link_local
                    ):
                        addresses.append(str(address))
        if any(
            condition.get("type") == "Failure" and condition.get("status") == "True"
            for condition in vm.get("status", {}).get("conditions", [])
        ):
            state = MachineState.FAILED
        return Machine(
            allocation_id=allocation_id,
            resource_id=name,
            state=state,
            addresses=tuple(dict.fromkeys(addresses)),
        )

    async def _inventory(self, path: str) -> list[dict]:
        params = {
            "labelSelector": f"{INSTALLATION_LABEL}={self._installation}",
            "limit": str(self._page_size),
        }
        result: list[dict] = []
        while True:
            page = self._body(await self._request("GET", path, params=params))
            result.extend(page.get("items", []))
            continuation = page.get("metadata", {}).get("continue")
            if not continuation:
                return result
            params["continue"] = continuation

    async def list(self) -> list[Machine]:
        allocation_ids: set[UUID] = set()
        for path in (
            self._vm_path,
            f"{self._core}/secrets",
            f"{self._core}/persistentvolumeclaims",
        ):
            for item in await self._inventory(path):
                raw_id = item.get("metadata", {}).get("labels", {}).get(ALLOCATION_LABEL)
                if not raw_id:
                    raise MachineOwnershipError("Managed Harvester resource lacks an allocation ID")
                allocation_id = UUID(raw_id)
                self._owned(item, allocation_id)
                allocation_ids.add(allocation_id)
        machines = []
        for allocation_id in sorted(allocation_ids):
            machine = await self.get(allocation_id)
            if machine is not None:
                machines.append(machine)
        return machines

    async def _delete(self, path: str, allocation_id: UUID) -> bool:
        obj = await self._read(path)
        if obj is None:
            return True
        self._owned(obj, allocation_id)
        metadata = obj["metadata"]
        uid = metadata.get("uid")
        if not uid:
            raise MachineProviderError("Harvester resource has no UID; refusing unsafe deletion")
        if not metadata.get("deletionTimestamp"):
            response = await self._request(
                "DELETE",
                path,
                json={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": uid, "resourceVersion": metadata["resourceVersion"]},
                },
            )
            if response.status_code != 404:
                self._body(response)
        return await self._read(path) is None

    async def delete(self, allocation_id: UUID) -> bool:
        name = self._resource_name(allocation_id)
        if not await self._delete(f"{self._vm_path}/{name}", allocation_id):
            return False
        # Never erase a root disk while the guest is still attached to it.
        vmi = await self._read(f"{self._vmi_path}/{name}")
        if vmi is not None:
            self._owned(vmi, allocation_id)
            return False
        disk_deleted = await self._delete(
            f"{self._core}/persistentvolumeclaims/{name}-root", allocation_id
        )
        secret_deleted = await self._delete(f"{self._core}/secrets/{name}-init", allocation_id)
        return disk_deleted and secret_deleted

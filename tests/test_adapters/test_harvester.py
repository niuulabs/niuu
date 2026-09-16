"""Harvester API contract tests; the HTTP server here is explicitly a test fake."""

from __future__ import annotations

import base64
import copy
from uuid import UUID, uuid4

import httpx
import pytest
import yaml

from niuu.adapters.outbound.http_auth import (
    FileBearerTokenAuthAdapter,
    StaticBearerTokenAuthAdapter,
)
from volundr.adapters.outbound.harvester import (
    ALLOCATION_LABEL,
    INSTALLATION_LABEL,
    HarvesterMachineProvider,
)
from volundr.domain.compute import (
    BootstrapFile,
    MachineBootstrap,
    MachineOwnershipError,
    MachineProviderError,
    MachineRequest,
    MachineState,
)


class HarvesterAPI:
    def __init__(self):
        self.objects = {}
        self.requests = []
        self.denied = None
        self.fail_create_vm = False
        self.delay_delete = False
        self.conflict = False
        self.prefix = "/proxy/"

    def handle(self, request):
        self.requests.append(request)
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.url.path.startswith(self.prefix)
        path = request.url.path.removeprefix(self.prefix)
        if self.denied:
            return httpx.Response(self.denied, json={"message": "do-not-expose-secret"})
        if request.method == "GET" and "/virtualmachineimages/" in path:
            return httpx.Response(200, json={"status": {"storageClassName": "image-storage"}})
        if request.method == "GET" and "/network-attachment-definitions/" in path:
            return httpx.Response(200, json={"metadata": {"name": "lan"}})
        if request.method == "GET":
            if path in self.objects:
                return httpx.Response(200, json=self.objects[path])
            if path.endswith(("/virtualmachines", "/secrets", "/persistentvolumeclaims")):
                items = [v for k, v in self.objects.items() if k.rsplit("/", 1)[0] == path]
                return httpx.Response(200, json={"items": items, "metadata": {}})
            return httpx.Response(404, json={})
        if request.method == "POST":
            if self.fail_create_vm and path.endswith("/virtualmachines"):
                raise httpx.ReadTimeout("private-body", request=request)
            import json

            body = json.loads(request.content)
            if path.endswith("/virtualmachines"):
                assert body["spec"]["template"]["spec"]["domain"]["memory"]["guest"] == "2048Mi"
            body["metadata"].update(uid=str(uuid4()), resourceVersion="1")
            self.objects[f"{path}/{body['metadata']['name']}"] = body
            if self.conflict:
                return httpx.Response(409, json={})
            return httpx.Response(201, json=body)
        if request.method == "DELETE":
            import json

            options = json.loads(request.content)
            assert options["preconditions"]["uid"] == self.objects[path]["metadata"]["uid"]
            assert options["preconditions"]["resourceVersion"] == "1"
            if self.delay_delete:
                self.objects[path]["metadata"]["deletionTimestamp"] = "2026-09-16T00:00:00Z"
            else:
                self.objects.pop(path)
            return httpx.Response(200, json={"status": "Success"})
        raise AssertionError(request)


@pytest.fixture
def api():
    return HarvesterAPI()


@pytest.fixture
def provider(api):
    return HarvesterMachineProvider(
        base_url="https://harvester.test/proxy",
        namespace="test-vms",
        installation_id="test-installation",
        profiles={
            "small": {
                "image": "images/ubuntu-24.04",
                "network": "test-vms/lan",
                "cpu": 2,
                "memory_mib": 2048,
                "disk_gib": 10,
            }
        },
        auth=StaticBearerTokenAuthAdapter(token="test-only-token"),
        transport=httpx.MockTransport(api.handle),
    )


async def test_real_api_shape_idempotency_inventory_and_cleanup(provider, api):
    request = MachineRequest(
        allocation_id=uuid4(),
        profile="small",
        bootstrap=MachineBootstrap(
            files=(BootstrapFile(path="/etc/niuu/config", content="guest-only-secret"),),
            commands=(("systemctl", "enable", "--now", "qemu-guest-agent"),),
        ),
    )
    machine = await provider.create(request)
    assert machine.state == MachineState.PROVISIONING
    assert "guest-only-secret" not in repr(request)
    assert len(api.objects) == 3
    secret = next(v for k, v in api.objects.items() if "/secrets/" in k)
    config = yaml.safe_load(base64.b64decode(secret["data"]["userdata"]))
    assert config["write_files"][0]["permissions"] == "0600"
    assert config["write_files"][0]["content"] == "guest-only-secret"
    assert "test-only-token" not in str(config)
    vm = next(v for k, v in api.objects.items() if "/virtualmachines/" in k)
    assert vm["spec"]["template"]["spec"]["volumes"][1]["cloudInitNoCloud"]["secretRef"]
    assert vm["metadata"]["labels"][ALLOCATION_LABEL] == str(request.allocation_id)
    assert "userdata" not in str(vm)
    assert await provider.create(request) == machine
    assert len([r for r in api.requests if r.method == "POST"]) == 3
    assert await provider.list() == [machine]
    assert await provider.delete(request.allocation_id)
    assert await provider.delete(request.allocation_id)
    assert not api.objects
    await provider.close()


async def test_partial_create_is_discoverable_and_removable(provider, api):
    request = MachineRequest(allocation_id=uuid4(), profile="small")
    api.fail_create_vm = True
    with pytest.raises(MachineProviderError, match="reconcile") as exc:
        await provider.create(request)
    assert "private-body" not in str(exc.value)
    assert len(api.objects) == 2
    assert (await provider.list())[0].allocation_id == request.allocation_id
    assert await provider.delete(request.allocation_id)
    assert not api.objects
    await provider.close()


async def test_resume_after_ambiguous_create_does_not_duplicate_resources(provider, api):
    request = MachineRequest(allocation_id=uuid4(), profile="small")
    api.fail_create_vm = True
    with pytest.raises(MachineProviderError):
        await provider.create(request)
    api.fail_create_vm = False
    await provider.create(request)
    assert len(api.objects) == 3
    assert len([r for r in api.requests if r.method == "POST" and "/secrets" in r.url.path]) == 1
    await provider.close()


@pytest.mark.parametrize("status", [401, 403, 500, 302])
async def test_auth_server_and_redirect_failures_are_not_hidden(provider, api, status):
    api.denied = status
    with pytest.raises(MachineProviderError) as exc:
        await provider.create(MachineRequest(allocation_id=uuid4(), profile="small"))
    assert "do-not-expose-secret" not in str(exc.value)
    assert not api.objects
    await provider.close()


async def test_changed_request_and_foreign_resources_are_never_adopted(provider, api):
    request = MachineRequest(allocation_id=uuid4(), profile="small")
    await provider.create(request)
    changed = request.model_copy(update={"bootstrap": MachineBootstrap(commands=(("true",),))})
    with pytest.raises(MachineProviderError, match="different request"):
        await provider.create(changed)
    for obj in api.objects.values():
        obj["metadata"]["labels"][INSTALLATION_LABEL] = "someone-else"
    with pytest.raises(MachineOwnershipError):
        await provider.get(request.allocation_id)
    with pytest.raises(MachineOwnershipError):
        await provider.delete(request.allocation_id)
    assert len(api.objects) == 3
    await provider.close()


async def test_delete_waits_for_vm_and_guest_before_erasing_disk(provider, api):
    request = MachineRequest(allocation_id=uuid4(), profile="small")
    await provider.create(request)
    api.delay_delete = True
    assert not await provider.delete(request.allocation_id)
    assert len(api.objects) == 3
    vm_path = next(k for k in api.objects if "/virtualmachines/" in k)
    vm = api.objects.pop(vm_path)
    vmi_path = vm_path.replace("/virtualmachines/", "/virtualmachineinstances/")
    api.objects[vmi_path] = vm
    assert not await provider.delete(request.allocation_id)
    assert len(api.objects) == 3
    api.objects.pop(vmi_path)
    api.delay_delete = False
    assert await provider.delete(request.allocation_id)
    await provider.close()


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("Running", MachineState.RUNNING),
        ("Failed", MachineState.FAILED),
        ("Succeeded", MachineState.STOPPED),
        ("Pending", MachineState.PROVISIONING),
    ],
)
async def test_vm_state_and_guest_addresses(provider, api, phase, expected):
    request = MachineRequest(allocation_id=uuid4(), profile="small")
    await provider.create(request)
    vm_path = next(k for k in api.objects if "/virtualmachines/" in k)
    vm = api.objects[vm_path]
    vm["status"] = {"ready": True}
    api.objects[vm_path.replace("/virtualmachines/", "/virtualmachineinstances/")] = {
        "metadata": copy.deepcopy(vm["metadata"]),
        "status": {"phase": phase, "interfaces": [{"ipAddresses": ["10.20.0.3", "127.0.0.1"]}]},
    }
    result = await provider.get(request.allocation_id)
    assert result.state == expected
    assert result.addresses == ("10.20.0.3",)
    await provider.close()


async def test_create_conflict_resolves_same_owned_request(provider, api):
    api.conflict = True
    await provider.create(MachineRequest(allocation_id=uuid4(), profile="small"))
    assert len(api.objects) == 3
    await provider.close()


async def test_cloud_init_defaults_profile_and_portable_steps_preserve_order(provider, api):
    provider._cloud_init = {
        "package_update": True,
        "packages": ["qemu-guest-agent"],
        "runcmd": [["systemctl", "enable", "--now", "qemu-guest-agent"]],
        "write_files": [{"path": "/etc/default-step", "content": "default"}],
        "timezone": "UTC",
    }
    provider._profiles["small"] = provider._profiles["small"].model_copy(
        update={
            "cloud_init": {
                "packages": ["git"],
                "runcmd": [["install", "-d", "/opt/niuu"]],
                "timezone": "America/Toronto",
            }
        }
    )
    request = MachineRequest(
        allocation_id=uuid4(),
        profile="small",
        bootstrap=MachineBootstrap(
            files=(BootstrapFile(path="/etc/session-step", content="session"),),
            commands=(("systemctl", "start", "niuu-session"),),
        ),
    )
    await provider.create(request)
    secret = next(v for k, v in api.objects.items() if "/secrets/" in k)
    config = yaml.safe_load(base64.b64decode(secret["data"]["userdata"]))
    assert config["packages"] == ["qemu-guest-agent", "git"]
    assert config["runcmd"] == [
        ["systemctl", "enable", "--now", "qemu-guest-agent"],
        ["install", "-d", "/opt/niuu"],
        ["systemctl", "start", "niuu-session"],
    ]
    assert [f["path"] for f in config["write_files"]] == ["/etc/default-step", "/etc/session-step"]
    assert config["timezone"] == "America/Toronto"
    assert config["package_update"] is True
    assert provider._cloud_init["packages"] == ["qemu-guest-agent"]
    provider._cloud_init["timezone"] = "Europe/Brussels"
    with pytest.raises(MachineProviderError, match="different request"):
        await provider.create(request)
    await provider.close()


@pytest.mark.parametrize(
    "defaults",
    [
        {"packages": "git"},
        {"runcmd": "true"},
        {"write_files": ["invalid"]},
        {"write_files": [{"path": "/etc/repeated", "content": "first"}]},
    ],
)
async def test_invalid_cloud_init_fails_before_creating_resources(provider, api, defaults):
    provider._cloud_init = defaults
    request = MachineRequest(
        allocation_id=uuid4(),
        profile="small",
        bootstrap=MachineBootstrap(files=(BootstrapFile(path="/etc/repeated", content="second"),)),
    )
    with pytest.raises(ValueError, match="cloud_init"):
        await provider.create(request)
    assert not api.objects
    await provider.close()


def test_file_bearer_rotation_and_invalid_files(tmp_path):
    path = tmp_path / "token"
    auth = FileBearerTokenAuthAdapter(token_file=str(path))
    with pytest.raises(FileNotFoundError):
        auth.headers()
    path.write_text("first\n")
    assert auth.headers() == {"Authorization": "Bearer first"}
    path.write_text("second")
    assert auth.invalidate()
    assert auth.headers() == {"Authorization": "Bearer second"}
    path.write_text("invalid token")
    with pytest.raises(ValueError, match="invalid whitespace"):
        auth.headers()


async def test_token_rotation_after_rejection(provider, api, tmp_path):
    path = tmp_path / "token"
    path.write_text("expired")
    provider._auth = FileBearerTokenAuthAdapter(token_file=str(path))

    def handle(request):
        if request.headers["authorization"] == "Bearer expired":
            path.write_text("renewed")
            return httpx.Response(401)
        return api.handle(request)

    await provider.close()
    provider._client = httpx.AsyncClient(
        base_url="https://harvester.test/proxy/", transport=httpx.MockTransport(handle)
    )
    assert await provider.get(UUID(int=1)) is None
    assert all(r.headers["authorization"] == "Bearer renewed" for r in api.requests)
    await provider.close()


async def test_profile_catalog_exposes_only_safe_details(provider):
    provider._cloud_init = {"runcmd": ["private-bootstrap-content"]}
    profiles = await provider.profiles()
    assert len(profiles) == 1
    assert profiles[0].name == "small"
    assert profiles[0].details == {
        "Image": "images/ubuntu-24.04",
        "CPU": "2",
        "Memory": "2048 MiB",
        "Disk": "10 GiB",
        "Network": "test-vms/lan",
        "Architecture": "amd64",
        "Cloud-init": "Configured",
    }
    assert "private-bootstrap-content" not in profiles[0].model_dump_json()
    assert "test-only-token" not in profiles[0].model_dump_json()
    await provider.close()

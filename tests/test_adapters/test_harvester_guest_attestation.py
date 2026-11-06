"""Harvester guest attestation: the launcher pod's token proves which VM is asking.

The Harvester API here is a test fake that answers TokenReviews, pods and VMIs.
"""

from __future__ import annotations

import base64
import json
from uuid import uuid4

import httpx
import pytest
import yaml

from niuu.adapters.outbound.http_auth import StaticBearerTokenAuthAdapter
from tests.test_adapters.test_harvester import HarvesterAPI
from volundr.adapters.outbound.guest_identity_provider import ProviderAttestedGuestIdentity
from volundr.adapters.outbound.harvester import (
    ALLOCATION_LABEL,
    GUEST_IDENTITY_DIRECTORY,
    GUEST_IDENTITY_VOLUME,
    GUEST_TOKEN_PATH,
    INSTALLATION_LABEL,
    REQUEST_ANNOTATION,
    HarvesterMachineProvider,
)
from volundr.domain.compute import MachineProviderError, MachineRequest
from volundr.domain.guest_credentials import GuestAuthenticationError, GuestRequest

NAMESPACE = "test-vms"
SERVICE_ACCOUNT = "niuu-guest"
PROFILES = {
    "small": {
        "image": "images/ubuntu-24.04",
        "network": "test-vms/lan",
        "cpu": 2,
        "memory_mib": 2048,
        "disk_gib": 10,
    }
}


def make_provider(transport: httpx.AsyncBaseTransport, **kwargs) -> HarvesterMachineProvider:
    return HarvesterMachineProvider(
        base_url="https://harvester.test/proxy",
        namespace=NAMESPACE,
        installation_id="test-installation",
        profiles=PROFILES,
        auth=StaticBearerTokenAuthAdapter(token="test-only-token"),
        transport=transport,
        **kwargs,
    )


class AttestationAPI:
    """One running VM: its VMI, its launcher pod and a TokenReview for its token."""

    def __init__(self) -> None:
        self.allocation_id = uuid4()
        self.vmi_name = f"niuu-{self.allocation_id.hex}"
        self.pod_name = f"virt-launcher-{self.vmi_name}-abcde"
        self.pod_uid = str(uuid4())
        self.review_status = 201
        self.review = {
            "authenticated": True,
            "user": {
                "username": f"system:serviceaccount:{NAMESPACE}:{SERVICE_ACCOUNT}",
                "extra": {
                    "authentication.kubernetes.io/pod-name": [self.pod_name],
                    "authentication.kubernetes.io/pod-uid": [self.pod_uid],
                },
            },
        }
        self.pod: dict | None = {
            "metadata": {
                "name": self.pod_name,
                "uid": self.pod_uid,
                "ownerReferences": [
                    {"kind": "VirtualMachineInstance", "name": self.vmi_name, "controller": True}
                ],
            }
        }
        self.vmi: dict | None = {
            "metadata": {
                "name": self.vmi_name,
                "namespace": NAMESPACE,
                "labels": {
                    INSTALLATION_LABEL: "test-installation",
                    ALLOCATION_LABEL: str(self.allocation_id),
                },
            },
            "status": {"activePods": {self.pod_uid: "node-a"}},
        }
        self.reviewed_tokens: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/proxy/")
        if request.method == "POST" and path == "apis/authentication.k8s.io/v1/tokenreviews":
            body = json.loads(request.content)
            self.reviewed_tokens.append(body["spec"]["token"])
            if self.review_status >= 400:
                return httpx.Response(self.review_status, json={})
            return httpx.Response(201, json={**body, "status": self.review})
        if (
            request.method == "GET"
            and path == f"api/v1/namespaces/{NAMESPACE}/pods/{self.pod_name}"
        ):
            return httpx.Response(200, json=self.pod) if self.pod else httpx.Response(404)
        vmi_path = f"apis/kubevirt.io/v1/namespaces/{NAMESPACE}/virtualmachineinstances/"
        if request.method == "GET" and path.startswith(vmi_path):
            if self.vmi is None or path.removeprefix(vmi_path) != self.vmi["metadata"]["name"]:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self.vmi)
        return httpx.Response(404, json={})


@pytest.fixture
def api() -> AttestationAPI:
    return AttestationAPI()


@pytest.fixture
async def provider(api: AttestationAPI):
    provider = make_provider(httpx.MockTransport(api.handle), guest_service_account=SERVICE_ACCOUNT)
    yield provider
    await provider.close()


async def test_a_launcher_pod_token_proves_its_allocation(provider, api) -> None:
    assert await provider.attested_allocation("pod-token") == api.allocation_id
    assert api.reviewed_tokens == ["pod-token"]


def unauthenticated(api):
    api.review["authenticated"] = False


def other_service_account(api):
    api.review["user"]["username"] = f"system:serviceaccount:{NAMESPACE}:default"


def not_pod_bound(api):
    api.review["user"]["extra"] = {}


def two_pod_names(api):
    api.review["user"]["extra"]["authentication.kubernetes.io/pod-name"].append("other")


def pod_gone(api):
    api.pod = None


def pod_replaced(api):
    api.pod["metadata"]["uid"] = str(uuid4())


def pod_without_vmi_owner(api):
    api.pod["metadata"]["ownerReferences"] = []


def vmi_gone(api):
    api.vmi = None


def vmi_of_another_installation(api):
    api.vmi["metadata"]["labels"][INSTALLATION_LABEL] = "elsewhere"


def vmi_without_allocation(api):
    del api.vmi["metadata"]["labels"][ALLOCATION_LABEL]


def vmi_named_for_another_allocation(api):
    api.vmi["metadata"]["labels"][ALLOCATION_LABEL] = str(uuid4())


def pod_claims_a_vmi_it_does_not_run(api):
    api.vmi["status"]["activePods"] = {str(uuid4()): "node-a"}


@pytest.mark.parametrize(
    "mutate",
    [
        unauthenticated,
        other_service_account,
        not_pod_bound,
        two_pod_names,
        pod_gone,
        pod_replaced,
        pod_without_vmi_owner,
        vmi_gone,
        vmi_of_another_installation,
        vmi_without_allocation,
        vmi_named_for_another_allocation,
        pod_claims_a_vmi_it_does_not_run,
    ],
)
async def test_every_unproven_token_is_the_same_rejection(provider, api, mutate) -> None:
    mutate(api)

    with pytest.raises(GuestAuthenticationError) as rejected:
        await provider.attested_allocation("pod-token")

    assert rejected.value.args == ()


async def test_a_token_review_forge_may_not_make_is_a_provider_error(provider, api) -> None:
    api.review_status = 403

    with pytest.raises(MachineProviderError, match="check auth and namespace RBAC"):
        await provider.attested_allocation("pod-token")


async def test_attestation_is_refused_when_not_configured(api) -> None:
    provider = make_provider(httpx.MockTransport(api.handle))

    assert not provider.attests_guests
    with pytest.raises(MachineProviderError, match="set guest_service_account"):
        await provider.attested_allocation("pod-token")
    assert api.reviewed_tokens == []
    await provider.close()


def test_guest_service_account_must_be_a_dns_label() -> None:
    with pytest.raises(ValueError, match="DNS labels"):
        make_provider(
            httpx.MockTransport(lambda r: httpx.Response(404)), guest_service_account="A_B"
        )


async def test_vms_share_the_launcher_token_and_mount_it_every_boot() -> None:
    harvester = HarvesterAPI()
    provider = make_provider(
        httpx.MockTransport(harvester.handle), guest_service_account=SERVICE_ACCOUNT
    )
    request = MachineRequest(allocation_id=uuid4(), profile="small")

    await provider.create(request)

    vm = next(v for k, v in harvester.objects.items() if "/virtualmachines/" in k)
    spec = vm["spec"]["template"]["spec"]
    assert spec["domain"]["devices"]["filesystems"] == [
        {"name": GUEST_IDENTITY_VOLUME, "virtiofs": {}}
    ]
    assert {
        "name": GUEST_IDENTITY_VOLUME,
        "serviceAccount": {"serviceAccountName": SERVICE_ACCOUNT},
    } in spec["volumes"]
    secret = next(v for k, v in harvester.objects.items() if "/secrets/" in k)
    config = yaml.safe_load(base64.b64decode(secret["data"]["userdata"]))
    [mount] = config["bootcmd"]
    assert f"mount -t virtiofs -o ro {GUEST_IDENTITY_VOLUME} {GUEST_IDENTITY_DIRECTORY}" in mount[2]
    assert GUEST_TOKEN_PATH.startswith(GUEST_IDENTITY_DIRECTORY + "/")
    await provider.close()


async def test_without_attestation_vms_and_their_request_digest_are_unchanged() -> None:
    plain, attested = HarvesterAPI(), HarvesterAPI()
    request = MachineRequest(allocation_id=uuid4(), profile="small")
    for api, kwargs in ((plain, {}), (attested, {"guest_service_account": SERVICE_ACCOUNT})):
        provider = make_provider(httpx.MockTransport(api.handle), **kwargs)
        await provider.create(request)
        await provider.close()

    def vm(api):
        return next(v for k, v in api.objects.items() if "/virtualmachines/" in k)

    assert "filesystems" not in vm(plain)["spec"]["template"]["spec"]["domain"]["devices"]
    plain_digest = vm(plain)["metadata"]["annotations"][REQUEST_ANNOTATION]
    assert plain_digest != vm(attested)["metadata"]["annotations"][REQUEST_ANNOTATION]


# --- the identity adapter ---------------------------------------------------------


async def test_the_identity_presents_the_bearer_token_to_the_provider(provider, api) -> None:
    identity = ProviderAttestedGuestIdentity(machine_provider=provider)
    request = GuestRequest(
        method="POST", path="/x", headers={"Authorization": "Bearer pod-token"}, body=b"{}"
    )

    assert await identity.authenticate(request) == api.allocation_id
    assert identity.agent_auth(api.allocation_id) == {
        "kind": "bearer_file",
        "token_path": GUEST_TOKEN_PATH,
    }
    assert identity.agent_mounts() == (
        {"source": GUEST_IDENTITY_DIRECTORY, "target": GUEST_IDENTITY_DIRECTORY, "readonly": True},
    )


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}],
    ids=["missing", "basic", "empty"],
)
async def test_a_request_without_a_bearer_token_never_reaches_harvester(
    provider, api, headers
) -> None:
    identity = ProviderAttestedGuestIdentity(machine_provider=provider)

    with pytest.raises(GuestAuthenticationError):
        await identity.authenticate(GuestRequest(method="POST", path="/x", headers=headers))

    assert api.reviewed_tokens == []


async def test_the_identity_needs_a_provider_configured_to_attest(api) -> None:
    provider = make_provider(httpx.MockTransport(api.handle))

    with pytest.raises(ValueError, match="set guest_service_account"):
        ProviderAttestedGuestIdentity(machine_provider=provider)
    await provider.close()


def test_the_identity_needs_a_provider_that_can_attest() -> None:
    with pytest.raises(ValueError, match="does not. Use HostKeyGuestIdentity"):
        ProviderAttestedGuestIdentity(machine_provider=object())

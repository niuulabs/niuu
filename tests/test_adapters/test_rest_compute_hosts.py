"""The registered-host REST routes: admin management and the two host-facing routes."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from tests.compute_host_fakes import (
    HOST_KEY,
    POOL,
    Connector,
    HostPool,
    HostRepository,
    Inspector,
    Issuer,
    SlotLedger,
    make_execution,
    make_host,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_compute_hosts import (
    create_compute_hosts_router,
    create_host_join_router,
)
from volundr.domain.compute import ComputePoolPolicy, ComputeSlot
from volundr.domain.compute_hosts import HostHealth, HostService
from volundr.domain.services.compute_hosts import ComputeHostService

ADMIN = Principal(user_id="admin", email="a@x", tenant_id="tenant", roles=["volundr:admin"])
USER = Principal(user_id="user", email="u@x", tenant_id="tenant", roles=["volundr:developer"])


class Pool:
    def __init__(self):
        self.capacity_shortfall = None
        self.provider = SimpleNamespace(tag_sets=self._tag_sets)

    async def _tag_sets(self):
        return (("gpu",), ())

    async def policy(self):
        return ComputePoolPolicy(profile="bm", max_machines=4, warm_min=4, reuse_policy="reuse")


def _client(*hosts, principal=ADMIN, issuer=None, pin=None, bindings=None):
    repository = HostRepository(*hosts)
    issuer = issuer or Issuer()
    service = ComputeHostService(
        pool_id=POOL,
        repository=repository,
        compute_repository=SlotLedger(bindings),
        compute_pool=HostPool(repository),
        connectors={"direct-ssh": Connector()},
        inspector=Inspector(),
        executions={
            "host@1": make_execution(),
            "docker@1": make_execution(execution="docker@1", requires_root_daemon=True),
        },
        token_issuer=issuer,
        join_url="https://forge.example",
        join_pin=pin,
        pairing_code_ttl_seconds=600,
        check_interval_seconds=300,
        pending_check_interval_seconds=5,
        reserved_cpu=4,
        reserved_memory_mib=8192,
    )
    app = FastAPI()
    app.include_router(create_compute_hosts_router(service, Pool()))
    app.include_router(create_host_join_router(service))
    app.dependency_overrides[extract_principal] = lambda: principal
    return TestClient(app), repository, issuer


def _scoped(code: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {code}"}


class TestAdmin:
    def test_list_shows_hosts_summary_and_choices(self):
        held = make_host(tags=("gpu",), allocation_id=uuid4())
        client, *_ = _client(held)
        body = client.get("/api/v1/forge/compute/hosts").json()
        [host] = body["hosts"]
        assert body["can_manage"] is True
        assert host["state"] == "in_service"
        assert host["runtime"] == "host"
        assert host["connection"]["kind"] == "fake"
        assert host["slots_free"] == 4
        assert host["host_key_fingerprint"].startswith("SHA256:")
        assert host["suggested_slots"]["slots"] == 7
        summary = body["summary"]
        assert summary["slots_total"] == 4
        assert summary["free_by_tag_set"] == [{"tags": ["gpu"], "free": 4}]
        assert summary["warm_targets"][0] == {
            "tags": [],
            "match": "all",
            "min": 4,
            "free": 4,
            "met": True,
        }
        assert body["connectors"] == [
            {"binding_id": "direct-ssh", "kind": "fake", "label": "Fake connector"}
        ]
        refused = next(r for r in body["runtimes"] if r["execution"] == "docker@1")
        assert refused["allowed"] is False
        assert "root daemon" in refused["refusal"]
        assert refused["platforms"] == ["linux"]

    def test_a_non_admin_reads_the_list_without_operator_only_detail(self):
        held = make_host(tags=("gpu",), allocation_id=uuid4())
        session = ComputeSlot(
            id=uuid4(),
            allocation_id=held.allocation_id,
            slot=0,
            pool_id=POOL,
            session_id=uuid4(),
            tenant_id="tenant",
            owner_id="someone-else",
        )
        client, *_ = _client(held, bindings={held.allocation_id: [session]})
        client.post(
            "/api/v1/forge/compute/hosts/pairing-codes",
            json={"execution": "host@1", "connector": "direct-ssh"},
        )
        assert client.get("/api/v1/forge/compute/hosts").json()["pairing_codes"]
        client.app.dependency_overrides[extract_principal] = lambda: USER

        response = client.get("/api/v1/forge/compute/hosts")

        assert response.status_code == 200
        body = response.json()
        assert body["can_manage"] is False
        [host] = body["hosts"]
        assert host["name"] == held.name
        assert host["slots_free"] == 3
        assert body["summary"]["slots_total"] == 4
        for operator_only in ("pairing_codes", "connectors", "runtimes"):
            assert operator_only not in body
        assert "last_error" not in body["summary"]
        for operator_only in (
            "findings",
            "host_key_fingerprint",
            "registered_by",
            "suggested_slots",
        ):
            assert operator_only not in host
        assert host["connection"]["kind"] == "fake"
        assert "target" not in host["connection"]
        assert "last_error" not in host["connection"]
        assert host["sessions"] == [{"slot": 0, "state": "starting"}]
        assert str(session.session_id) not in response.text

    def test_every_mutation_is_refused_for_a_non_admin(self):
        host = make_host()
        client, *_ = _client(host, principal=USER)
        base = "/api/v1/forge/compute/hosts"
        refused = [
            client.post(
                f"{base}/pairing-codes",
                json={"execution": "host@1", "connector": "direct-ssh"},
            ),
            client.delete(f"{base}/pairing-codes/{uuid4()}"),
            client.patch(f"{base}/{host.id}", json={"tags": ["x"]}),
            client.post(f"{base}/{host.id}/drain", json={}),
            client.post(f"{base}/{host.id}/resume"),
            client.post(f"{base}/{host.id}/reprepare"),
            client.post(f"{base}/{host.id}/check"),
            client.delete(f"{base}/{host.id}"),
        ]
        assert [response.status_code for response in refused] == [403] * len(refused)

    def test_tag_sets_for_targeting(self):
        client, *_ = _client(principal=USER)
        assert client.get("/api/v1/forge/compute/hosts/tag-sets").json() == {
            "tag_sets": [["gpu"], []]
        }

    def test_mint_then_revoke(self):
        client, repository, _ = _client()
        minted = client.post(
            "/api/v1/forge/compute/hosts/pairing-codes",
            json={"execution": "host@1", "connector": "direct-ssh", "tags": ["gpu"]},
        )
        assert minted.status_code == 201
        body = minted.json()
        assert body["code"] in body["command"]
        assert body["tags"] == ["gpu"]
        listed = client.get("/api/v1/forge/compute/hosts").json()["pairing_codes"]
        assert [code["id"] for code in listed] == [body["id"]]
        assert (
            client.delete(f"/api/v1/forge/compute/hosts/pairing-codes/{body['id']}").status_code
            == 204
        )
        assert (
            client.delete(f"/api/v1/forge/compute/hosts/pairing-codes/{body['id']}").status_code
            == 409
        )

    @pytest.mark.parametrize(
        ("principal", "payload", "status"),
        [
            (USER, {"execution": "host@1", "connector": "direct-ssh"}, 403),
            (ADMIN, {"execution": "docker@1", "connector": "direct-ssh"}, 422),
            (ADMIN, {"execution": "host@1", "connector": "nope"}, 422),
            (ADMIN, {"execution": "host@1", "connector": "direct-ssh", "slots": 0}, 422),
        ],
    )
    def test_mint_refusals(self, principal, payload, status):
        client, *_ = _client(principal=principal)
        response = client.post("/api/v1/forge/compute/hosts/pairing-codes", json=payload)
        assert response.status_code == status

    def test_patch_drain_resume_check_and_remove(self):
        host = make_host()
        client, repository, _ = _client(host)
        base = f"/api/v1/forge/compute/hosts/{host.id}"

        assert client.patch(base, json={"tags": ["fast-disk"]}).json()["tags"] == ["fast-disk"]
        assert client.patch(base, json={"slots": 99}).status_code == 422
        assert client.post(f"{base}/drain", json={}).json()["state"] == "out_of_service"
        assert client.post(f"{base}/resume").json()["state"] == "ready"
        assert client.post(f"{base}/check").json()["health"] == "healthy"
        assert client.post(f"{base}/reprepare").json()["health"] == "healthy"
        removed = client.delete(base).json()
        assert removed == {"removed": True, "host_cleanup_command": None}
        assert client.post(f"{base}/check").status_code == 404

    def test_state_conflicts_are_409(self):
        host = make_host(allocation_id=uuid4())
        client, *_ = _client(host)
        response = client.patch(f"/api/v1/forge/compute/hosts/{host.id}", json={"slots": 2})
        assert response.status_code == 409
        drained = client.post(f"/api/v1/forge/compute/hosts/{host.id}/drain", json={"remove": True})
        assert drained.json()["remove_when_empty"] is True
        assert client.post(f"/api/v1/forge/compute/hosts/{host.id}/resume").status_code == 409
        assert client.post(f"/api/v1/forge/compute/hosts/{host.id}/reprepare").status_code == 409

    def test_unknown_host_is_404(self):
        client, *_ = _client()
        assert client.delete(f"/api/v1/forge/compute/hosts/{uuid4()}").status_code == 404


class TestHostFacing:
    def _mint(self, client):
        return client.post(
            "/api/v1/forge/compute/hosts/pairing-codes",
            json={"execution": "host@1", "connector": "direct-ssh", "slots": 2},
        ).json()["code"]

    def test_join_script_needs_a_usable_code(self):
        client, *_ = _client()
        assert client.get("/api/v1/forge/compute/hosts/join.sh").status_code == 401
        assert (
            client.get("/api/v1/forge/compute/hosts/join.sh", headers=_scoped("x")).status_code
            == 401
        )
        code = self._mint(client)
        response = client.get("/api/v1/forge/compute/hosts/join.sh", headers=_scoped(code))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/x-shellscript")
        assert response.headers["cache-control"] == "no-store"
        assert "FORGE_URL=https://forge.example" in response.text
        assert code in response.text

    def test_mint_answers_the_join_url_and_pin(self):
        client, *_ = _client(pin="sha256//abc=")
        body = client.post(
            "/api/v1/forge/compute/hosts/pairing-codes",
            json={"execution": "host@1", "connector": "direct-ssh"},
        ).json()
        assert body["join_url"] == "https://forge.example"
        assert body["pin"] == "sha256//abc="
        assert "--pinnedpubkey sha256//abc=" in body["command"]
        assert body["command_wget"] is None

    def test_mint_without_a_pin_answers_a_null_pin_and_a_wget_line(self):
        client, *_ = _client()
        body = client.post(
            "/api/v1/forge/compute/hosts/pairing-codes",
            json={"execution": "host@1", "connector": "direct-ssh"},
        ).json()
        assert body["pin"] is None
        assert body["command_wget"].startswith("wget ")

    def test_a_pinned_join_script_posts_with_the_same_pin(self):
        client, *_ = _client(pin="sha256//abc=")
        code = self._mint(client)
        script = client.get("/api/v1/forge/compute/hosts/join.sh", headers=_scoped(code)).text
        assert "PIN=sha256//abc=" in script
        assert '--pinnedpubkey "$PIN"' in script

    def test_the_admin_router_alone_serves_no_host_facing_route(self):
        app = FastAPI()
        app.include_router(
            create_compute_hosts_router(
                ComputeHostService(
                    pool_id=POOL,
                    repository=(repository := HostRepository()),
                    compute_repository=SlotLedger(),
                    compute_pool=HostPool(repository),
                    connectors={"direct-ssh": Connector()},
                    inspector=Inspector(),
                    executions={"host@1": make_execution()},
                    token_issuer=Issuer(),
                    join_url="https://forge.example",
                    join_pin=None,
                    pairing_code_ttl_seconds=600,
                    check_interval_seconds=300,
                    pending_check_interval_seconds=5,
                    reserved_cpu=4,
                    reserved_memory_mib=8192,
                ),
                Pool(),
            )
        )
        paths = app.openapi()["paths"]
        assert "/api/v1/forge/compute/hosts/pairing-codes" in paths
        assert "/api/v1/forge/compute/hosts/join.sh" not in paths
        assert "/api/v1/forge/compute/hosts/join" not in paths

    def test_mint_offers_the_script_to_read_first_with_its_digest(self):
        client, *_ = _client()
        minted = client.post(
            "/api/v1/forge/compute/hosts/pairing-codes",
            json={"execution": "host@1", "connector": "direct-ssh"},
        ).json()
        script = minted["script"]
        served = client.get("/api/v1/forge/compute/hosts/join.sh", headers=_scoped(minted["code"]))
        assert script["content"] == served.text
        assert script["sha256"] == hashlib.sha256(served.content).hexdigest()
        download, verify, read, run = script["commands"]
        assert download.startswith("curl -fsSL -o niuu-join.sh -H ")
        assert minted["code"] in download
        assert verify == f"echo '{script['sha256']}  niuu-join.sh' | sha256sum -c -"
        assert (read, run) == ("less niuu-join.sh", "sudo sh niuu-join.sh")

    def test_join_registers_and_answers_what_join_sh_needs(self):
        client, repository, _ = _client()
        code = self._mint(client)
        response = client.post(
            "/api/v1/forge/compute/hosts/join",
            headers=_scoped(code),
            json={
                "name": "glad",
                "public_key": HOST_KEY,
                "address": "10.0.0.9",
                "facts": {"platform": "linux"},
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "glad"
        assert body["pool_id"] == POOL
        assert body["ssh_user"] == "root"
        assert body["setup"] == {"files": [], "commands": []}
        [host] = repository.hosts.values()
        assert host.health is HostHealth.PENDING
        again = client.post(
            "/api/v1/forge/compute/hosts/join",
            headers=_scoped(code),
            json={"name": "other", "public_key": HOST_KEY, "address": "10.0.0.9"},
        )
        assert again.status_code == 401

    def test_join_refusals(self):
        client, *_ = _client()
        code = self._mint(client)
        bad = client.post(
            "/api/v1/forge/compute/hosts/join",
            headers=_scoped(code),
            json={"name": "glad", "public_key": "ssh-rsa AAAA", "address": "10.0.0.9"},
        )
        assert bad.status_code == 422
        assert (
            client.post(
                "/api/v1/forge/compute/hosts/join",
                json={"name": "glad", "public_key": HOST_KEY},
            ).status_code
            == 401
        )

    def test_a_token_with_another_scope_is_refused_before_the_route(self):
        client, *_ = _client()
        other = jwt.encode(
            {"token_use": "valkyrie_build", "scopes": ["node_join"]}, "k" * 32, algorithm="HS256"
        )
        response = client.get("/api/v1/forge/compute/hosts/join.sh", headers=_scoped(other))
        assert response.status_code == 403

    def test_removing_a_draining_host_reports_state(self):
        host = make_host(service=HostService.DRAINING, allocation_id=uuid4())
        client, *_ = _client(host)
        listed = client.get("/api/v1/forge/compute/hosts").json()["hosts"][0]
        assert listed["state"] == "draining"
        assert listed["slots_free"] == 0

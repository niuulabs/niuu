"""Exercise the pinned SDK's real messages, isolated from legacy module doubles.

The adapter unit tests replace the SDK with lightweight fakes, so they cannot
notice a field the pinned protocol renamed or removed. Every check here builds
the request Völundr sends with the installed ``openshell`` wheel, round-trips it
through the wire format, and reads it back as the gateway would. The adapter
module is imported afresh against the real SDK for each check (other test
modules import it against their doubles) and dropped again afterwards.
"""

import importlib
import sys

import pytest

_GATEWAY_MODULE = "volundr.adapters.outbound.openshell_gateway"
CHECKS = (
    "sandbox_lifecycle",
    "create_sandbox_request",
    "deletion_outcomes",
    "service_requests",
    "exec_requests",
    "interactive_exec_request",
    "tcp_forward_request",
    "sandbox_logs",
    "provider_grants",
    "provider_profiles",
    "profile_equivalence_ignores_output_only_fields",
    "sandbox_policy_endpoints",
    "sandbox_metadata_from_proto",
    "legacy_resume_failure_names_the_remedy",
)


@pytest.fixture
def real_gateway():
    pytest.importorskip("openshell")
    sys.modules.pop(_GATEWAY_MODULE, None)
    for name in ("grpc", "google.protobuf", "openshell._proto.openshell_pb2"):
        module = importlib.import_module(name)
        assert getattr(module, "__file__", None), f"{name} is a test double, not the SDK"
    gateway = importlib.import_module(_GATEWAY_MODULE)
    yield gateway
    sys.modules.pop(_GATEWAY_MODULE, None)


@pytest.mark.parametrize("check", CHECKS)
def test_gateway_requests_match_pinned_protocol(real_gateway, check: str) -> None:
    globals()[f"check_{check}"]()


# -- helpers ---------------------------------------------------------------------------

PLATFORM_PROFILE_RPCS = frozenset(
    {"GetProviderProfile", "ImportProviderProfiles", "DeleteProviderProfile"}
)


def _wire(request):
    """The request as the gateway decodes it."""
    return type(request).FromString(request.SerializeToString())


def _client(stub):
    from unittest.mock import Mock

    from volundr.adapters.outbound.openshell_gateway import OpenShellGatewayClient

    client = OpenShellGatewayClient(token_provider=Mock(token=Mock(return_value="token")))
    client._stub = stub
    return client


def _assert_default_workspace(request) -> None:
    assert request.workspace_scope.WhichOneof("selection") == "workspace"
    assert request.workspace_scope.workspace == "default"


def _sandbox(name: str = "sandbox", sandbox_id: str = "target"):
    from openshell._proto import datamodel_pb2 as d
    from openshell._proto import openshell_pb2 as p

    return p.Sandbox(metadata=d.ObjectMeta(id=sandbox_id, name=name))


class _UnaryStub:
    """Answers unary RPCs with the declared response type; records wire requests."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, object]] = []
        self.responses = dict(responses or {})

    def __getattr__(self, method):
        from openshell._proto import openshell_pb2 as p

        def call(request, **_kwargs):
            request = _wire(request)
            self.calls.append((method, request))
            if method in PLATFORM_PROFILE_RPCS:
                # Völundr's provider profiles are platform-scoped (the provider's
                # profile_workspace is empty), so these carry no workspace.
                assert not request.HasField("workspace_scope")
            elif "workspace_scope" in request.DESCRIPTOR.fields_by_name:
                _assert_default_workspace(request)
            if method in self.responses:
                return self.responses[method](request)
            output = p.DESCRIPTOR.services_by_name["OpenShell"].methods_by_name[method].output_type
            response = getattr(p, output.name)()
            if "sandbox" in response.DESCRIPTOR.fields_by_name:
                response.sandbox.CopyFrom(_sandbox(name=getattr(request, "name", "sandbox")))
            return response

        return call

    def requests(self, method):
        return [request for name, request in self.calls if name == method]


# -- checks ------------------------------------------------------------------------


def check_sandbox_lifecycle() -> None:
    from openshell._proto import datamodel_pb2 as d
    from openshell._proto import openshell_pb2 as p

    pages = []

    def list_sandboxes(request):
        pages.append(request.page_token)
        assert request.page_size == 100
        response = p.ListSandboxesResponse()
        if not request.page_token:
            response.sandboxes.add(metadata=d.ObjectMeta(id="other"))
            response.next_page_token = "next-page"
            return response
        response.sandboxes.append(_sandbox())
        return response

    stub = _UnaryStub({"ListSandboxes": list_sandboxes})
    client = _client(stub)
    try:
        assert client.get_sandbox("sandbox").name == "sandbox"
        assert client.get_sandbox_by_id("target").id == "target"
        assert pages == ["", "next-page"]  # A short page can still have a successor.
        assert client.get_sandbox_by_id("missing") is None
        client.stop_sandbox("sandbox")
        client.start_sandbox("sandbox")
        for method in ("GetSandbox", "StopSandbox", "StartSandbox"):
            assert [request.name for request in stub.requests(method)] == ["sandbox"]
    finally:
        client.close()


def check_create_sandbox_request() -> None:
    from openshell._proto import openshell_pb2 as p
    from openshell._proto import sandbox_pb2 as s

    from volundr.adapters.outbound.openshell_gateway import (
        _driver_config_from_values,
        _sandbox_policy_from_config,
    )

    stub = _UnaryStub()
    client = _client(stub)
    driver_config = _driver_config_from_values(
        {
            "nodeSelector": {"pool": "gpu"},
            "persistence": {"existingClaim": "workspace", "mountPath": "/sandbox/workspace"},
            "openshell": {
                "volumes": [{"name": "flock-ipc", "empty_dir": {}}],
                "volumeMounts": [
                    {"name": "flock-ipc", "mount_path": "/tmp/niuu-mesh", "read_only": False}
                ],
                "workloads": [
                    {
                        "name": "ravn-reviewer",
                        "image": "ghcr.io/niuulabs/ravn:pinned",
                        "command": ["python", "-m", "ravn", "daemon"],
                        "environment": {"RAVN_PERSONA": "reviewer"},
                        "resources": {"limits": {"memory": "512Mi"}},
                        "volume_mounts": [
                            {
                                "name": "flock-ipc",
                                "mount_path": "/tmp/niuu-mesh",
                                "read_only": False,
                            }
                        ],
                    }
                ],
            },
        },
        workload_start_file="/sandbox/workspace/.volundr/start",
        workload_environment={"HOME": "/sandbox"},
    )
    policy = _sandbox_policy_from_config(
        {
            "version": 1,
            "filesystem": {"read_write": ["/sandbox"]},
            "process": {"run_as_user": "sandbox", "run_as_group": "sandbox"},
            "network_policies": {
                "api": {
                    "endpoints": [
                        {"host": "api.example.com", "port": 443, "protocol": "rest"},
                        {"host": "10.0.0.5", "port": 4222, "tls": "skip"},
                    ],
                    "binaries": [{"path": "/usr/bin/curl"}],
                }
            },
        }
    )
    try:
        created = client.create_sandbox(
            name="forge-1",
            image="ghcr.io/niuulabs/skuld:pinned",
            env={"SKULD__PORT": "9200"},
            labels={"volundr.niuu.io/session": "s-1"},
            annotations={"volundr.niuu.io/image": "ghcr.io/niuulabs/skuld:pinned"},
            pod_labels={"ravn.niuu.world/realm": "realm-1"},
            pod_annotations={"ravn.niuu.world/peer-id": "peer-1"},
            resources={"limits": {"cpu": "1"}},
            driver_config=driver_config,
            providers=("volundr-platform",),
            policy=policy,
        )
        assert created.name == "forge-1"
        (request,) = stub.requests("CreateSandbox")
        assert isinstance(request, p.CreateSandboxRequest)
        # Völundr's metadata stays on the sandbox; only what the Pod needs goes there.
        assert dict(request.labels) == {"volundr.niuu.io/session": "s-1"}
        assert dict(request.annotations) == {
            "volundr.niuu.io/image": "ghcr.io/niuulabs/skuld:pinned"
        }
        template = request.spec.template
        assert dict(template.labels) == {"ravn.niuu.world/realm": "realm-1"}
        assert dict(template.annotations) == {"ravn.niuu.world/peer-id": "peer-1"}
        assert template.image == "ghcr.io/niuulabs/skuld:pinned"
        assert list(request.spec.providers) == ["volundr-platform"]
        kubernetes = template.driver_config.fields["kubernetes"].struct_value
        workload = kubernetes["containers"]["workloads"][0]
        assert set(workload.keys()) == {
            "name",
            "image",
            "command",
            "environment",
            "resources",
            "volume_mounts",
        }
        assert kubernetes["volumes"][1]["empty_dir"] == {}
        endpoints = request.spec.policy.network_policies["api"].endpoints
        assert endpoints[0].tls == s.NETWORK_TLS_MODE_UNSPECIFIED
        assert endpoints[1].tls == s.NETWORK_TLS_MODE_SKIP
    finally:
        client.close()

    rejected = _client(_UnaryStub())
    try:
        rejected.create_sandbox(
            name="forge-2",
            image="img",
            env={},
            labels={},
            pod_labels={"openshell.ai/managed-by": "volundr"},
        )
    except ValueError as exc:
        assert "reserves" in str(exc)
    else:
        raise AssertionError("an openshell.ai pod label must be refused before sending")
    finally:
        rejected.close()


def check_deletion_outcomes() -> None:
    from openshell._proto import openshell_pb2 as p

    for outcome, expected in (
        (p.DELETION_OUTCOME_COMPLETED, True),
        (p.DELETION_OUTCOME_ACCEPTED, True),
        (p.DELETION_OUTCOME_ALREADY_ABSENT, False),
    ):
        stub = _UnaryStub(
            {"DeleteSandbox": lambda _request, o=outcome: p.DeleteSandboxResponse(outcome=o)}
        )
        client = _client(stub)
        try:
            assert client.delete_sandbox("sandbox") is expected
            (request,) = stub.requests("DeleteSandbox")
            assert request.name == "sandbox"
            assert request.allow_missing is True
        finally:
            client.close()

    client = _client(_UnaryStub({"DeleteSandbox": lambda _request: p.DeleteSandboxResponse()}))
    try:
        client.delete_sandbox("sandbox")
    except RuntimeError as exc:
        assert "no deletion outcome" in str(exc)
    else:
        raise AssertionError("an unspecified deletion outcome must not read as success")
    finally:
        client.close()


def check_service_requests() -> None:
    from openshell._proto import openshell_pb2 as p

    stub = _UnaryStub(
        {
            "ExposeService": lambda _request: p.ServiceEndpointResponse(url="https://svc"),
            "DeleteService": lambda _request: p.DeleteServiceResponse(
                outcome=p.DELETION_OUTCOME_ALREADY_ABSENT
            ),
        }
    )
    client = _client(stub)
    try:
        assert client.expose_service(sandbox_name="sandbox", target_port=9200, service="skuld") == (
            "https://svc"
        )
        assert client.delete_service(sandbox_name="sandbox", service="skuld") is False
        (expose,) = stub.requests("ExposeService")
        assert (expose.sandbox, expose.name, expose.target_port, expose.domain) == (
            "sandbox",
            "skuld",
            9200,
            True,
        )
        # Omitted: OpenShell strips the caller's Authorization, as before.
        assert expose.authorization_mode == p.SERVICE_AUTHORIZATION_MODE_UNSPECIFIED
        (delete,) = stub.requests("DeleteService")
        assert (delete.sandbox, delete.name, delete.allow_missing) == ("sandbox", "skuld", True)
    finally:
        client.close()


def _exec_stub(events):
    from openshell._proto import openshell_pb2 as p

    class Stub:
        def __init__(self):
            self.requests = []

        def ExecSandbox(self, request, **_kwargs):  # noqa: N802 - gRPC method name.
            request = _wire(request)
            _assert_default_workspace(request)
            self.requests.append(request)
            return [p.ExecSandboxEvent(**event) for event in events]

    return Stub()


def check_exec_requests() -> None:
    import io

    from openshell._proto import openshell_pb2 as p

    from volundr.adapters.outbound import openshell_gateway as gateway

    events = [
        {"stdout": p.ExecSandboxStdout(data=b"ok\n")},
        {"exit": p.ExecSandboxExit(exit_code=0)},
    ]
    stub = _exec_stub(events)
    client = _client(stub)
    try:
        assert (
            client.exec_detached(
                sandbox="r-1", command=["skuld"], env={"A": "B"}, log_path="/tmp/skuld.log"
            )
            == 0
        )
        assert client.exec_script(sandbox="r-1", script="echo ok\n", env={}) == (0, "ok\n")
        destination = io.BytesIO()
        transfer = client.exec_to_file(
            sandbox="r-1",
            script="cat file",
            destination=destination,
            max_bytes=1024,
            timeout_seconds=90.5,
        )
        assert transfer.exit_code == 0 and destination.getvalue() == b"ok\n"
        client.write_files(sandbox="r-1", files={"/sandbox/.volundr/a": b"a"})
    finally:
        client.close()

    detached, script, to_file, files = stub.requests
    for request in stub.requests:
        assert request.sandbox == "r-1"
        assert request.HasField("execution_timeout")
    assert detached.execution_timeout.seconds == gateway.DETACHED_EXEC_TIMEOUT_SECONDS
    assert script.execution_timeout.seconds == gateway.BOOTSTRAP_TIMEOUT_SECONDS
    assert to_file.execution_timeout.ToNanoseconds() == 90_500_000_000
    assert to_file.no_login_shell is True
    assert files.execution_timeout.seconds == int(gateway.WRITE_FILES_TIMEOUT_SECONDS)
    assert files.command[:2] == ["tar", "-xf"]


def check_interactive_exec_request() -> None:
    import io

    from openshell._proto import openshell_pb2 as p

    frames = []

    class Stub:
        def ExecSandboxInteractive(self, requests, **_kwargs):  # noqa: N802 - gRPC method.
            first = _wire(next(requests))
            frames.append(first)
            frames.append(_wire(next(requests)))
            return [p.ExecSandboxEvent(exit=p.ExecSandboxExit(exit_code=0))]

    client = _client(Stub())
    try:
        transfer = client.exec_from_file(
            sandbox="r-1",
            command=["tar", "-xf", "-"],
            source=io.BytesIO(b"archive"),
            timeout_seconds=30,
        )
    finally:
        client.close()
    assert transfer.exit_code == 0
    start = frames[0].start
    _assert_default_workspace(start)
    assert start.sandbox == "r-1"
    assert start.execution_timeout.seconds == 30
    assert frames[1].stdin == b"archive"


def check_tcp_forward_request() -> None:
    import socket

    from openshell._proto import openshell_pb2 as p

    frames = []

    class Stub:
        def CreateSshSession(self, *_args, **_kwargs):  # noqa: N802 - gRPC method.
            raise AssertionError("TCP forwards need no SSH session token")

        RevokeSshSession = CreateSshSession

        def ForwardTcp(self, requests, **_kwargs):  # noqa: N802 - gRPC method.
            frames.extend(_wire(frame) for frame in requests)
            return [p.TcpForwardFrame(data=b"pong")]

    client = _client(Stub())
    bridge, caller = socket.socketpair()
    caller.sendall(b"ping")
    caller.shutdown(socket.SHUT_WR)
    try:
        client._forward_tcp_connection(bridge, sandbox="r-1", target_port=18789)
        assert caller.recv(16) == b"pong"
    finally:
        client.close()
        bridge.close()
        caller.close()
    init = frames[0].init
    assert (init.sandbox, init.workspace) == ("r-1", "default")
    assert init.WhichOneof("target") == "tcp"
    assert (init.tcp.host, init.tcp.port) == ("127.0.0.1", 18789)
    assert init.authorization_token == ""
    assert frames[1].data == b"ping"

    # Through the local listener Völundr's proxy connects to.
    frames.clear()
    client = _client(Stub())
    forwarder = client.start_tcp_forward(sandbox="r-2", target_port=9200)
    try:
        with socket.create_connection((forwarder.host, forwarder.port), timeout=5) as caller:
            caller.sendall(b"ping")
            caller.shutdown(socket.SHUT_WR)
            assert caller.recv(16) == b"pong"
    finally:
        forwarder.close()
        client.close()
    assert frames[0].init.sandbox == "r-2"
    assert frames[0].init.tcp.port == 9200


def check_sandbox_logs() -> None:
    from google.protobuf import timestamp_pb2
    from openshell._proto import openshell_pb2 as p

    stamp = timestamp_pb2.Timestamp()
    stamp.FromMilliseconds(1_700_000_000_123)

    def logs(_request):
        response = p.GetSandboxLogsResponse(buffer_total=2)
        response.logs.add(event_time=stamp, level="INFO", source="sandbox", message="up")
        response.logs.add(level="WARN", source="gateway", message="no time")
        return response

    stub = _UnaryStub({"GetSandboxLogs": logs})
    client = _client(stub)
    try:
        page = client.get_sandbox_logs("r-1", lines=10, sources=["sandbox"], min_level="INFO")
    finally:
        client.close()
    (request,) = stub.requests("GetSandboxLogs")
    assert (request.sandbox, request.lines, list(request.sources)) == ("r-1", 10, ["sandbox"])
    assert [entry.timestamp_ms for entry in page.entries] == [1_700_000_000_123, 0]
    assert page.buffer_total == 2


def check_provider_grants() -> None:
    from openshell._proto import openshell_pb2 as p

    from volundr.adapters.outbound.openshell_gateway import (
        OpenShellProviderGrant,
        _provider_profile,
    )

    profile = _provider_profile(
        profile_id="volundr-grant",
        env_name="OPENAI_API_KEY",
        token_endpoint="https://volundr.example/token",
    )

    import grpc

    class NotFound(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.NOT_FOUND

    def missing(_request):
        raise NotFound()

    stub = _UnaryStub(
        {
            "GetProviderProfile": missing,
            "GetProvider": missing,
            "ImportProviderProfiles": lambda _request: p.ImportProviderProfilesResponse(
                imported=True
            ),
        }
    )
    client = _client(stub)
    try:
        client.create_provider_grant(
            profile=profile, provider_name="volundr-grant", config={"k": "v"}
        )
        client.delete_provider_grant(
            OpenShellProviderGrant(provider_name="volundr-grant", profile_id="volundr-grant")
        )
    finally:
        client.close()
    (imported,) = stub.requests("ImportProviderProfiles")
    assert imported.profiles[0].profile.id == "volundr-grant"
    (created,) = stub.requests("CreateProvider")
    assert created.provider.metadata.name == "volundr-grant"
    assert dict(created.provider.credentials) == {"OPENAI_API_KEY": ""}
    assert [request.name for request in stub.requests("DeleteProvider")] == ["volundr-grant"]
    assert [request.id for request in stub.requests("DeleteProviderProfile")] == ["volundr-grant"]


def check_provider_profiles() -> None:
    from openshell._proto import openshell_pb2 as p
    from openshell._proto import sandbox_pb2 as s

    from volundr.adapters.outbound.openshell_gateway import (
        _codex_enrollment_profile,
        _platform_provider_profile,
        _provider_profile,
    )

    connection = _wire(
        _provider_profile(
            profile_id="volundr-conn",
            env_name="ANTHROPIC_API_KEY",
            token_endpoint="https://volundr.example/token",
            cache_ttl_seconds=300,
        )
    )
    grant = connection.credentials[0].token_grant
    assert grant.HasField("cache_ttl") and grant.cache_ttl.seconds == 300
    (endpoint,) = connection.endpoints
    assert endpoint.tls == s.NETWORK_TLS_MODE_UNSPECIFIED
    assert endpoint.enforcement == s.NETWORK_ENFORCEMENT_MODE_ENFORCE
    assert endpoint.access == s.NETWORK_ACCESS_PRESET_FULL

    platform = _wire(
        _platform_provider_profile(
            profile_id="volundr-platform",
            token_endpoint="https://volundr.example/token",
            api_urls=("https://volundr.example", "http://mimir.internal:8080"),
        )
    )
    # No override: the gateway caches for the issued token's own expires_in.
    assert not platform.credentials[0].token_grant.HasField("cache_ttl")
    assert [endpoint.tls for endpoint in platform.endpoints] == [
        s.NETWORK_TLS_MODE_UNSPECIFIED,
        s.NETWORK_TLS_MODE_UNSPECIFIED,
    ]
    assert [endpoint.port for endpoint in platform.endpoints] == [443, 8080]

    codex = _wire(_codex_enrollment_profile("volundr-enroll"))
    assert codex.category == p.PROVIDER_PROFILE_CATEGORY_AGENT
    assert {endpoint.tls for endpoint in codex.endpoints} == {s.NETWORK_TLS_MODE_UNSPECIFIED}


def check_profile_equivalence_ignores_output_only_fields() -> None:
    from openshell._proto import openshell_pb2 as p

    from volundr.adapters.outbound.openshell_gateway import _profiles_equivalent, _provider_profile

    expected = _provider_profile(
        profile_id="volundr-conn",
        env_name="OPENAI_API_KEY",
        token_endpoint="https://volundr.example/token",
    )
    stored = p.ProviderProfile()
    stored.CopyFrom(expected)
    stored.resource_version = 7
    stored.credentials[0].token_grant_owners.append("provider:volundr-conn")
    stored.endpoints[0].token_grant_owner = "provider:volundr-conn"
    stored.endpoints[0].provider_credentialed = True
    assert _profiles_equivalent(stored, expected)
    stored.endpoints[0].host = "elsewhere.example"
    assert not _profiles_equivalent(stored, expected)


def check_sandbox_policy_endpoints() -> None:
    from volundr.adapters.outbound.openshell_gateway import (
        _network_endpoint,
        _sandbox_policy_from_config,
    )

    try:
        _network_endpoint(["host"])
    except ValueError as exc:
        assert "must be a mapping" in str(exc)
    else:
        raise AssertionError("a non-mapping endpoint must be refused")

    for removed in ("terminate", "passthrough"):
        try:
            _sandbox_policy_from_config(
                {
                    "version": 1,
                    "network_policies": {
                        "api": {"endpoints": [{"host": "a.example", "port": 443, "tls": removed}]}
                    },
                }
            )
        except ValueError as exc:
            assert "omit tls" in str(exc)
        else:
            raise AssertionError(f"tls: {removed} must be refused with its remedy")


def check_sandbox_metadata_from_proto() -> None:
    from openshell._proto import datamodel_pb2 as d
    from openshell._proto import openshell_pb2 as p

    from volundr.adapters.outbound.openshell_gateway import _sandbox_from_proto

    raw = p.Sandbox(
        metadata=d.ObjectMeta(
            id="id-1",
            name="r-1",
            labels={"volundr.niuu.io/resident": "res-1"},
            annotations={"volundr.niuu.io/image": "img:1"},
        ),
        spec=p.SandboxSpec(
            template=p.SandboxTemplate(image="img:1", annotations={"pod": "only"}),
            providers=["volundr-platform"],
        ),
        status=p.SandboxStatus(
            phase=p.SANDBOX_PHASE_READY,
            conditions=[p.SandboxCondition(type="Ready", status="True")],
        ),
    )
    sandbox = _sandbox_from_proto(_wire(raw))
    assert (sandbox.id, sandbox.name, sandbox.ready, sandbox.image) == (
        "id-1",
        "r-1",
        True,
        "img:1",
    )
    assert sandbox.annotations == {"volundr.niuu.io/image": "img:1"}
    assert sandbox.labels == {"volundr.niuu.io/resident": "res-1"}
    assert sandbox.providers == ("volundr-platform",)


def check_legacy_resume_failure_names_the_remedy() -> None:
    import asyncio

    import grpc

    from volundr.adapters.outbound.openshell_gateway import OpenShellGatewayPodManager

    class Refused(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.FAILED_PRECONDITION

        def details(self):
            return "attachment lacks admission provenance"

    def start(_request):
        raise Refused()

    manager = OpenShellGatewayPodManager(client=_client(_UnaryStub({"StartSandbox": start})))
    try:
        asyncio.run(manager._resume_legacy_sandbox("forge-legacy"))
    except RuntimeError as exc:
        message = str(exc)
        assert "FAILED_PRECONDITION: attachment lacks admission provenance" in message
        assert "older than v0.1.6 cannot be resumed" in message
        assert isinstance(exc.__cause__, Refused)
    else:
        raise AssertionError("a refused resume must raise with its remedy")

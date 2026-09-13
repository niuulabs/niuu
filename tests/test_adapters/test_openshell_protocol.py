"""Exercise the pinned SDK's real messages, isolated from legacy module doubles."""

import subprocess
import sys

import pytest


def test_gateway_requests_match_pinned_protocol() -> None:
    pytest.importorskip("openshell")
    subprocess.run([sys.executable, __file__], check=True, timeout=30)


def _check_protocol() -> None:
    from unittest.mock import Mock

    from openshell._proto import datamodel_pb2 as d
    from openshell._proto import openshell_pb2 as p

    from volundr.adapters.outbound.openshell_gateway import (
        OpenShellGatewayClient,
        OpenShellProviderGrant,
    )

    sandbox = p.Sandbox(metadata=d.ObjectMeta(id="target", name="sandbox"))
    calls = []
    pages = []

    class Stub:
        def __getattr__(self, method):
            def call(request, **_kwargs):
                request = type(request).FromString(request.SerializeToString())
                calls.append(method)
                if "workspace_scope" in request.DESCRIPTOR.fields_by_name:
                    assert request.workspace_scope.WhichOneof("selection") == "workspace"
                    assert request.workspace_scope.workspace == "default"
                response_name = (
                    p.DESCRIPTOR.services_by_name["OpenShell"]
                    .methods_by_name[method]
                    .output_type.name
                )
                response = getattr(p, response_name)()
                if method == "ListSandboxes":
                    pages.append(request.page_token)
                    assert request.page_size == 100
                    if not request.page_token:
                        response.sandboxes.add(metadata=d.ObjectMeta(id="other"))
                        response.next_page_token = "next-page"
                        return response
                    assert request.page_token == "next-page"
                    response.sandboxes.append(sandbox)
                    return response
                if "sandbox" in response.DESCRIPTOR.fields_by_name:
                    response.sandbox.CopyFrom(sandbox)
                if "deleted" in response.DESCRIPTOR.fields_by_name:
                    response.deleted = True
                return response

            return call

    client = OpenShellGatewayClient(token_provider=Mock())
    client._stub = Stub()
    try:
        assert client.create_sandbox(name="sandbox", image="python:3.12", env={}, labels={}).id
        assert client.get_sandbox("sandbox").id == "target"
        assert client.get_sandbox_by_id("target").id == "target"
        assert pages == ["", "next-page"]  # A short page can still have a successor.
        assert client.get_sandbox_by_id("missing") is None
        client.stop_sandbox("sandbox")
        client.start_sandbox("sandbox")
        client.get_provider("provider")
        client.get_sandbox_logs("target", lines=10, sources=[], min_level="INFO")
        client.expose_service(sandbox_name="sandbox", target_port=8080)
        assert client.delete_service(sandbox_name="sandbox", service="skuld")
        assert client.delete_sandbox("sandbox")
        profile = p.ProviderProfile(id="profile")
        client.get_provider_profile = lambda _name: profile
        client.get_provider = lambda _name: None
        client.create_provider_grant(profile=profile, provider_name="provider", config={})
        client.delete_provider_grant(
            OpenShellProviderGrant(provider_name="provider", profile_id="profile")
        )
        assert {"CreateProvider", "DeleteProvider", "GetSandboxLogs"} <= set(calls)
    finally:
        client.close()


if __name__ == "__main__":
    _check_protocol()

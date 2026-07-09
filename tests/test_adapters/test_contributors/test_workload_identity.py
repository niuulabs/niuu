"""Tests for WorkloadIdentityContributor."""

from volundr.adapters.outbound.contributors.workload_identity import WorkloadIdentityContributor
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext


async def test_workload_identity_contributor_projects_audience_scoped_token():
    contributor = WorkloadIdentityContributor(
        audience="volundr-api",
        exchange_url="https://niuu.example/api/v1/tokens/workload/exchange",
    )

    contribution = await contributor.contribute(
        Session(name="session", model="gpt-5.5", source=GitSource()),
        SessionContext(),
    )

    assert contribution.pod_spec is not None
    assert contribution.pod_spec.volumes == (
        {
            "name": "niuu-workload-identity",
            "projected": {
                "sources": [
                    {
                        "serviceAccountToken": {
                            "path": "token",
                            "audience": "volundr-api",
                            "expirationSeconds": 1200,
                        }
                    }
                ]
            },
        },
    )
    assert contribution.pod_spec.volume_mounts == (
        {
            "name": "niuu-workload-identity",
            "mountPath": "/var/run/secrets/niuu-workload",
            "readOnly": True,
        },
    )
    assert {
        "name": "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE",
        "value": "/var/run/secrets/niuu-workload/token",
    } in contribution.pod_spec.env
    assert {
        "name": "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        "value": "https://niuu.example/api/v1/tokens/workload/exchange",
    } in contribution.pod_spec.env


async def test_workload_identity_contributor_skips_openshell_backend():
    contributor = WorkloadIdentityContributor()

    contribution = await contributor.contribute(
        Session(name="session", model="gpt-5.5", source=GitSource()),
        SessionContext(runtime_backend="openshell"),
    )

    assert contribution.values == {}
    assert contribution.pod_spec is None

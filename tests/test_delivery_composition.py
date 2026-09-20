"""Delivery adapters are explicit and provider credentials remain caller scoped."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from niuu.domain.delivery import AcceptancePolicy
from niuu.domain.models import Principal
from niuu.ports.delivery import DeliveryForgeProvider, EvidenceAuthenticator, WorkstreamRepository
from volundr.config import DeliveryConfig
from volundr.main import _create_delivery_service_factory


def configured_delivery():
    return DeliveryConfig(
        enabled=True,
        authenticator={"adapter": "test.Signer", "kwargs": {"key_id": "deployment"}},
        workstreams={"adapter": "test.Workspaces", "kwargs": {"workspace_root": "/workspaces"}},
        forge={"adapter": "test.Forge"},
        authorizer={"adapter": "test.Authorizer"},
        trusted_producers=("runner", "reviewer"),
        policies={
            "reviewed": AcceptancePolicy(
                required_review_roles=("code",),
                required_test_contract_ids=("tests",),
                review_producers={"code": ("reviewer",)},
                test_producers={"tests": ("runner",)},
            )
        },
    )


def test_delivery_disabled_by_default_and_requires_explicit_configuration():
    assert not DeliveryConfig().enabled
    with pytest.raises(ValidationError, match="requires authenticator"):
        DeliveryConfig(enabled=True)
    with pytest.raises(ValidationError, match="pinned producers"):
        DeliveryConfig(
            enabled=True,
            authenticator={"adapter": "test.Signer"},
            workstreams={"adapter": "test.Workspaces"},
        )


def test_dynamic_composition_keeps_forge_credentials_per_principal():
    signer = MagicMock(spec=EvidenceAuthenticator)
    workspaces = MagicMock(spec=WorkstreamRepository)
    forge = MagicMock(spec=DeliveryForgeProvider)
    factories = {
        "test.Signer": MagicMock(return_value=signer),
        "test.Workspaces": MagicMock(return_value=workspaces),
        "test.Forge": MagicMock(return_value=forge),
    }
    integrations = object()
    with patch("volundr.main.import_class", side_effect=factories.__getitem__):
        factory = _create_delivery_service_factory(configured_delivery(), integrations)
        alice = Principal(user_id="alice", email="", tenant_id="one", roles=[])
        bob = Principal(user_id="bob", email="", tenant_id="two", roles=[])
        first, second = factory(alice), factory(bob)
    assert first is not second
    assert factories["test.Workspaces"].call_args.kwargs["authenticator"] is signer
    assert [call.kwargs["principal"] for call in factories["test.Forge"].call_args_list] == [
        alice,
        bob,
    ]
    assert factories["test.Forge"].call_args.kwargs["integrations"] is integrations


@pytest.mark.parametrize("invalid", ["test.Signer", "test.Workspaces", "test.Forge"])
def test_wrong_adapter_contract_fails_composition(invalid):
    instances = {
        "test.Signer": MagicMock(spec=EvidenceAuthenticator),
        "test.Workspaces": MagicMock(spec=WorkstreamRepository),
        "test.Forge": MagicMock(spec=DeliveryForgeProvider),
    }
    instances[invalid] = object()
    with patch(
        "volundr.main.import_class", side_effect=lambda name: lambda **kwargs: instances[name]
    ):
        with pytest.raises(TypeError, match="must implement"):
            factory = _create_delivery_service_factory(configured_delivery(), object())
            factory(Principal(user_id="alice", email="", tenant_id="one", roles=[]))

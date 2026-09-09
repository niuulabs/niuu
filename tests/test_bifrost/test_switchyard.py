"""Real native selection with test-only provider doubles."""

import pytest

pytest.importorskip("switchyard.libsy")

from bifrost.adapters.switchyard import SwitchyardSelection
from bifrost.config import BifrostConfig, ProviderConfig, RoutingStrategy
from bifrost.router import ModelRouter, RouterError
from tests.test_bifrost.test_router import FakeProvider, _make_request


@pytest.mark.parametrize("stream", [False, True])
async def test_native_selection_preserves_request_and_selects_second_provider(stream, caplog):
    selector = SwitchyardSelection(weights={"first": 0, "second": 1}, seed=42)
    router = ModelRouter(
        BifrostConfig(
            routing_strategy=RoutingStrategy.DIRECT,
            providers={name: ProviderConfig(models=["gpt-4o"]) for name in ("first", "second")},
        ),
        selection=selector,
    )
    first, second = FakeProvider(), FakeProvider()
    router._adapters.update(first=first, second=second)
    request = _make_request()
    with caplog.at_level("INFO"):
        if stream:
            assert [chunk async for chunk in router.stream(request)] == ["data: test\n\n"]
            assert second.stream_calls == [(request, "gpt-4o")]
        else:
            await router.complete(request)
            assert second.complete_calls == [(request, "gpt-4o")]
    assert not first.complete_calls and not first.stream_calls
    assert "switchyard decision provider=second" in caplog.text
    assert "outcome_id=" in caplog.text
    await router.close()


async def test_native_selected_provider_failure_does_not_call_another_provider():
    from bifrost.ports.provider import ProviderError

    router = ModelRouter(
        BifrostConfig(
            providers={name: ProviderConfig(models=["gpt-4o"]) for name in ("first", "second")}
        ),
        selection=SwitchyardSelection(weights={"first": 1, "second": 0}),
    )
    first, second = FakeProvider(raises=ProviderError("offline")), FakeProvider()
    router._adapters.update(first=first, second=second)
    with pytest.raises(RouterError, match="offline"):
        await router.complete(_make_request())
    assert not second.complete_calls


@pytest.mark.parametrize("weight", [-1, float("inf"), float("nan")])
def test_invalid_weights_fail(weight):
    with pytest.raises(ValueError, match="weights"):
        SwitchyardSelection(weights={"first": weight})


async def test_empty_candidates_fail():
    with pytest.raises(ValueError, match="candidate"):
        await SwitchyardSelection().select([])


async def test_seed_is_repeatable_and_algorithm_is_reused():
    candidates = [("first", "model"), ("second", "model")]
    a, b = SwitchyardSelection(seed=42), SwitchyardSelection(seed=42)
    sequence_a = [await a.select(candidates) for _ in range(20)]
    assert sequence_a == [await b.select(candidates) for _ in range(20)]
    assert set(sequence_a) == set(candidates)
    assert len(a._algorithms) == 1


def test_composition_loads_real_native_adapter():
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from bifrost.app import create_app

    app = create_app(
        BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            selection={
                "adapter": "bifrost.adapters.switchyard.SwitchyardSelection",
                "seed": 42,
            },
        )
    )
    # Exercise the dynamically composed selector through the public HTTP path.
    with patch("bifrost.router._load_adapter", return_value=FakeProvider()):
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "claude-sonnet-4-6",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            assert response.status_code == 200, response.text


def test_composition_rejects_invalid_adapter():
    from bifrost.app import create_app

    with pytest.raises(TypeError, match="SelectionPort"):
        create_app(BifrostConfig(selection={"adapter": "builtins.dict"}))

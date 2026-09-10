"""Native model routing and classifier HTTP/usage integration."""

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

pytest.importorskip("switchyard.libsy")

from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.adapters.switchyard_models import SwitchyardModelSelection, _denormalize, _normalize
from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.router import ModelRouter
from bifrost.translation.models import AnthropicRequest, Message


def config(classifier=True):
    return BifrostConfig(
        providers={
            "vllm": ProviderConfig(base_url="https://vllm.test", models=["small", "large", "judge"])
        },
        selection={
            "adapter": "bifrost.adapters.switchyard_models.SwitchyardModelSelection",
            "targets": {
                "fast": {"provider": "vllm", "model": "small", "weight": 0},
                "best": {"provider": "vllm", "model": "large", "weight": 1},
                "judge": {"provider": "vllm", "model": "judge"},
            },
            "routes": {
                "auto": {
                    "targets": ["fast", "best"],
                    **(
                        {
                            "judge": "judge",
                            "prompt": "Choose fast for simple tasks and best for complex tasks.",
                        }
                        if classifier
                        else {}
                    ),
                }
            },
            "seed": 42,
        },
    )


def upstream_response(model, text):
    return httpx.Response(
        200,
        json={
            "id": "test",
            "model": model,
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


@pytest.mark.parametrize("classifier", [False, True])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/messages", "/api/chat"])
def test_native_route_uses_model_target_and_accounts_judge(classifier, stream, endpoint):
    store = MemoryUsageStore()
    models = []

    def respond(request):
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"] == "judge":
            assert body["response_format"]["json_schema"]["schema"]["properties"]["target"][
                "enum"
            ] == ["fast", "best"]
            assert not body["stream"]
            return upstream_response("judge", '{"target":"best"}')
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        if not stream:
            return upstream_response("large", "answer")
        events = [
            {
                "id": "test",
                "choices": [
                    {"delta": {"role": "assistant", "content": "answer"}, "finish_reason": None}
                ],
            },
            {
                "id": "test",
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n",
        )

    with patch("bifrost.app._build_usage_store", return_value=store), respx.mock:
        respx.post("https://vllm.test/v1/chat/completions").mock(side_effect=respond)
        with TestClient(create_app(config(classifier))) as client:
            response = client.post(
                endpoint,
                json={
                    "model": "auto",
                    "stream": stream,
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            assert response.status_code == 200, response.text
            assert "answer" in response.text
    assert models == (["judge", "large"] if classifier else ["large"])
    assert [r.model for r in store._records] == models
    assert all(r.provider == "vllm" for r in store._records)
    assert all(r.input_tokens == 10 and r.output_tokens == 5 for r in store._records)


@pytest.mark.parametrize(
    "answer", ['{"target":"missing"}', "not json", '{"target":"best","extra":1}']
)
def test_invalid_classifier_is_billed_but_never_calls_answer(answer):
    store = MemoryUsageStore()
    with patch("bifrost.app._build_usage_store", return_value=store), respx.mock:
        upstream = respx.post("https://vllm.test/v1/chat/completions").mock(
            return_value=upstream_response("judge", answer)
        )
        with TestClient(create_app(config())) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "Hi"}]},
            )
            assert response.status_code == 502
        assert upstream.call_count == 1
    assert [r.model for r in store._records] == ["judge"]


def test_unconfigured_target_fails_startup():
    cfg = config()
    cfg.providers["vllm"].models.remove("large")
    with pytest.raises(ValueError, match="not configured"):
        create_app(cfg)


def test_selected_target_requires_permission():
    from bifrost.config import AgentPermissions

    cfg = config()
    cfg.agent_permissions["anonymous"] = AgentPermissions(allowed_models=["auto", "judge"])
    with respx.mock:
        upstream = respx.post("https://vllm.test/v1/chat/completions").mock(
            return_value=upstream_response("judge", '{"target":"best"}')
        )
        with TestClient(create_app(cfg)) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "Hi"}]},
            )
            assert response.status_code == 403
        assert upstream.call_count == 1


def test_judge_requires_permission_before_network_call():
    from bifrost.config import AgentPermissions

    cfg = config()
    cfg.agent_permissions["anonymous"] = AgentPermissions(allowed_models=["auto", "large"])
    with respx.mock:
        with TestClient(create_app(cfg)) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "Hi"}]},
            )
            assert response.status_code == 403
        assert not respx.calls


def test_judge_spend_can_exhaust_budget_before_answer():
    from bifrost.config import AgentPermissions, PricingOverride, QuotaConfig

    cfg = config()
    cfg.agent_permissions["anonymous"] = AgentPermissions(quota=QuotaConfig(max_cost_per_day=0.001))
    cfg.pricing["judge"] = PricingOverride(input_per_million=1000)
    store = MemoryUsageStore()
    with patch("bifrost.app._build_usage_store", return_value=store), respx.mock:
        upstream = respx.post("https://vllm.test/v1/chat/completions").mock(
            return_value=upstream_response("judge", '{"target":"best"}')
        )
        with TestClient(create_app(cfg)) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "Hi"}]},
            )
            assert response.status_code == 429, response.text
        assert upstream.call_count == 1
    assert store._records[0].cost_usd == 0.01


def test_classifier_transport_failure_does_not_call_answer():
    with respx.mock:
        upstream = respx.post("https://vllm.test/v1/chat/completions").mock(
            return_value=httpx.Response(503)
        )
        with TestClient(create_app(config())) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "Hi"}]},
            )
            assert response.status_code == 502
        assert upstream.call_count == 1


def test_native_structured_output_and_images_reach_provider():
    from bifrost.adapters.anthropic import AnthropicAdapter
    from bifrost.translation.to_openai import anthropic_to_openai

    request = AnthropicRequest.model_validate(
        {
            "model": "judge",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": "https://image.test/photo.png"},
                        }
                    ],
                }
            ],
        }
    )
    request._response_format = {
        "type": "json_schema",
        "json_schema": {"schema": {"type": "object"}},
    }
    payload = anthropic_to_openai(request, "judge")
    assert payload["response_format"] == request._response_format
    assert payload["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "https://image.test/photo.png"}}
    ]
    # Serialization does not require constructing an HTTP client.
    payload = AnthropicAdapter._build_payload(None, request, "judge")
    assert payload["output_config"]["format"] == {
        "type": "json_schema",
        "schema": {"type": "object"},
    }


@pytest.mark.parametrize(
    "route",
    [
        {"targets": ["fast", "fast"]},
        {"targets": ["unknown"]},
        {"targets": ["fast", "best"], "judge": "judge"},
        {"targets": ["fast", "best"], "judge": "judge", "prompt": ""},
    ],
)
def test_invalid_route_configuration_fails_startup(route):
    cfg = config()
    cfg.selection["routes"]["auto"] = route
    with pytest.raises(ValueError):
        create_app(cfg)


@pytest.mark.parametrize(
    "block",
    [
        {"type": "text", "text": "hello"},
        {"type": "thinking", "thinking": "reason"},
        {"type": "image", "source": {"type": "url", "url": "https://image.test/a.png"}},
        {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "hello"}},
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [{"type": "text", "text": "result"}],
            "is_error": False,
        },
    ],
)
def test_classifier_content_roundtrip(block):
    assert _denormalize(_normalize(block)) == block


async def test_random_can_choose_two_models_at_one_endpoint():
    spec = config(False).selection
    spec["targets"]["fast"]["weight"] = 1
    selector = SwitchyardModelSelection(**{k: v for k, v in spec.items() if k != "adapter"})
    router = ModelRouter(config(False), selection=selector)
    choices = [
        (
            await router.prepare(
                AnthropicRequest(model="auto", messages=[Message(role="user", content="Hi")])
            )
        ).model
        for _ in range(30)
    ]
    assert set(choices) == {"small", "large"}

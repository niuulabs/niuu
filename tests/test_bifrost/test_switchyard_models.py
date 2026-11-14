"""Native model routing and classifier HTTP/usage integration."""

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from switchyard.libsy import Step

pytest.importorskip("switchyard.libsy")

from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.adapters.switchyard_models import SwitchyardModelSelection, _denormalize, _normalize
from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.router import ModelRouter
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    Message,
    TextBlock,
    UsageInfo,
)
from niuu import cap_telemetry


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
                            "max_tokens": 512,
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


def _selector(classifier: bool, **route_overrides) -> SwitchyardModelSelection:
    spec = config(classifier).selection
    spec["routes"]["auto"].update(route_overrides)
    return SwitchyardModelSelection(**{k: v for k, v in spec.items() if k != "adapter"})


def _request() -> AnthropicRequest:
    return AnthropicRequest(model="auto", messages=[Message(role="user", content="Hi")])


def _judge_answer(text: str, stop_reason: str = "end_turn") -> AnthropicResponse:
    return AnthropicResponse(
        id="judge-answer",
        content=[TextBlock(text=text)],
        model="judge",
        stop_reason=stop_reason,
        usage=UsageInfo(input_tokens=10, output_tokens=5),
    )


def test_classifier_route_without_max_tokens_is_refused():
    spec = config().selection
    del spec["routes"]["auto"]["max_tokens"]
    with pytest.raises(ValueError, match=r"routes\.<name>\.max_tokens"):
        SwitchyardModelSelection(**{k: v for k, v in spec.items() if k != "adapter"})


def test_max_tokens_on_a_route_without_a_judge_is_refused():
    spec = config(False).selection
    spec["routes"]["auto"]["max_tokens"] = 64
    with pytest.raises(ValueError, match="only on classifier routes"):
        SwitchyardModelSelection(**{k: v for k, v in spec.items() if k != "adapter"})


def test_max_tokens_must_be_positive():
    with pytest.raises(ValueError, match="greater than 0"):
        _selector(True, max_tokens=0)


async def test_judge_answer_cut_off_at_max_tokens_is_a_counted_cap_hit():
    from niuu.cap_telemetry import OutputTruncatedError

    knob = "bifrost.selection.routes.auto.max_tokens"
    cap_telemetry.reset_cap_hits()
    selector = _selector(True)

    async def call_model(provider, model, judge):
        # A cut-off answer: invalid JSON, so only the cap check can report it.
        return _judge_answer('{"target": "be', stop_reason="max_tokens")

    with pytest.raises(OutputTruncatedError, match=knob) as info:
        await selector.route(_request(), call_model)
    assert info.value.knob == knob
    assert "raise" in str(info.value)
    assert cap_telemetry.cap_hit_counts()[knob] == 1


async def test_judge_answer_cut_off_returns_502_through_the_gateway():
    knob = "bifrost.selection.routes.auto.max_tokens"
    cap_telemetry.reset_cap_hits()
    with respx.mock:
        upstream = respx.post("https://vllm.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "test",
                    "model": "judge",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": '{"target":'},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        )
        with TestClient(create_app(config())) as client:
            response = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "Hi"}]},
            )
            assert response.status_code == 502
        assert upstream.call_count == 1
    assert cap_telemetry.cap_hit_counts()[knob] == 1


async def _foreign_done(target: str) -> Step.Done:
    """A real ``Step.Done`` selecting *target*, produced by a route with no judge."""
    spec = config(False).selection
    spec["targets"]["fast"]["weight"] = 1 if target == "fast" else 0
    spec["targets"]["best"]["weight"] = 1 if target == "best" else 0
    other = SwitchyardModelSelection(**{k: v for k, v in spec.items() if k != "adapter"})
    messages = {"messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}]}
    async for step in other._routes["auto"].run_stream(messages):
        if isinstance(step, Step.Done):
            return step
    raise AssertionError("the weighted route produced no Done step")


async def test_done_without_a_judge_answer_is_refused():
    selector = _selector(True)
    done = await _foreign_done("fast")

    async def run_stream(_messages):
        yield done

    async def call_model(provider, model, judge):
        raise AssertionError("the judge must not be called")

    selector._routes["auto"] = type("Route", (), {"run_stream": staticmethod(run_stream)})()
    with pytest.raises(RuntimeError, match="chose fast without the judge's answer"):
        await selector.route(_request(), call_model)


async def test_done_that_differs_from_the_judge_choice_is_refused():
    selector = _selector(True)
    real = selector._routes["auto"]
    done = await _foreign_done("fast")

    class DefaultingRoute:
        async def run_stream(self, messages):
            async for step in real.run_stream(messages):
                if isinstance(step, Step.CallModel):
                    yield step
                    # Switchyard finishes with its default instead of the judge's choice.
                    yield done
                    return

    async def call_model(provider, model, judge):
        return _judge_answer('{"target":"best"}')

    selector._routes["auto"] = DefaultingRoute()
    with pytest.raises(RuntimeError, match="chose fast without the judge's answer"):
        await selector.route(_request(), call_model)


async def test_done_matching_the_judge_choice_selects_that_target():
    selector = _selector(True)

    async def call_model(provider, model, judge):
        return _judge_answer('{"target":"best"}')

    assert await selector.route(_request(), call_model) == ("vllm", "large")

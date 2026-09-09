"""Explicit model targets and native schema-based Switchyard classification."""

import json
import logging
import time

from pydantic import BaseModel, ConfigDict, Field, model_validator
from switchyard.libsy import (
    CustomClassifierConfig,
    LlmClassifierConfig,
    LlmResponse,
    Step,
    algorithms,
)

from bifrost.adapters.switchyard import SwitchyardSelection
from bifrost.ports.selection import ModelCall
from bifrost.translation.models import AnthropicRequest, Message, TextBlock

logger = logging.getLogger(__name__)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    weight: float = Field(default=1, ge=0, allow_inf_nan=False)


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[str] = Field(min_length=1)
    judge: str | None = None
    prompt: str | None = None
    max_tokens: int = Field(default=512, gt=0)

    @model_validator(mode="after")
    def validate_classifier(self):
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("Route targets must be unique")
        if (self.judge is None) != (self.prompt is None):
            raise ValueError("Classifier routes require both judge and prompt")
        if self.prompt is not None and not self.prompt.strip():
            raise ValueError("Classifier prompt must not be empty")
        return self


def _normalize(block: dict) -> dict:
    """Convert a supported Bifrost content block to Switchyard protocol types."""
    match block["type"]:
        case "text":
            return {"type": "text", "text": block["text"]}
        case "thinking":
            return {"type": "reasoning", "text": block["thinking"], "signature": None}
        case "image":
            source = dict(block["source"])
            kind = source.pop("type")
            return {"type": "image", "source": {"type": kind, "data": source}}
        case "tool_use":
            return {
                "type": "tool_call",
                "id": block["id"],
                "name": block["name"],
                "arguments": block["input"],
            }
        case "tool_result":
            content = block["content"]
            return {
                "type": "tool_result",
                "tool_call_id": block["tool_use_id"],
                "content": (
                    [{"type": "text", "text": content}]
                    if isinstance(content, str)
                    else [_normalize(b) for b in content]
                ),
                "is_error": block["is_error"],
            }
        case _:
            raise ValueError(f"Unsupported classifier content: {block['type']}")


def _denormalize(block: dict) -> dict:
    match block["type"]:
        case "text":
            return block
        case "reasoning":
            return {"type": "thinking", "thinking": block["text"]}
        case "image":
            return {
                "type": "image",
                "source": {"type": block["source"]["type"], **block["source"]["data"]},
            }
        case "tool_call":
            return {
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": block["arguments"],
            }
        case "tool_result":
            return {
                "type": "tool_result",
                "tool_use_id": block["tool_call_id"],
                "content": [_denormalize(b) for b in block["content"]],
                "is_error": bool(block.get("is_error")),
            }
        case _:
            raise ValueError(f"Unsupported native classifier content: {block['type']}")


class SwitchyardModelSelection(SwitchyardSelection):
    """Routes without a judge use target weights; routes with a judge classify.

    Each target names a provider and actual model. All answer transport remains
    in Bifrost, and classifier failures propagate instead of using a default.
    """

    def __init__(self, targets: dict, routes: dict, seed: int | None = None):
        super().__init__(seed=seed)
        self.targets = {name: Target.model_validate(value) for name, value in targets.items()}
        self.routes = {name: Route.model_validate(value) for name, value in routes.items()}
        if not self.targets or not self.routes:
            raise ValueError("Model selection requires targets and routes")
        self._routes = {}
        for name, route in self.routes.items():
            for target in [*route.targets, *([route.judge] if route.judge else [])]:
                if target not in self.targets:
                    raise ValueError(f"Route {name} references unknown target {target}")
            if route.judge is None:
                self._routes[name] = algorithms.random(
                    route.targets,
                    weights=[self.targets[t].weight for t in route.targets],
                    seed=seed,
                )
                continue
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["target"],
                "properties": {"target": {"type": "string", "enum": route.targets}},
            }
            self._routes[name] = algorithms.llm_classifier(
                LlmClassifierConfig.custom(
                    route.judge,
                    [(t, t) for t in route.targets],
                    default_target=route.targets[0],
                    config=CustomClassifierConfig(
                        route.prompt, schema, "/target", max_output_tokens=route.max_tokens
                    ),
                )
            )

    def configured_targets(self) -> list[tuple[str, str]]:
        return [(t.provider, t.model) for t in self.targets.values()]

    async def route(self, request: AnthropicRequest, call_model: ModelCall):
        route = self.routes.get(request.model)
        if route is None:
            return None
        normalized = {
            "messages": [
                {
                    "role": message.role,
                    "content": (
                        [{"type": "text", "text": message.content}]
                        if isinstance(message.content, str)
                        else [_normalize(block.model_dump()) for block in message.content]
                    ),
                }
                for message in request.messages
            ]
        }
        # Include the complete request contract as context, while keeping it at
        # user authority: only the operator's routing prompt instructs the judge.
        context = request.model_dump(exclude={"messages", "model", "stream"}, exclude_none=True)
        normalized["messages"].insert(
            0,
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Request configuration: " + json.dumps(context)}
                ],
            },
        )
        started = time.perf_counter()
        async for step in self._routes[request.model].run_stream(normalized):
            if isinstance(step, Step.CallModel):
                call = step.call
                if not call.models or call.models[0] != route.judge:
                    raise RuntimeError("Classifier requested an unexpected target")
                native = call.request
                judge = self.targets[route.judge]
                judge_request = AnthropicRequest(
                    model=judge.model,
                    messages=[
                        Message(role=m["role"], content=[_denormalize(b) for b in m["content"]])
                        for m in native["messages"]
                    ],
                    system="\n".join(
                        b["text"] for i in native["instructions"] for b in i["content"]
                    ),
                    max_tokens=native["output"]["max_output_tokens"],
                    temperature=native["sampling"]["temperature"],
                )
                judge_request._response_format = native["output"]["response_format"]
                response = await call_model(judge.provider, judge.model, judge_request)
                answer = "".join(b.text for b in response.content if isinstance(b, TextBlock))
                choice = json.loads(answer)
                if (
                    not isinstance(choice, dict)
                    or set(choice) != {"target"}
                    or choice["target"] not in route.targets
                ):
                    raise ValueError("Classifier returned an invalid target")
                if response.stop_reason not in {"end_turn", "stop_sequence"}:
                    raise ValueError("Classifier response did not finish successfully")
                call.respond(
                    LlmResponse.Agg(
                        {
                            "model": judge.model,
                            "outputs": [
                                {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": answer}],
                                    "stop_reason": "end_turn",
                                }
                            ],
                        }
                    )
                )
                continue
            if not isinstance(step, Step.Done) or not step.outcome.selected_model_ids:
                raise RuntimeError("Classifier returned no routing outcome")
            name = step.outcome.selected_model_ids[0]
            if name not in route.targets:
                raise RuntimeError("Switchyard selected a target outside the route")
            target = self.targets[name]
            logger.info(
                "switchyard route=%s target=%s provider=%s model=%s latency_ms=%.3f outcome_id=%s",
                request.model,
                name,
                target.provider,
                target.model,
                (time.perf_counter() - started) * 1000,
                step.outcome.metadata.outcome_id if step.outcome.metadata else None,
            )
            return target.provider, target.model
        raise RuntimeError("Switchyard ended without a model choice")

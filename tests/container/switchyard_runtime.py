"""Offline native smoke test in the final runtime image, without toolchains."""

import asyncio
import json
import shutil
from importlib.metadata import version
from pathlib import Path

from switchyard.libsy import (
    CustomClassifierConfig,
    LlmClassifierConfig,
    LlmResponse,
    Step,
    algorithms,
)


async def main() -> None:
    for executable in (
        "rustc",
        "cargo",
        "rustup",
        "gcc",
        "g++",
        "cc",
        "cmake",
        "git",
        "uv",
        "pytest",
        "ruff",
    ):
        assert shutil.which(executable) is None, f"Unexpected build tool in runtime: {executable}"
    assert not Path("/usr/local/rustup").exists()
    assert not Path("/usr/local/cargo").exists()
    provenance = json.loads(Path("/opt/venv/share/niuu/switchyard-build.json").read_text())
    assert version("nemo-switchyard") == provenance["version"]

    steps = [
        step
        async for step in algorithms.random(["first", "second"], weights=[0, 1]).run_stream(
            {"messages": []}
        )
    ]
    assert len(steps) == 1 and isinstance(steps[0], Step.Done)
    assert steps[0].outcome.selected_model_ids[0] == "second"

    schema = {
        "type": "object",
        "properties": {"target": {"type": "string", "enum": ["first", "second"]}},
        "required": ["target"],
        "additionalProperties": False,
    }
    algorithm = algorithms.llm_classifier(
        LlmClassifierConfig.custom(
            "judge",
            [("first", "first"), ("second", "second")],
            default_target="first",
            config=CustomClassifierConfig("Choose a target", schema, "/target"),
        )
    )
    completed = False
    async for step in algorithm.run_stream({"messages": []}):
        if isinstance(step, Step.CallModel):
            # An explicit test fixture: no network calls during the image build.
            step.call.respond(
                LlmResponse.Agg(
                    {
                        "model": "judge",
                        "outputs": [
                            {
                                "role": "assistant",
                                "content": [{"type": "text", "text": '{"target":"second"}'}],
                                "stop_reason": "end_turn",
                            }
                        ],
                    }
                )
            )
        elif isinstance(step, Step.Done):
            assert step.outcome.selected_model_ids[0] == "second"
            completed = True
    assert completed
    from bifrost.adapters.switchyard_models import SwitchyardModelSelection

    assert SwitchyardModelSelection
    print("Native Switchyard runtime verified:", provenance)


if __name__ == "__main__":
    asyncio.run(main())

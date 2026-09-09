"""Opt-in live classifier transport proof with one real model in two target roles.

This tests judge and answer execution, not selection between distinct models.
Set BIFROST_LIVE_URL and BIFROST_LIVE_MODEL to run against an actual vLLM server.
"""

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("switchyard.libsy")

from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig


@pytest.mark.skipif(
    not os.environ.get("BIFROST_LIVE_URL"), reason="Live vLLM endpoint not requested"
)
@pytest.mark.parametrize("stream", [False, True])
def test_live_classifier_and_answer(stream, caplog):
    model = os.environ["BIFROST_LIVE_MODEL"]
    store = MemoryUsageStore()
    cfg = BifrostConfig(
        providers={
            "live": ProviderConfig(
                base_url=os.environ["BIFROST_LIVE_URL"],
                models=[model],
                chat_template_kwargs={"enable_thinking": False},
            )
        },
        selection={
            "adapter": "bifrost.adapters.switchyard_models.SwitchyardModelSelection",
            "targets": {
                role: {"provider": "live", "model": model} for role in ["fast", "best", "judge"]
            },
            "routes": {
                "auto": {
                    "targets": ["fast", "best"],
                    "judge": "judge",
                    "max_tokens": 128,
                    "prompt": (
                        "Choose fast for simple tasks and best for difficult tasks. "
                        "Return only JSON with the target field. Do not answer the request."
                    ),
                }
            },
        },
    )
    with patch("bifrost.app._build_usage_store", return_value=store), caplog.at_level("INFO"):
        with TestClient(create_app(cfg)) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "stream": stream,
                    "messages": [
                        {"role": "user", "content": "Reply with exactly CLASSIFIED_NEMOTRON_OK"}
                    ],
                    "max_tokens": 64,
                    "temperature": 0,
                },
            )
            assert response.status_code == 200, response.text
            if stream:
                assert "[DONE]" in response.text
                text = "".join(
                    choice.get("delta", {}).get("content", "")
                    for line in response.text.splitlines()
                    if line.startswith("data: ") and line != "data: [DONE]"
                    for choice in json.loads(line[6:]).get("choices", [])
                )
                assert text == "CLASSIFIED_NEMOTRON_OK"
            else:
                assert (
                    response.json()["choices"][0]["message"]["content"] == "CLASSIFIED_NEMOTRON_OK"
                )
    assert len(store._records) == 2
    assert ":classifier:" in store._records[0].request_id
    assert all(r.model == model and r.input_tokens > 0 for r in store._records)
    print(
        "LIVE",
        "stream" if stream else "buffered",
        [(r.model, r.input_tokens, r.output_tokens) for r in store._records],
    )

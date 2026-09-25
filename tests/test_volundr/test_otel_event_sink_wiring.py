"""No-fallbacks coverage for volundr.main's OTel event sink wiring.

event_pipeline.otel.enabled: true with the SDK missing must raise, with the
remedy, per .claude/rules/no-fallbacks.md — never log a warning and run the
event pipeline without it. Extracted as _build_otel_event_sink specifically
so this doesn't need the full create_app()/database machinery to test.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from volundr.config import OtelConfig
from volundr.main import _build_otel_event_sink


def test_raises_when_sdk_is_missing():
    cfg = OtelConfig(enabled=True)
    with patch.dict(sys.modules, {"opentelemetry.sdk.trace": None}):
        with pytest.raises(RuntimeError, match="opentelemetry.*not installed"):
            _build_otel_event_sink(cfg)


def test_builds_a_real_sink_when_the_sdk_is_installed():
    pytest.importorskip("opentelemetry.sdk")
    cfg = OtelConfig(enabled=True, service_name="volundr-test", provider_name="anthropic")

    sink = _build_otel_event_sink(cfg)

    assert sink.sink_name == "otel"
    assert sink.healthy is True

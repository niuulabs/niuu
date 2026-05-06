"""Default executor adapter that builds a standard RavnAgent."""

from __future__ import annotations

import inspect
from typing import Any

from ravn.agent import RavnAgent
from ravn.ports.executor import ExecutionAgentPort, ExecutorPort


class AgentExecutor(ExecutorPort):
    """Build the default tool-driven RavnAgent runtime."""

    def __init__(self, **_: Any) -> None:
        return None

    def build(self, **kwargs: Any) -> ExecutionAgentPort:
        sig = inspect.signature(RavnAgent)
        filtered = {key: value for key, value in kwargs.items() if key in sig.parameters}
        return RavnAgent(**filtered)

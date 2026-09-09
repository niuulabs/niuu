"""Embedded native Switchyard weighted provider selection."""

import logging
import math
import time

from switchyard.libsy import Step, algorithms

from bifrost.ports.selection import SelectionPort

logger = logging.getLogger(__name__)


class SwitchyardSelection(SelectionPort):
    """Use libsy random routing; Bifrost retains the original payload and transport.

    Weights are keyed by provider name. This algorithm is content-independent,
    so no user content crosses the Python/Rust boundary. Algorithms are retained
    per candidate set to preserve their random sequence across requests.
    """

    def __init__(self, weights: dict[str, float] | None = None, seed: int | None = None):
        self._weights = weights or {}
        if any(not math.isfinite(w) or w < 0 for w in self._weights.values()):
            raise ValueError("Switchyard weights must be finite and non-negative")
        self._seed = seed
        self._algorithms: dict = {}

    async def select(self, candidates: list[tuple[str, str]]) -> tuple[str, str]:
        key = tuple(candidates)
        if not key:
            raise ValueError("Switchyard requires at least one candidate")
        if key not in self._algorithms:
            self._algorithms[key] = algorithms.random(
                [str(i) for i in range(len(key))],
                weights=[self._weights.get(provider, 1.0) for provider, _ in key],
                seed=self._seed,
            )
        started = time.perf_counter()
        async for step in self._algorithms[key].run_stream({"messages": []}):
            if not isinstance(step, Step.Done):
                raise RuntimeError("Switchyard random routing unexpectedly requested a model call")
            outcome = step.outcome
            if not outcome.selected_model_ids:
                raise RuntimeError("Switchyard returned no selected target")
            # libsy returns the chosen target followed by fallback candidates.
            # Bifrost executes only the chosen target and propagates its failure.
            selected = key[int(outcome.selected_model_ids[0])]
            logger.info(
                "switchyard decision provider=%s model=%s overhead_ms=%.3f outcome_id=%s",
                *selected,
                (time.perf_counter() - started) * 1000,
                outcome.metadata.outcome_id if outcome.metadata else None,
            )
            return selected
        raise RuntimeError("Switchyard ended without a routing decision")

# Resident Signals

Momentum consumes canonical `ResidentInboxSignal` envelopes. Signal storage and
source-specific parsing stay outside `src/ravn/momentum/`.

To add a source:

1. Implement `ResidentSignalSourcePort.load_signal()`.
2. Return a `ResidentInboxSignal` with source, kind, summary, payload,
   classification, status, and evidence refs.
3. Wire the adapter in CLI/main/config composition.
4. Do not change Momentum semantic logic for the source.

The LLM decides meaning. Deterministic code loads signals, verifies provenance,
and persists resident outputs.

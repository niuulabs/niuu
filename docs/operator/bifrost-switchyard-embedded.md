# Embedded Switchyard routing

Bifrost can select a provider using the native Switchyard `libsy` engine in
the Python process. There is no Switchyard HTTP server or loopback proxy hop.
The optional dependency is pinned to upstream commit
`578e1b7cbd9db9577c4a9f14b0218ebc543b2049`; PyPI 0.2.0 has a different Python API.

## Install and run

Install Rust through rustup, with Cargo on PATH. Switchyard's pinned source
selects its Rust toolchain. The first installation compiles a native wheel.

```sh
uv sync --frozen --extra dev --extra switchyard
uv run --no-sync bifrost --config scripts/setups/configs/bifrost-switchyard-embedded.yaml
```

The supplied configuration binds to `127.0.0.1:4010`, uses the real Valaskjalf
vLLM endpoint, and maps `local` to `nvidia/nemotron-3-super`. It uses open auth
for local operation; use the normal Bifrost authentication configuration when
exposing the gateway beyond loopback. The provider base URL excludes `/v1`
because Bifrost's existing adapter appends it.

## Configuration and scope

```yaml
selection:
  adapter: bifrost.adapters.switchyard.SwitchyardSelection
  weights:
    valaskjalf: 1
  seed: 42
```

The adapter runs weighted-random selection over the providers configured for
the resolved model, after Bifrost's rules and alias resolution. It replaces
the built-in `routing_strategy` and per-model strategy when enabled. Weights
use provider names, default to 1, and must be finite and non-negative; each
candidate set must contain positive weight. Seed is optional. Retained native
algorithms preserve their random sequence across requests.

This first integration selects providers for an already resolved model. It
does not yet select between different model IDs or run content classifiers,
stage routing, or judge calls. Random routing requires no request content, so
the native engine receives an empty normalized message list; Bifrost sends
the original full request to the selected provider. Tools, images, reasoning,
streaming, credentials, quotas, and accounting retain their existing paths.

Switchyard returns an ordered list of candidates. Bifrost executes only the
first and propagates its failure; it does not try another provider. Omitting
`selection` retains existing Bifrost behavior. A missing configured adapter
fails application construction. Invalid native weights fail selection.

Every decision logs `provider`, actual `model`, `overhead_ms`, and Switchyard's
`outcome_id`; prompts and credentials are not logged by this adapter.

## Live proof — 2026-09-09

With the gateway running, send a buffered request:

```sh
curl --fail-with-body http://127.0.0.1:4010/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"Reply with exactly SWITCHYARD_EMBEDDED_OK"}],"max_tokens":64,"temperature":0}'
```

Observed response: model `nvidia/nemotron-3-super`, content
`SWITCHYARD_EMBEDDED_OK`, 29 prompt tokens and 11 completion tokens.

```sh
curl --fail-with-body -N http://127.0.0.1:4010/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","stream":true,"messages":[{"role":"user","content":"Reply with exactly SWITCHYARD_STREAM_OK"}],"max_tokens":64,"temperature":0}'
```

Observed streamed content: `SW`, `ITCHYARD`, `_STREAM_OK`, followed by a stop
event and `[DONE]`; 26 prompt tokens and 8 completion tokens. Existing Bifrost
streaming responses report the requested alias `local`; the selection log
reports the actual model. This was a local gateway test against real vLLM,
not a cluster deployment or a test of model-quality routing.

A local routing-only measurement (100 warmups, 1,000 samples, one provider,
logging disabled) measured median 0.500 microseconds for Bifrost direct routing
and 45.458 microseconds for embedded Switchyard; p95 was 0.542 and 57.084
microseconds respectively. Native routing adds about 0.045 ms here. These
numbers exclude HTTP, model inference, and request translation; they do not
establish an end-to-end speedup.

A separate live router-to-vLLM comparison alternated direct and embedded
requests with one warmup and three measured requests each. Median latency was
408.473 ms direct and 408.521 ms embedded. Both returned `OK`. This sample is
too small to establish a performance advantage; inference and network variance
dominated the selection cost.

## Verification

```sh
uv run --no-sync pytest tests/test_bifrost -q --cov=bifrost --cov-fail-under=85
uv run --no-sync ruff check src/bifrost tests/test_bifrost/test_switchyard.py
```

The native tests require the `switchyard` extra and otherwise skip. They run
the real compiled algorithm with test-only provider doubles to verify weighted
choice, payload preservation, streaming, failure propagation, and seeded
sequences. The public HTTP composition is also tested.

Validation on the pinned build: 1,058 Bifrost tests passed, 93.30% coverage;
lint, formatting, and lockfile checks passed without test warnings.

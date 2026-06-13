# Model Routing

Understand how local and cloud models are exposed to the platform.

Bifröst is the model control plane. It tracks models, providers, aliases, routing behavior, usage, cache health, and provider availability.

## Why route through Bifröst

- Give platform services a consistent model catalog.
- Mix local and cloud providers.
- Centralize provider health and usage telemetry.
- Use aliases for workflow-friendly names such as fast, balanced, or best.

## Provider responsibility

Cloud model usage is still governed by the provider account, API key, subscription, or local runtime you configure. Niuu does not make cloud usage free or invisible.

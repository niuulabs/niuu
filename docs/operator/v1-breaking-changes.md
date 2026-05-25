## v1 Breaking Changes

This document marks the `dev -> main` release train as an explicit `v1` cut with
breaking API and contract changes for operators and integrators.

### Breaking areas

- Service naming and vocabulary have shifted across the platform, including
  `raid -> run` and `tyr -> ting` in user-facing and API-adjacent contracts.
- Observatory topology payloads and rendering contracts have changed to support
  guild discovery, richer containment, run/flock internals, and shared service
  rune rendering.
- Guild-backed discovery and control-plane routing now expose different
  registry-backed service shapes and health aliases than the previous mainline.
- Session archive, tracing, telemetry, and self-hosted runtime flows now rely
  on updated storage/bootstrap behavior and revised service integration points.

### Release intent

Consumers should treat this release as a major upgrade and validate any
automation, dashboards, API clients, and deployment glue that depend on the
previous pre-`v1` contracts.

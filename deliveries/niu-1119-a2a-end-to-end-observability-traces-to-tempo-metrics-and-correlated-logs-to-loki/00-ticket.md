# NIU-1119 — A2A end-to-end observability

- **Tracker:** [NIU-1119](https://linear.app/niuu/issue/NIU-1119/a2a-end-to-end-observability-traces-to-tempo-metrics-and-correlated)
- **Scope:** Instrument the Valkyrie/Ravn tool-build path and Ting A2A JSON-RPC surface so one OTel trace can follow a learned-tool commission through A2A launch and flock session handoff.
- **Acceptance criteria:** Propagate trace context across A2A, link the flock session trace through launch provenance/workload config, expose Ravn and Ting OTel metrics, correlate stdout logs with trace/span ids, and keep observability disabled by default.

## Implementation pass

Resolved the Linear task through the repository tracker shim and confirmed it is `In Progress` before starting code changes.

## Revision pass

Resolved the review-blocking duplicate public metric emission by keeping `ravn_tool_build_*` ownership in the Ravn `BuildTool` lifecycle wrapper and removing the same public metric emissions from the A2A backend.

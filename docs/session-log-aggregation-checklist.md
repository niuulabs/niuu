# Session Log Aggregation Checklist

## Goal

Provide a single live session logs experience in Volundr that:

- Interleaves Skuld, Ravn, and service logs in timestamp order
- Lets the operator focus the stream by participant
- Supports downloading the currently visible log view
- Preserves the existing broker-only endpoint for backwards compatibility

## Backend

- [x] Add a workspace log aggregator that normalizes `.skuld.log`, `.flock/logs/*.log`, and `.services/logs/*.log`
- [x] Expose `GET /api/logs/aggregate` from Skuld
- [x] Expose `GET /sessions/{session_id}/logs/aggregate` from Volundr as a proxy endpoint
- [x] Support level, participant, and text filtering in the aggregated endpoint
- [x] Add backend tests for parsing, filtering, and endpoint proxying

## Frontend

- [x] Extend the Volundr service contract for aggregated log snapshots and live polling
- [x] Normalize aggregated participants and rows in the HTTP adapter
- [x] Update the live session Logs tab to render a participant filter bar above the table header
- [x] Interleave all participant rows in one stream while preserving source and level columns
- [x] Add a download action for the currently visible log view
- [x] Add frontend tests for participant rendering and filtering

## Verification

- [x] Run targeted Python tests for the aggregated endpoint and proxy
- [x] Run targeted Vitest coverage for the Volundr adapter and Logs tab
- [x] Build the `plugin-volundr` package successfully
- [x] Verify end to end in the running `web-next` UI against the live dev stack
- [x] Fix any issues found during browser verification and re-run checks

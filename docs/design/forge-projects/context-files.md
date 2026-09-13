# Repository-owned project context

A project can select the ordered files included in its bounded launch snapshot:

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "name": "Example",
  "context_files": ["PROJECT.md", "AGENTS.md", "context/CURRENT.md"]
}
```

This augments the existing `project.json` identity manifest. Forge does not interpret role instructions, select a manager workflow, or require a coordinator-specific skill. The repository owns those policies. `AGENTS.md` can carry a compact shared working agreement and reference detailed skills; a Claude `CLAUDE.md` import can point to the same canonical instructions without duplicating their text.

## Contract

- No `context_files` key: preserve the optional `PROJECT.md` and `context/CURRENT.md` default.
- Explicit key: one to sixteen unique, safe, checkout-relative paths, in the supplied order. Every declared file is required; a missing instruction fails registration preview/context/launch rather than disappearing silently.
- Reject absolute, traversal, backslash, control-character, duplicate and Git-metadata paths. Resolved symlinks must remain inside the allowed checkout; Git metadata cannot be included through an alias.
- Manifest and UTF-8 files are bounded by the configured context byte budget. Headings and separators count toward the combined snapshot (8 KiB default). An invalid manifest, encoding or oversized snapshot fails visibly.
- Revision remains Git HEAD plus SHA-256 of the exact composed text, so dirty instruction edits change context identity. Existing dispatched sessions retain their persisted snapshot across restart; changing the repository does not retroactively replace live instructions.
- The existing session contribution assembly appends the snapshot to resolved system instructions, preserving the chosen persona. No model-name or role-name branch selects workflow behavior.

Repository instructions remain instructions, not permission grants or guarantees of compliance. Harness adapters must preserve the outgoing context. Tests cover Codex `baseInstructions`, Claude tmux appended instructions and Grok's existing `systemPrompt` forwarding; the Grok test does **not** establish acceptance of that extension by a real provider. No model call is needed for these assembly tests.

## Review/rollout

Context support is additive infrastructure, not a workflow engine or a new progress-state API. Deploy and verify a host's actual context response before relying on newly selected files. Older Forge hosts ignore the new manifest key and still send only the legacy pair; repositories must keep critical safety constraints available in those files until their hosts are upgraded. Existing snapshots require explicit normal follow-up context when behavior must change; no implicit resend or restart.

Implementation tests: `tests/test_projects/test_workspace.py`, `test_projects.py`, and `tests/test_skuld/test_project_instruction_transport.py`. This branch does not deploy services or change production registration/configuration.

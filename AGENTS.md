# Volundr / Niuu Platform

Codex agents working in this repository must follow the binding conventions in
`CLAUDE.md` and `.claude/rules/*.md` before changing code.

Key reminders:

- Keep the hexagonal boundaries intact: domain/services depend on ports, adapters
  implement ports, composition happens in package `main.py` files.
- Preserve the Ravn/Niuu boundary in `.claude/rules/ravn-niuu-boundary.md`:
  Ravn wraps models/runtimes and owns judgment, learning, evolution, and A2A.
  A resident is an autonomous Ravn steward attached to an environment; ordinary
  Ravns need not be autonomous. Shared collaboration and direct-mesh mechanics
  live in Niuu libraries used by both Ravn and Skuld. Skuld manages runtime
  sessions and adapts collaboration to human-facing channels. Flokk mesh,
  collaboration rooms, and A2A are distinct mechanisms.
- Do not introduce placeholders, fake implementations, partial adapters, mock
  credentials, or demo-only paths outside tests. If a real implementation cannot be
  completed, report the blocker instead of making the code pretend.
- Do not add product/runtime validation solely to enforce agent-process rules unless
  the user explicitly asks for that behavior.
- Use existing dynamic adapter/config patterns before adding new wiring.
- Tests or live proof must match the risk of the change.

# Volundr / Niuu Platform

Codex agents working in this repository must follow the binding conventions in
`CLAUDE.md` and `.claude/rules/*.md` before changing code.

Key reminders:

- Keep the hexagonal boundaries intact: domain/services depend on ports, adapters
  implement ports, composition happens in package `main.py` files.
- Do not introduce placeholders, fake implementations, partial adapters, mock
  credentials, or demo-only paths outside tests. If a real implementation cannot be
  completed, report the blocker instead of making the code pretend.
- Do not add product/runtime validation solely to enforce agent-process rules unless
  the user explicitly asks for that behavior.
- Use existing dynamic adapter/config patterns before adding new wiring.
- Tests or live proof must match the risk of the change.

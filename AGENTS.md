# Niuu Codex Instructions

Before changing code, read:

- `CLAUDE.md`
- `.claude/rules/architecture.md`
- `.claude/rules/module-boundaries.md`
- `.claude/rules/dynamic-adapters.md`
- `.claude/rules/testing.md`
- `.claude/rules/integrity.md`
- any `.claude/rules/*.md` relevant to the files you touch

These rules are binding for Codex too.

Do not add new architecture layers unless the task explicitly requires them.
Prefer reshaping existing code over wrapping it in new services.
Use existing ports/adapters and selected backend abstractions.
Do not hardwire Mimir, GBrain, Claude, Codex, or any single implementation unless the ticket explicitly says so.
Do not implement fake autonomy, schedulers, daemons, or workflow engines when asked for a proof.
Do not present mocks, fakes, hardcoded outputs, scripted helpers, or constrained schemas as proof of real functionality.
Run the relevant tests and report exactly what was run.

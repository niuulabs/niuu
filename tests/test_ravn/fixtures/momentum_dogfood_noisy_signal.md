# Thread Dump: Momentum Thing, Maybe

okay so this started as "make the eval prove the thing", but that wording is bad.
Not a product demo. Not a benchmark. Please do not turn this into a scoreboard.

I first said "Claude should bless the packet" and immediately regretted it.
Correction: the local model should run the same Momentum procedure we already use,
then we inspect whether the judgment and reflection are actually useful.

Constraint pile:
- no daemon
- no scheduler
- no hidden loop that starts doing work
- no hardwired Claude or Codex names inside Momentum
- it must use the configured LLM port
- it must leave a report artifact so future-us can see what happened

Tangent: the phrase "resident learning" still feels too grand. Maybe just say
the reflection records what was learned from the disposition. Also, if the model
suggests a reflex, that is only a candidate. Do not promote it. Do not register
tools. Do not mutate doctrine.

Actionable tension:
fake LLM tests prove the plumbing, but they do not prove the actual cognitive
procedure survives a messy resident signal. We need one opt-in dogfood command
that runs once, persists the judgment, optionally records an accepted/wrong/etc
disposition, asks for a reflection, and writes a report with the refs and checks.

Rejected wording:
"Momentum eval platform" is wrong. "Manual dogfood proof" is closer.

Remember: source grounding matters more than pretty titles. If provenance is not
verified, the run should fail the dogfood proof.

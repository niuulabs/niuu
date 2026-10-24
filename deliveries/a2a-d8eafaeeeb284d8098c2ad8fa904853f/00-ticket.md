# NIU-1147 — Tool & Skill Builder blocked publisher outcome

- Tracker: [NIU-1147](https://linear.app/niuu/issue/NIU-1147/tool-and-skill-builder-capability-publish-rejects-legitimate)
- Status at implementation start: In Progress
- Target branch: `regin`

## Scope

Route the capability publisher's legitimate `blocked` verdict (`capability.blocked`) to an explicit input-required blocked gate. Keep the successful `capability.ready` route unchanged. Do not alter workload identity, authorization, artifact format, or other workflow graphs.

## Acceptance evidence

The execution regression loads the production Tool & Skill Builder graph and drives the real `capability-publisher` persona outcome path through Ravn, Skuld, and Ting:

- The publisher's blocked outcome retains `Publication is blocked by missing Mímir workload identity exchange configuration.` through the `capability.blocked` routing event and the Skuld `capability-blocked` help-needed gate.
- Ting projects that gate into a blocked campaign whose A2A task state is `TASK_STATE_INPUT_REQUIRED`, without an artifact and without an `outcome_rejected` failure.
- A successful `published` outcome still routes through `capability.ready` and reaches the existing `capability-complete` terminal node.

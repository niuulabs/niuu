# Reviewed live Forge corpus

Captured on 2026-09-07 and 2026-09-08 from real Claude/tmux and Codex sessions.
These fixtures contain synthetic task data and reviewed literal path redactions.
Each expectations file pins the exported hash, source provenance, assertions,
expected turns, checkpoint cursors, and reviewer notes.

Serve with `make forge-trace-lab`. Discover cases at
`GET /api/v1/forge/replay/catalog`. Connect to
`ws://127.0.0.1:8767/api/v1/forge/replay/fixtures/<case>` for playback.

The September 7 source runs retain a deployment-level failure: the running platform had not
yet loaded the public replay-facade fix. A valid scenario fixture does not hide
that failure or certify iOS rendering. The September 8 free-text question case
comes from a passing run after the Lexi Astra capture/recovery fix.

The September 8 interleaving fix adds ordered final text parts to six Codex
expectations. Separate `projection_review` records bind those new projections
to the same captured frames: final text was independently checked against the
actual deltas, and every other turn field/nontext part is unchanged. Original
raw fixture hashes and capture reviews are retained.

The new interleaving captures use synthetic two-command requests with a pause
inside each command. Their reviews pin the observed native text/tool order,
public text hashes, database and replay equality, and capture source hashes.
Earlier collector and ordering failures remain referenced as private provenance;
only the passing interval is promoted. These are replay fixtures, with actual
iOS rendering assessed separately.

Six legacy Claude projections also have separate source-bound reviews removing
seven phantom pane-scrape turns. Each removed turn was an idle terminal redraw
after a completed result, flushed either at EOF or the next human message.
Every real turn and every captured frame remains byte-identical. The new Claude
interleaving case retains its full interval, including the final idle redraw.

| Case | Frames | Expected turns |
|---|---:|---:|
| [claude-tmux / agents](claude-tmux-agents-c8656d2372.frames.json) | 388 | 5 |
| [claude-tmux / background](claude-tmux-background-0dc1b5bccf.frames.json) | 69 | 2 |
| [claude-tmux / interleaving](claude-tmux-interleaving-4187139460.frames.json) | 84 | 2 |
| [claude-tmux / presentation](claude-tmux-presentation-5661c0f978.frames.json) | 31 | 3 |
| [claude-tmux / question](claude-tmux-question-ba5eae7a7f.frames.json) | 74 | 2 |
| [claude-tmux / recovery](claude-tmux-recovery-708373ae98.frames.json) | 70 | 3 |
| [claude-tmux / workspace](claude-tmux-workspace-2544ad8b84.frames.json) | 91 | 3 |
| [codex / agents](codex-agents-da06fa93af.frames.json) | 248 | 2 |
| [codex / background](codex-background-b27deac17e.frames.json) | 56 | 2 |
| [codex / interleaving](codex-interleaving-0e6245cb5f.frames.json) | 129 | 2 |
| [codex / long-output](codex-long-output-27c14b62bb.frames.json) | 70 | 2 |
| [codex / question](codex-question-3c651f5bc0.frames.json) | 63 | 2 |
| [codex / question-freeform](codex-question-freeform-784c2d0311.frames.json) | 77 | 2 |
| [codex / workspace](codex-workspace-f7572f15f7.frames.json) | 72 | 2 |

See the [acceptance plan](../../../docs/testing/forge-live-agentic-acceptance.md)
for the iOS replay matrix, review process, and live commands.

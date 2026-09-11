# Resident HUD

A live view of what a resident is doing, the judgment it reached, what it
*believes* about its environment, and how that belief changes turn by turn.

The resident's working state is overwritten in place, so only the latest
snapshot survives. The history is reconstructed from the append-only turn
records, each of which carries the `working_state` the model authored on that
turn — see `src/ravn/resident_timeline.py`.

Ravn serves the live HUD at `/resident/hud` when
`gateway.channels.http.resident_hud_enabled` is enabled. A generated
`index.html` can also be opened directly (`file://`, no server, no network).
Space plays, arrow keys step, and clicking a marker jumps.

## What it shows

| Column | Meaning |
|---|---|
| Observations | Evidence-grounded, with provenance |
| Hypotheses | Explicitly unproven interpretations |
| Unknowns | Questions it knows could change a judgment |
| Capability gaps | What it cannot yet do |
| Attempts | Research, delegation, tools, operator outreach |

Entries are tagged `NEW` on the turn they appear and struck through as
`DROPPED` on the turn they are abandoned. The strip along the bottom shows the
size of each list across every turn — knowledge accumulating, hypotheses
resolving, capability gaps closing.

## Live progress and persisted judgments

The current-activity strip is ephemeral runtime telemetry for the task that is
executing now: its status, elapsed time, iteration and tool-call counters, and
observable activity events. It is useful for following in-flight work, but it
is not a judgment and is not retained as the resident's durable timeline. For
an active task the judgment field deliberately says `Not emitted yet`.

A judgment becomes authoritative only when the resident completes the turn and
persists its structured outcome. The HUD then reads the decision and judgment
badges from that matching completed turn, and reads the durable working-state
snapshot from the append-only turn record. The renderer does not infer that a
hypothesis was falsified or a capability was acquired merely from live progress
or list changes.

When a completed task is restored, its persisted outcome and working state are
available, but detailed live activity is not retained unless the task has a
trace. `LIVE` means the HUD is receiving fresh polling responses; `STALE`
means those responses have stopped arriving, not that the resident reached a
new judgment.

## Pointing it at any resident

It is not Ivaldi-specific and not workshop-specific. Columns, labels and accent
colours come from the payload, so a resident declaring different working-state
fields still renders. A payload may also carry several residents
(`{"residents": [...]}`), which adds a switcher — the same core against more
than one environment.

From a resident running in Kubernetes:

```bash
python scripts/export_resident_timeline.py \
    --pod <pod> --namespace <ns> --container ravn \
    --resident <name> --out /tmp/<name>
```

Add `--watch 30` to re-export on an interval. Serve the output directory and
the page polls its own `timeline.json`, so new turns appear without a reload
and a `● LIVE` badge shows in the footer:

```bash
python scripts/export_resident_timeline.py --pod <pod> --namespace <ns> \
    --resident <name> --out /tmp/<name> --watch 30 &
cd /tmp/<name> && python3 -m http.server 8777
```

Opened from `file://` there is no polling — the embedded payload stands and the
page is a self-contained snapshot, which is what you want on stage.

## Regenerating from a local state directory

The packaged renderer starts empty and only displays state returned by the
resident. Create a self-contained export with real durable state using:

```bash
python scripts/export_resident_timeline.py \
    --state-root ~/.ravn/state \
    --resident ivaldi \
    --charter "Steward this workshop..." \
    --environment-name "Ivaldi's Workshop" \
    --environment-type workshop \
    --out /tmp/ivaldi-hud
```

`--state-root` is the directory containing `resident/continuation/`. The script
writes `timeline.json` and rewrites `index.html` with the data embedded, so the
result stays self-contained. Live exports set their own provenance note;
`--note` overrides it.

## What it is built with

Plain HTML and vanilla JavaScript in one packaged file — no TypeScript,
framework, build step, or runtime dependency. The live route polls the
same-origin `/resident/hud-data` endpoint; standalone exports poll
`timeline.json`.

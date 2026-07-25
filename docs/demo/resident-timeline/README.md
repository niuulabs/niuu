# Resident working-state visual

A standalone view of what a resident *believes* about its environment, and how
that belief changes turn by turn.

The resident's working state is overwritten in place, so only the latest
snapshot survives. The history is reconstructed from the append-only turn
records, each of which carries the `working_state` the model authored on that
turn — see `src/ravn/resident_timeline.py`.

Open `index.html` directly (`file://`, no server, no network). Space plays,
arrow keys step, clicking a marker jumps.

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

Callouts fire on the transitions worth noticing: a hypothesis falsified, a
capability gap closed, the operator consulted, and a turn taken with no task,
no signal and no human involved.

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

The committed page ships an **illustrative** trajectory — the renderer and the
data shape are real, the content is not from a live run, and the page labels
itself as such. Replace it with a real export:

```bash
python scripts/export_resident_timeline.py \
    --state-root ~/.ravn/state \
    --resident ivaldi \
    --charter "Steward this workshop..." \
    --environment-name "Ivaldi's Workshop" \
    --environment-type workshop \
    --out docs/demo/resident-timeline
```

`--state-root` is the directory containing `resident/continuation/`. The script
writes `timeline.json` and rewrites `index.html` with the data embedded, so the
result stays self-contained. Live exports set their own provenance note, so
they never inherit the template's `ILLUSTRATIVE TRAJECTORY` label; `--note`
overrides it.

## What it is built with

Plain HTML and vanilla JavaScript in one file — no TypeScript, no framework, no
build step, no runtime dependencies, no network requests except the optional
same-origin poll of `timeline.json`.

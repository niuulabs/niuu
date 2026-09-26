# Mímir memory view

`/mimir` is a navigable 3D map of what Niuu knows. Every page is a sphere,
coloured by page type (or by confidence or age), sized by how many links it has,
and grouped by the mount that serves it. The instance management pages
(instances, deployments, routing, health, analytics) stay under the **Registry**
tab.

## What the view does

| Mode | How you get there | What it shows |
|---|---|---|
| Explore | default | the whole graph, "Fly to" per mount, most-connected pages, live activity, Add a source |
| Focus | click a page, or Find a page | the page and its links lit, relation labels on the links, its key facts with proof trends, Read the page |
| Ask | type into the ask bar | the pages that answered, numbered to match the key facts quoted verbatim from them; a hollow `?` when memory has nothing |
| Replay | Replay, or pick a date under "As of" | pages appear on the day they were first seen, with a ring on the day they were born |

Shift-click a second page while one is focused to trace the shortest path
between them. Escape steps back: path, then focus, then question, then replay.
Every mode is in the URL (`focus`, `depth`, `q`, `asOf`, `mount`, `view`,
`colour`), so a view can be linked; a realm links to `/mimir?mount=<its memory mount>`.

## Where the data comes from

| Element | Source |
|---|---|
| Spheres and links | `GET /graph` (typed relationship edges, wikilinks, shared sources) |
| Replay dates | each node's `first_seen`: the earliest of its dated Timeline entries and its last write |
| Answers | `GET /search` (hybrid), which names the mount each result was read from |
| `?` questions | zero-result queries from `GET /eval/queries` |
| Amber rings | pages flagged by lint rule L02 (a `[CONTRADICTION]` marker) |
| Dashed amber links | edges typed `contradicts`, `disagrees_with` or `conflicts_with` |
| "Reading this" / "wrote here" markers | `GET /activity/live` |

## Live activity

`GET /api/v1/mimir/activity/live?since=<ISO-8601>` lists page reads (`GET /page`)
and writes (`PUT /page`, `DELETE /page`, fact revisions, and the MCP server's
`mimir_read`/`mimir_write`), newest first, attributed to the verified caller's
user id (`null` when the request was unauthenticated).

It is presence, not an audit log: events live in memory in each Mímir process
and are never persisted, so a restart clears them and each replica reports only
the requests it served. Configure the window in the Mímir chart:

```yaml
config:
  liveActivity:
    buffer_size: 2000     # most events kept
    window_seconds: 900   # only events this recent are returned
```

A resident wired to an in-process Mímir adapter (no HTTP or MCP hop) is not
recorded.

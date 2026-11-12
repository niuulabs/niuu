# Architecture pages

A new **Architecture** section of the published site (`docs/site/architecture/`)
that explains how the Niuu stack fits together. It will replace most of today's
`concepts/` and parts of `operations/` once it lands.

Integration branch: `docs/architecture-pages` (off `dev`). Each area is written on
its own branch and merged into it.

## Areas

| Area | Folder (owned by one writer) | Covers |
|---|---|---|
| Stack map | `architecture/index.md`, `architecture/stack/` | Every service and library, what talks to what, one request end to end, product vs implementation names |
| Design principles | `architecture/design/` | Hexagonal layout, ports/adapters, composition roots, dynamic adapters, fail loud, no hidden limits, config first, module boundaries |
| Forge | `architecture/forge/` | Session lifecycle, workspaces and projects, pod managers, compute pools, VMs, registered hosts, slots, event log |
| Skuld | `architecture/skuld/` | Runtime gateway: runtimes, process lifecycle, transcripts, channels, WebSocket, broker and Forge MCP |
| Workflows | `architecture/workflows/` | Ting: workflows, runs, steps, gates and reviews, trackers, personas in workflows |
| Security | `architecture/security/` | Edge auth, identity adapters, every credential kind, machine identities, isolation, secrets |
| Deployment modes | `architecture/deployment/` | Local mini mode, Docker, joined nodes and remote compute, Kubernetes; tradeoffs; the levers per mode |
| Communication | `architecture/communication/` | Sleipnir events, collaboration rooms, Flokk mesh, A2A, SSE/WebSocket APIs |
| Models | `architecture/models/` | Bifröst gateway, model catalog, provider connections, routing |
| Memory | `architecture/memory/` | Mímir: sources, pages, retrieval, tenants, hosting |

Ravn is being rebuilt and gets its page once the new runtime lands.

## Page shape

Every page follows this shape. Cut sections that have nothing real to say.

```markdown
# <Title>

<One sentence: what this is and why it exists.>

## In one minute
- 3–5 bullets a newcomer can repeat back.

## How it fits
<One mermaid diagram, at most ~15 nodes. Then 2–4 lines on reading it.>

## <Mechanics — one heading per idea>
Tables and short lists over paragraphs. A paragraph is at most 4 sentences.

## Levers
| Knob | Default | What it changes | Where it is set |

## Tradeoffs
| Choice | Gains | Costs |   (or "Pick X when …")

## Code map
| Concern | Where |   (links to https://github.com/niuulabs/niuu/blob/dev/...)

## Related
```

Limits that keep it readable: a page is at most ~200 lines of Markdown, an area
at most 4 pages (`index.md` plus up to 3). If it does not fit, it is two ideas:
split or cut.

## Writing rules

- **The code is the truth.** Read the code on this branch; existing docs are often
  stale. Every lever, default and flow must exist in the code. Something not built
  gets at most one line under "Not built yet", never a description.
- **Product names first**, implementation name once in parentheses: Forge
  (Völundr), Platform (Guild), Workspaces (Realms). User-facing copy says
  *resident*, not steward.
- No competitor product names.
- Do not edit `mkdocs.yml` or pages outside your folder. The integrator adds the
  nav. Do not touch `docs/design/resident-runtime/`.
- Links out of `docs/site/` use `https://github.com/niuulabs/niuu/blob/dev/...`;
  relative links to other folders fail the strict build.
- Mermaid is enabled (```` ```mermaid ```` fences).
- The build must pass: `uvx --from mkdocs-material==9.7.5 mkdocs build --strict`.
  Locally the one expected warning is `reference/api.md` linking `../openapi.json`
  (gitignored, generated in CI); any other warning is yours to fix.

## Merging

1. Work on `docs/arch-<area>` in your own worktree, branched from
   `docs/architecture-pages`. Conventional commits (`docs(forge): …`).
2. When done: `git fetch niuulabs && git merge niuulabs/docs/architecture-pages`
   (merge, never rebase), rebuild with `--strict`.
3. `git push niuulabs HEAD:docs/arch-<area>` then
   `git push niuulabs HEAD:docs/architecture-pages`. If the second push is
   rejected, fetch, merge again and retry.
4. No pull requests, no pushes to `dev`.
5. In your final report list the existing `docs/site/` pages your area supersedes.

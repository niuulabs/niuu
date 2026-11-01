# Memory and knowledge

Agents need context while working and knowledge they can return to later.
Niuu brings together ephemeral working context, durable resident state,
conversation history, and shared knowledge. These have different lifetimes and
owners. The shared knowledge service is one part of that memory model.

The knowledge service, named Mímir in the UI and configuration, stores material
that can be retrieved across interactions and by multiple agents. The agent
runtime owns the judgment about what an experience means and how to revise
its understanding. Keeping a transcript alone does not turn an interaction into
useful shared learning.

## Context, continuity, and shared memory

| Kind of state | Purpose |
| --- | --- |
| Working context | Information needed for the current reasoning or execution step |
| Conversation and room history | What participants said and the context of their exchange |
| Resident state and cases | Ongoing observations, judgments, attempts, and the work waiting to continue |
| Shared knowledge | Sources and curated understanding available beyond one agent or session |
| Artifacts and capabilities | Outputs and verified tools that can be used in subsequent work |

Persisting state allows continuity. Sharing it requires an explicit scope,
access, and a useful representation. An agent's private context need not become
shared merely because it participates in a team.

## Sources, pages, and mounts

A **source** preserves ingested material. A **page** synthesizes knowledge from
sources. A **mount** identifies a knowledge store available to a client; several
mounts can expose separate stores without merging their ownership.

The knowledge service's filesystem store contains `raw/` sources and `wiki/` pages. The search
index can be rebuilt; the source files and knowledge pages are the durable data.
Back up the store, not only its search database.

## Evidence and current understanding

Pages separate **Compiled Truth**, which can be revised, from a **Timeline** of
evidence. Updating an assessment should preserve the evidence explaining how
that assessment changed. Sources pending synthesis and pages with missing or
inconsistent evidence are different maintenance problems.

Retrieval uses full-text search and can use configured embeddings. Enabling an
embedding backend adds model and runtime requirements; configure those explicitly.
A healthy HTTP endpoint alone says nothing about retrieval quality.

## Giving an agent memory

Make the store available through the agent's configured knowledge adapter or tools.
Verify a known fact can be retrieved before depending on it in a task. Writing a
page in one store does not mean every agent can see it: check mount selection,
access, and write routing.

[Add and retrieve knowledge](../get-started/durable-memory.md) walks through the
observable path. Credentials belong in the credential system, not in sources or
pages that other participants may retrieve.

## Cross-agent learning and capability evolution

One agent's investigation can supply evidence for another. A curated page can
preserve a diagnosis; a verified tool can make the same investigation possible
elsewhere. Recipients still need to assess relevance to their own environment
and have authority to use the result.

The agent runtime owns learning, evidence-backed revision, and capability
adoption. The knowledge service provides durable storage and retrieval; Niuu's communication and execution
services let agents exchange results and commission further work. This connects
memory to the [resident capability cycle](agents-and-personas.md#from-a-capability-gap-to-a-reusable-tool).

The [OS direction](operating-system.md) extends this toward common evaluation
and lineage across the system. Today's shared stores and runtime learning mechanisms are
the foundation for that work.

# Memory and knowledge

Conversation history records an interaction. Mímir stores knowledge that can be
retrieved across interactions. Keeping a transcript does not automatically make
its contents useful shared knowledge.

## Sources, pages, and mounts

A **source** preserves ingested material. A **page** synthesizes knowledge from
sources. A **mount** identifies a knowledge store available to a client; several
mounts can expose separate stores without merging their ownership.

Mímir's filesystem store contains `raw/` sources and `wiki/` pages. The search
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

Make the store available through the agent's configured Mímir adapter or tools.
Verify a known fact can be retrieved before depending on it in a task. Writing a
page in one store does not mean every agent can see it: check mount selection,
access, and write routing.

[Add and retrieve knowledge](../get-started/durable-memory.md) walks through the
observable path. Credentials belong in the credential system, not in sources or
pages that other participants may retrieve.

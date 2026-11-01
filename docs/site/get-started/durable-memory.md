# Ingest and retrieve knowledge

This exercise stores a short source in Mímir, then reads it back. It verifies
persistence of source material without requiring a model to synthesize a page.
Use a local Niuu host with Mímir enabled and a writable store. The commands below
use its default local origin and write to the default mount.

## 1. Inspect the store

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/mimir/mounts
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/mimir/stats
```

Identify the mount you are using before writing. On a shared instance, use the
normal authentication and select the intended mount explicitly; do not use a
shared production knowledge base to try an unfamiliar write.

## 2. Add a small source

```bash
curl --fail --silent --show-error   -H 'Content-Type: application/json'   --data '{"title":"Niuu onboarding note","content":"Our onboarding exercise verifies a file named hello.txt before stopping the session.","source_type":"document"}'   http://127.0.0.1:8080/api/v1/mimir/ingest
```

The response contains a `source_id` and `pages_updated`. Preserve the ID. An empty
`pages_updated` list means no pages were produced by this operation; it does not
mean the source disappeared. Source ingestion and model-based synthesis are
separate results.

## 3. Read the source back

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/mimir/sources
```

Find the returned source ID and title. In the Mímir UI, inspect that source and
its compilation state. To inspect existing knowledge pages through search:

```bash
curl --fail --silent --show-error --get   --data-urlencode 'q=onboarding'   http://127.0.0.1:8080/api/v1/mimir/search
```

Search results are knowledge retrieval, not a guarantee that every raw source
has already become a searchable synthesized page. If the result is empty, check
which pages cite the source and which mount is being queried.

## 4. Give the agent access

Configure the agent's Mímir adapter or tools to use this store. Ask it to retrieve
the onboarding note and cite the source. Verify the returned content rather than
accepting a plausible answer from model memory. This final agent step requires a
working model and configured tools and is not covered by the source-ingest check.

A warden can maintain knowledge over time once the store has a defined scope.
See [memory concepts](../concepts/memory-and-knowledge.md) for evidence, page
structure, and what must be backed up.

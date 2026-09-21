# Grok Build models

The built-in `skuldGrok` runtime uses the Grok CLI through ACP over stdio.
The shared Bifrost catalogue offers **Grok 4.7 (Build)** (`grok-4.7`) and
**Grok 4.6 (Build)** (`grok-4.6`), with 4.7 as the runtime default.
Both advertise Low, Medium, High, and Extra High reasoning. Forge keeps its
existing Extra High launch preference; the transport checks the CLI's actual
effort options before applying a selection.

The web custom-runtime model picker consumes this catalogue and the
`skuldGrok` session definition. It requires no separate hard-coded model list.
Explicit model IDs on existing sessions remain unchanged, including 4.6 and
older 4.5 sessions. The existing generic legacy aliases select the new default.

## Host prerequisite

Before deploying the new default on each Forge host, authenticate the Grok CLI
under the session runtime's user, run `grok update`, and confirm `grok models`
lists **both** `grok-4.7` and `grok-4.6`. A valid public API model ID alone does
not establish that the installed Build CLI/account can select it.

On September 21, Thor's unauthenticated CLI 1.0.25 and an isolated copy of the
latest stable 1.0.40 both listed only 4.6 and 4.5. An authenticated 4.7 turn was
therefore not verified. No host CLI, running gateway, or API was replaced as
part of the source update. Server activation follows the
[local API release procedure](local-api-release.md).

The opt-in `tests/test_skuld/test_grok_e2e.py` gate checks both catalogue model
IDs against `grok models` and exercises real ACP turns. Run it only on a prepared,
authenticated test host:

```sh
FORGE_LIVE_CLI=1 uv run --extra dev pytest tests/test_skuld/test_grok_e2e.py -m live_cli
```

Sources: [Grok 4.7 announcement](https://x.ai/news/grok-4-7),
[4.7 model details](https://docs.x.ai/developers/models/grok-4.7),
[4.6 model details](https://docs.x.ai/developers/models/grok-4.6), and
[Grok Build CLI reference](https://docs.x.ai/build/cli/reference).

# Inspect models and provider routing

Use this guide when the model catalog and the runtime's actual provider are hard
to distinguish. You need a running local Niuu host with Bifröst enabled. The reads
below use the default local port; use your deployment's authenticated origin for
a shared host.

## 1. Inspect the configured catalog

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/bifrost/models
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/bifrost/aliases
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/bifrost/providers/health
```

These answer different questions: which models are advertised, which names map
to models, and what the provider health checks report. Empty provider or alias
results can be valid on a fresh setup. They are not proof that inference is ready.

## 2. Configure an actual provider

For the local host, the `bifrost` section uses `BifrostConfig` from
`src/bifrost/config.py`. Provider entries contain `base_url`, `models`, and an
explicit credential source such as `api_key_env` or `api_key_file`; aliases map a
name to a model. Use endpoint and model IDs supplied by your actual provider.
Keep credential values outside the configuration you commit.

For a local Ollama setup, the repository includes
`scripts/setups/configs/bifrost-ollama.bifrost.yaml`. Its listed model names are
configuration choices, not an instruction to assume those models are installed.
Check your local server's catalog and replace the list with the models it serves.
Choose `direct` routing when a request must stay on its configured provider.

## 3. Verify from the consuming runtime

Point the intended client at Bifröst using that client's supported gateway
configuration. Send a small request, then inspect Bifröst usage and the provider
that served it. Verify the requested alias resolves to the intended model.

A normal Claude Code subscription session follows its own authentication path
unless you explicitly configured otherwise. Do not assume that selecting a model
in Forge makes Bifröst an intermediary.

## Diagnose failures

A catalog response followed by a failed inference request usually needs a closer
look at provider credentials, the actual model ID, API compatibility, or network
reachability. Fix that path before saving a reusable launch or putting a workflow
on it. See [model concepts](../concepts/model-routing.md) and
[credentials](../reference/credentials-and-secrets.md).

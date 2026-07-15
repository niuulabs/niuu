# Config First — No Bare Environment Reads

## Rule

Every runtime knob flows through the package's Settings/config model
(pydantic-settings or adapter kwargs). Reading `os.environ` directly for
behavior configuration is **forbidden**.

```python
# ❌ FORBIDDEN: invisible, untestable, undocumented configuration
exchange_url = os.environ.get("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "")

# ✅ GOOD: a Settings field — config file is canonical, env is an alias
class WorkloadIdentityConfig(BaseModel):
    exchange_url: str = Field(default="", description="...")
```

## Why

- A bare `os.environ.get` is a config surface that appears in no schema, no
  values file, no `/settings` endpoint, and no documentation — it can only be
  discovered by reading source.
- It bypasses validation (pydantic types, fail-loudly on malformed values).
- It cannot be set through the charts' config-file rendering, forcing env-var
  hacks back into deployments.

## How

1. Add the field to the package's config model (`src/<pkg>/config.py`) with a
   default and description.
2. Keep the legacy env var working via pydantic-settings env aliases
   (`validation_alias=AliasChoices(...)`) when deployments already set it —
   the config file is canonical, env is an override.
3. Thread the value from the composition root (main/create_app/container)
   into the adapter as a constructor kwarg. Adapters take plain kwargs —
   never read the environment themselves.
4. Render the value in the Helm chart's config file template, not `env:`.

## Allowed exceptions

- Process bootstrap owned by the OS/runtime: `HOME`, `PATH`, `KUBECONFIG`,
  locale — things that are not application behavior knobs.
- Selecting WHICH config file to load (`RAVN_CONFIG`, `SKULD_CONFIG`).
- Entrypoint shell scripts and test fixtures.

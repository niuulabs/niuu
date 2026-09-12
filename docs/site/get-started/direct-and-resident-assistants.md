# Run Ravn directly and understand residents

Ravn can run independently of Forge. A direct conversation is the smallest way
to use it; a resident adds a purpose, environment, persistent state, and autonomous
behavior. You need the `ravn` executable and a working model-provider configuration.

## Prepare a direct configuration

From a source checkout, inspect the supplied setup before generating it into a
new directory:

```bash
scripts/setups/ravn-setup describe minimal
NIUU_RAVN_SETUP_DIR=$(mktemp -d)
scripts/setups/ravn-setup generate minimal "$NIUU_RAVN_SETUP_DIR"
```

The generated config is a template. Its model name, provider URL, and credential
environment-variable name are intentionally unset. Fill them with values for a
real provider before starting. The `minimal` profile uses an OpenAI-compatible
adapter; a Claude Code subscription login is not an API key for that adapter.

After editing the generated `config.yaml`, run:

```bash
ravn run --config "$NIUU_RAVN_SETUP_DIR/config.yaml" 'Reply with exactly OK.'
```

A successful response verifies that Ravn's configured model path works. An
authentication or model error should be fixed here before enabling daemon
behavior. Run without the prompt to enter an interactive conversation.

## Move to a daemon deliberately

Inspect the daemon profile first:

```bash
scripts/setups/ravn-setup describe daemon-http
```

It adds a local HTTP channel, task processing, memory, and persistent queue state.
Generate it into a separate directory and review all enabled behavior before
launching. Do not overwrite a working direct config just to experiment.

The daemon command uses a completed config:

```bash
ravn daemon --help
```

Use its `--config` option to select the file you prepared. A daemon is not a
resident merely because it keeps running. A resident also needs its environment,
observations, identity, state storage, authority, and intended stewardship.

## Operate a resident

In a platform deployment, select a target and runtime profile that actually
advertise the controls you need. Verify chat, logs, waiting-for-input behavior,
and persistence across a restart. Check the case that is waiting before sending
an answer; continuation belongs to that case.

The [Ravn CLI reference](../reference/cli-ravn.md) covers rooms, flocks, personas,
and wardens. [Architecture](../concepts/platform-model.md) explains the boundary
between Ravn judgment and shared Niuu infrastructure. Live resident deployment is
separate from the validated local Forge bootstrap.

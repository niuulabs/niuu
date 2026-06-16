# One Local Platform

Start with one local platform before thinking about teams, memory, or
Kubernetes.

This step proves that the `niuu` binary can run the platform, serve the web UI,
and expose the local APIs used by the rest of the walkthrough.

![Niuu home screen](../images/ui-niuu-home.png)

## What you are building

At this stage, Niuu is just one operator-controlled stack on your machine:

- one `niuu` process
- one local platform configuration under `~/.niuu`
- one web UI
- one local database/runtime
- embedded services for the default local profile

You do not download separate binaries for every platform service.

## Before you start

Install `niuu` first:

```bash
niuu --version
niuu --help
```

If `niuu` is not on your `PATH`, go back to [Install](install.md).

## Initialize config

Run:

```bash
niuu platform init
```

This creates the local platform configuration. Keep the defaults for the first
run unless you already know which host, database, or runtime settings you need.

Check that the config exists:

```bash
ls ~/.niuu
```

## Start the platform

Run:

```bash
niuu platform up
```

When startup completes, the command prints the UI and API URLs. Open the UI URL
in your browser.

## Verify health

Use the printed host. For a default local stack, this is usually:

```bash
curl http://127.0.0.1:8080/health
```

Then check service status:

```bash
niuu platform status
```

## What you should see

The home screen should load without asking you to configure every platform
service. From here, you should be able to reach the workspace/session area,
settings, and the other platform sections.

If the browser cannot connect, use the exact host printed by `niuu platform up`.
Some local setups bind to a LAN address instead of `127.0.0.1`.

## Stop the platform

```bash
niuu platform down
```

Use `platform down` when you want a clean stop. Closing the terminal is not the
same as asking the platform to shut itself down.

## Contributor shortcut

If you are developing Niuu itself from a repo checkout, use:

```bash
./start-dev
./stop-dev
```

That path rebuilds local assets and uses the source tree. Operators should
start with the release binary path above.

## Next

Create your first live workspace:

[One workspace session](one-workspace-session.md)

# First Local Stack

Start the platform on your machine.

This is the smallest useful Niuu setup: one local platform, one web UI, and the
services needed to create and inspect workspace sessions.

## Start

```bash
niuu platform up
```

When the stack is ready, the command prints the web UI and API URLs.

## Stop

```bash
niuu platform down
```

## Check status

```bash
niuu platform status
```

## What starts

The local stack is intentionally bundled. You do not download separate
executables for every service.

At this stage, think of it as:

- the Niuu web UI
- the platform API
- a local database/runtime
- workspace/session services
- embedded service APIs needed by the local profile

Later pages introduce the service names behind those capabilities.

## Source development shortcut

If you are working from a repo checkout, `./start-dev` starts the development
version of the same local stack and rebuilds local assets as needed.

```bash
./start-dev
./stop-dev
```

Operators should use `niuu platform up`. Contributors can use `./start-dev`.

## Verify

Open the printed UI URL and check the health endpoint:

```bash
curl http://127.0.0.1:8080/health
```

If the stack binds to a LAN address, use the host printed by the command.

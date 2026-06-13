# First Local Stack

Start Niuu on your machine.

Use the local stack when you want the web UI, platform APIs, embedded services, and development database in one operator-controlled environment.

## Start

```bash
./start-dev
```

When the stack is ready, the script prints the web UI and API URLs. The default web UI runs on port `8080` with the `web-next` interface.

## Stop

```bash
./stop-dev
```

## What starts

- The Niuu platform host
- Local PostgreSQL under `~/.niuu/pgdata`
- The web-next UI
- Embedded or locally mounted service APIs for the development profile

## Verify

Open the printed UI URL and check the health endpoint:

```bash
curl http://127.0.0.1:8080/health
```

If the stack binds to a LAN address, use the host printed by `start-dev`.

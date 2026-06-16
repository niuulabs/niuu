# Local Development

Use the local stack for day-to-day platform development.

## Start and stop

```bash
./start-dev
./stop-dev
```

## Logs

The startup script prints the platform log path. By default, logs are written under:

```text
build/dev-run/logs/
```

## Web UI

The local stack builds and serves the web-next UI. Use the URL printed by `start-dev`, including the `config.live.json` query parameter.

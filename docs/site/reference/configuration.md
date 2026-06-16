# Configuration Reference

Understand how Niuu services are configured.

Services load YAML configuration with environment variable overrides. Nested environment overrides use `__`.

## Common config files

| Service | Config file |
| --- | --- |
| Völundr | `config.yaml` or `/etc/volundr/config.yaml` |
| Bifröst | `bifrost.yaml` |
| Ting | `ting.yaml` |
| Ravn | `ravn.yaml` |

## Environment overrides

```bash
DATABASE__HOST=postgres.local
DATABASE__PASSWORD=secret
GIT__GITHUB__TOKEN=ghp_xxxx
EVENT_PIPELINE__OTEL__ENABLED=true
```

## Local stack

`./start-dev` sets the local host profile and aligns service URLs so embedded services can call back into the shared platform host.

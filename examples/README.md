# Example configuration

Reference configs for each service. Copy one to where that service looks for
its configuration and edit it; nothing reads the files in this folder directly.

| File | Service | Copy to |
|---|---|---|
| [`volundr.yaml`](volundr.yaml) | Volundr (Forge backend) | `config.yaml` |
| [`ting.yaml`](ting.yaml) | Ting (dispatcher); all agent prompts live here | `ting.yaml` |
| [`ravn.yaml`](ravn.yaml) | Ravn (agent runtime) | `~/.ravn/config.yaml` |
| [`ravn-tui.yaml`](ravn-tui.yaml) | Ravn TUI keybindings | `~/.ravn/tui.yaml` |
| [`bifrost.yaml`](bifrost.yaml) | Bifrost (model gateway) | `bifrost.yaml` |
| [`bifrost-pi.yaml`](bifrost-pi.yaml) | Bifrost on a Raspberry Pi; run with `bifrost --config examples/bifrost-pi.yaml` | — |
| [`mcp.json`](mcp.json) | Claude Code MCP servers for working in this repo | `.mcp.json` |

`tests/test_infrastructure/test_example_configs.py` fails if an example names a
key its settings model does not define. The models ignore unknown keys at
runtime, so without that test a stale example reads as valid.

"""``python -m skuld.forge_mcp --broker-url ... --token-file ...``: the Forge MCP stdio server."""

from skuld.forge_mcp.stdio import main

if __name__ == "__main__":
    raise SystemExit(main())

"""Bifröst gateway entry point.

Usage:
    bifrost [--config CONFIG_FILE] [--host HOST] [--port PORT]

The gateway reads configuration from a YAML file (default: bifrost.yaml)
and starts an Anthropic-compatible HTTP server.
"""

from __future__ import annotations

import argparse
import logging

import uvicorn
import yaml

from bifrost.app import create_app
from bifrost.config import BifrostConfig

logger = logging.getLogger(__name__)


def _load_config(path: str) -> BifrostConfig:
    """Load BifrostConfig from a YAML file."""
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        return BifrostConfig.model_validate(data.get("bifrost", data))
    except FileNotFoundError:
        logger.warning("Config file %s not found; using defaults.", path)
        return BifrostConfig()


def main() -> None:
    """CLI entry point for the Bifröst gateway."""
    parser = argparse.ArgumentParser(
        prog="bifrost",
        description="Bifröst multi-provider LLM gateway",
    )
    parser.add_argument(
        "--config",
        default="bifrost.yaml",
        help="Path to the YAML configuration file (default: bifrost.yaml)",
    )
    parser.add_argument("--host", default=None, help="Override host from config")
    parser.add_argument("--port", default=None, type=int, help="Override port from config")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = _load_config(args.config)
    if args.host:
        config = config.model_copy(update={"host": args.host})
    if args.port:
        config = config.model_copy(update={"port": args.port})

    app = create_app(config)
    # SIGTERM (a rollout) stops accepting new connections and lets streamed
    # answers already in flight finish, up to the configured grace; without the
    # timeout uvicorn waits indefinitely and the kubelet's SIGKILL cuts them.
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=args.log_level,
        timeout_graceful_shutdown=config.shutdown_grace_seconds,
    )


if __name__ == "__main__":
    main()

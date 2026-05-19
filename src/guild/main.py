"""Standalone Guild service entrypoint."""

from __future__ import annotations

from niuu.service_runtime import run_service_app


def main() -> None:
    """Run the Guild API server."""
    run_service_app("guild.app:app", default_port=8084)


if __name__ == "__main__":  # pragma: no cover
    main()

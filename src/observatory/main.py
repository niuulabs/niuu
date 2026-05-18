"""Standalone Observatory service entrypoint."""

from __future__ import annotations

from niuu.service_runtime import run_service_app
from observatory.plugin import ObservatoryPlugin

app = ObservatoryPlugin().create_api_app()


def main() -> None:
    """Run the Observatory API server."""
    run_service_app("observatory.main:app", default_port=8083)


if __name__ == "__main__":  # pragma: no cover
    main()

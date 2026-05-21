"""Standalone features service entrypoint."""

from __future__ import annotations

from features.app import create_app
from niuu.service_runtime import run_service_app

app = create_app()


def main() -> None:
    run_service_app("features.main:app", 8084)


if __name__ == "__main__":
    main()

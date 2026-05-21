"""Standalone integrations service entrypoint."""

from __future__ import annotations

from integrations.app import create_app
from niuu.service_runtime import run_service_app

app = create_app()


def main() -> None:
    run_service_app("integrations.main:app", 8086)


if __name__ == "__main__":
    main()

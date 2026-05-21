"""Standalone audit service entrypoint."""

from __future__ import annotations

from audit.app import create_app
from niuu.service_runtime import run_service_app

app = create_app()


def main() -> None:
    run_service_app("audit.main:app", 8082)


if __name__ == "__main__":
    main()

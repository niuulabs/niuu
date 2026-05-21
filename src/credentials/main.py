"""Standalone credentials service entrypoint."""

from __future__ import annotations

from credentials.app import create_app
from niuu.service_runtime import run_service_app

app = create_app()


def main() -> None:
    run_service_app("credentials.main:app", 8085)


if __name__ == "__main__":
    main()

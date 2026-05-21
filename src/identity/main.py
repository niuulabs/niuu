"""Standalone identity service entrypoint."""

from __future__ import annotations

from identity.app import create_app
from niuu.service_runtime import run_service_app

app = create_app()


def main() -> None:
    run_service_app("identity.main:app", 8083)


if __name__ == "__main__":
    main()

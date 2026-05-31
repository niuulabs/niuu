"""Standalone tracker service entrypoint."""

from __future__ import annotations

from niuu.service_runtime import run_service_app
from tracker.app import create_app

app = create_app()


def main() -> None:
    run_service_app("tracker.main:app", 8087)


if __name__ == "__main__":
    main()

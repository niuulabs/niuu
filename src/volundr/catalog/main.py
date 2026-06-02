"""CLI entrypoint for the standalone Volundr catalog app."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Run the standalone catalog service."""
    import os

    uvicorn.run(
        "volundr.catalog.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8086")),
        workers=int(os.environ.get("WORKERS", "1")),
        access_log=False,
    )


if __name__ == "__main__":
    main()

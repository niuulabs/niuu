from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])

    config_path = os.environ.get("RAVN_CONFIG", "/etc/ravn/config.yaml")
    persona = os.environ.get("RAVN_PERSONA", "mimir-warden")
    profile = os.environ.get("RAVN_PROFILE", "")

    args = ["ravn", "daemon"]
    if Path(config_path).is_file():
        args.extend(["--config", config_path])
    if persona:
        args.extend(["--persona", persona])
    if profile:
        args.extend(["--profile", profile])

    os.execvp(args[0], args)


if __name__ == "__main__":
    main()

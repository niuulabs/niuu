"""Run an instance-attached warden with the manual warden configuration pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from ravn.cli.daemon_config import _deep_merge
from ravn.warden.artifacts import (
    local_daemon_program_arguments,
    supervisor_environment,
    write_runtime_config,
)
from ravn.warden.models import WardenSpec


def prepare_runtime(document: dict, *, name: str, data_path: Path) -> tuple[WardenSpec, Path]:
    """Bind the chart's warden profile to its owning instance and render its config."""
    data_path = data_path.resolve()
    state = data_path / ".warden"
    overrides = document.get("config") or {}
    profile = dict(document.get("spec") or {})
    if "model" not in profile and overrides.get("llm", {}).get("model"):
        profile["model"] = overrides["llm"]["model"]
    spec = WardenSpec.model_validate(
        {
            **profile,
            "id": f"{name}-warden",
            "name": f"{name} warden",
            "mimir": {
                **profile.get("mimir", {}),
                "mount_names": [name],
                "read_mount_names": [name],
                "write_mount_names": [name],
                "write_mount": name,
                "instance_configs": {name: {"name": name, "path": str(data_path)}},
            },
        }
    )
    path = write_runtime_config(spec, warden_dir=state, workspace_root=data_path)
    payload = _deep_merge(yaml.safe_load(path.read_text()), overrides)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))
    path.chmod(0o600)
    return spec, path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--data-path", type=Path, required=True)
    args = parser.parse_args()
    spec, path = prepare_runtime(
        yaml.safe_load(args.spec.read_text()), name=args.name, data_path=args.data_path
    )
    command = local_daemon_program_arguments(
        spec, config_path=str(path), python_executable=sys.executable
    )
    os.execvpe(command[0], command, {**os.environ, **supervisor_environment(spec)})


if __name__ == "__main__":
    main()

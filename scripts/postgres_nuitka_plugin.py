"""Preserve PostgreSQL's nested Mach-O layout before Nuitka packs onefile."""

import os
import subprocess
import sys
from pathlib import Path
from typing import override

from nuitka.plugins.PluginBase import NuitkaPluginBase


class PostgresLayoutPlugin(NuitkaPluginBase):
    plugin_name = "niuu-postgres-layout"
    plugin_desc = "Make bundled PostgreSQL dependencies relative to each native file."

    @override
    def onStandaloneDistributionFinished(self, dist_dir):
        if sys.platform != "darwin":
            return
        root = Path(dist_dir)
        postgres = root / "niuu/pginstall"
        binaries = list((postgres / "bin").glob("*"))
        libraries = [p for p in (postgres / "lib").rglob("*") if p.suffix in {".dylib", ".so"}]
        for binary in binaries + libraries:
            dependencies = subprocess.check_output(["otool", "-L", binary], text=True)
            changes = []
            for line in dependencies.splitlines()[1:]:
                dependency = line.strip().split(" (", 1)[0]
                if dependency.startswith("@executable_path/"):
                    target = root / dependency.removeprefix("@executable_path/")
                    relative = os.path.relpath(target, binary.parent)
                    changes.extend(["-change", dependency, f"@loader_path/{relative}"])
            if changes:
                subprocess.run(["install_name_tool", *changes, binary], check=True)
                subprocess.run(["codesign", "--force", "--sign", "-", binary], check=True)

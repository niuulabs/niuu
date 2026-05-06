"""MountedVolumePersonaAdapter — read-only PersonaPort backed by a mounted volume.

Designed for Kubernetes deployments where personas are projected into ravn
sidecar containers via a ConfigMap or PVC mount.  The kubelet syncs
projected ConfigMaps within ~60 s, so this adapter intentionally performs
no caching — every call re-scans the mount path so callers always see fresh
data.

k8s projected ConfigMaps use ``..data`` symlink indirection; ``follow_symlinks``
is therefore ``True`` by default.

Overlay ordering example::

    adapter = MountedVolumePersonaAdapter(
        mount_path="/mnt/personas/builtin",
        overlay_paths=[
            "/mnt/personas/tenant",
            "/mnt/personas/flock",
        ],
    )

Personas in later overlay paths override those in earlier paths (and in
``mount_path``) by name.  This enables layering:
  bundled built-ins → tenant ConfigMap → per-flock override.

Write operations (``save`` / ``delete``) are not supported — the write-side
story belongs to ``KubernetesConfigMapPersonaRegistry`` (NIU-8).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ravn.ports.persona import PersonaPort

if TYPE_CHECKING:
    from ravn.adapters.personas.loader import PersonaConfig

logger = logging.getLogger(__name__)

_NOT_IMPLEMENTED_MSG = (
    "Personas are backed by a mounted volume; "
    "edit via the volundr REST API or edit the source ConfigMap."
)


class MountedVolumePersonaAdapter(PersonaPort):
    """Read-only :class:`~ravn.ports.persona.PersonaPort` backed by a mounted volume.

    Scans *mount_path* (and any *overlay_paths*) for ``*.yaml`` files on
    every method call.  No in-process caching is performed so that kubelet
    ConfigMap updates are visible within a single sync cycle.

    Args:
        mount_path: Primary directory to scan for ``*.yaml`` persona files.
            The adapter returns an empty result set — without raising — when
            this path does not exist yet (bootstrap case before the ConfigMap
            is created).
        overlay_paths: Optional ordered list of additional directories.
            Later entries **override** earlier ones (and *mount_path*) by
            persona name.  Paths that do not exist are silently skipped.
        follow_symlinks: When ``True`` (default) the adapter follows symlinks
            when checking for files and directories.  Required for k8s
            projected ConfigMaps which use ``..data`` symlink indirection.
    """

    def __init__(
        self,
        *,
        mount_path: str,
        overlay_paths: list[str] | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        self._mount_path = Path(mount_path)
        self._overlay_paths: list[Path] = [Path(p) for p in overlay_paths] if overlay_paths else []
        self._follow_symlinks = follow_symlinks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ordered_dirs(self) -> list[Path]:
        """Return directories in overlay order: mount_path first, overlays last."""
        return [self._mount_path, *self._overlay_paths]

    def _is_dir(self, path: Path) -> bool:
        """Return True when *path* is a directory, respecting follow_symlinks."""
        if self._follow_symlinks:
            return path.is_dir()
        return path.is_dir() and not path.is_symlink()

    def _iter_yaml_files(self, directory: Path) -> list[Path]:
        """Return all *.yaml files in *directory*, silently skipping missing dirs."""
        if not self._is_dir(directory):
            return []
        return list(directory.glob("*.yaml"))

    def _scan_all(self) -> dict[str, Path]:
        """Scan all directories in overlay order; later entries win by name.

        Returns a mapping of persona name → source file path.
        """
        result: dict[str, Path] = {}
        for directory in self._ordered_dirs():
            for yaml_file in self._iter_yaml_files(directory):
                result[yaml_file.stem] = yaml_file
        return result

    def _parse_file(self, path: Path) -> PersonaConfig | None:
        """Parse a persona YAML file, logging WARN and returning None on failure."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("MountedVolumePersonaAdapter: cannot read %s: %s", path, exc)
            return None

        # Import here to avoid a circular import at module load time.
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter  # noqa: PLC0415

        persona = FilesystemPersonaAdapter.parse(text)
        if persona is None:
            logger.warning(
                "MountedVolumePersonaAdapter: failed to parse persona in %s — skipping",
                path,
            )
        return persona

    # ------------------------------------------------------------------
    # PersonaPort
    # ------------------------------------------------------------------

    def load(self, name: str) -> PersonaConfig | None:
        """Return the named persona, or ``None`` if not found or unparseable."""
        from ravn.adapters.personas.loader import _apply_outcome_instruction  # noqa: PLC0415

        all_files = self._scan_all()
        path = all_files.get(name)
        if path is None:
            return None
        persona = self._parse_file(path)
        if persona is None:
            return None
        return _apply_outcome_instruction(persona)

    def list_names(self) -> list[str]:
        """Return a sorted list of all resolvable persona names."""
        return sorted(self._scan_all().keys())

    def load_all(self) -> list[PersonaConfig]:
        """Return all parseable personas discovered across all mount paths."""
        from ravn.adapters.personas.loader import _apply_outcome_instruction  # noqa: PLC0415

        result: list[PersonaConfig] = []
        for name, path in self._scan_all().items():
            persona = self._parse_file(path)
            if persona is not None:
                result.append(_apply_outcome_instruction(persona))
        return result

    def source(self, name: str) -> str:
        """Return the file path that provides *name*, or ``''`` if not found."""
        path = self._scan_all().get(name)
        if path is None:
            return ""
        return str(path)

    def is_builtin(self, name: str) -> bool:  # noqa: ARG002
        """Always ``False`` — nothing on a mounted volume is a bundled built-in."""
        return False

    # ------------------------------------------------------------------
    # Write operations — not supported
    # ------------------------------------------------------------------

    def save(self, config: PersonaConfig) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def delete(self, name: str) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

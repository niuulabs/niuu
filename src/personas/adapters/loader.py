"""Filesystem persona source.

Personas are YAML files stored at ``~/.ravn/personas/<name>.yaml``,
``<cwd>/.ravn/personas/<name>.yaml``, in explicitly configured directories, or in the
built-in catalogue shipped with this package (``personas/builtin``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from personas.domain.persona import (
    PersonaConfig,
    apply_outcome_instruction,
    parse_persona,
)
from personas.domain.persona_document import (
    PersonaDocumentError,
    PortablePersonaDefinition,
    portable_persona_from_config,
    validate_persona_identifier,
)
from personas.ports.persona import PersonaRegistryPort

# Built-in personas shipped with this package (src/personas/builtin/*.yaml)
_BUILTIN_PERSONAS_DIR = Path(__file__).parent.parent / "builtin"


class FilesystemPersonaAdapter(PersonaRegistryPort):
    """Loads persona configurations from YAML files on the filesystem.

    Two operating modes depending on whether *persona_dirs* is supplied:

    **Default mode** (``persona_dirs=None``):
      1. Project-local: ``<cwd>/.ravn/personas/<name>.yaml``
      2. User-global: ``~/.ravn/personas/<name>.yaml``
      3. Bundled: ``src/personas/builtin/<name>.yaml`` (shipped with the package)

    **Explicit mode** (``persona_dirs=[...]``):
      1. Each directory in *persona_dirs*, in order (highest priority first)
      2. Bundled directory (if *include_builtin* is ``True``)

      When *persona_dirs* is set, the project-local and user-global paths
      are **not** added automatically.

    Args:
        persona_dirs: Explicit list of directories to search (highest priority
            first).  When ``None``, uses default three-layer discovery:
            ``<cwd>/.ravn/personas/`` → ``~/.ravn/personas/`` → bundled.
        include_builtin: Whether to include the bundled personas directory
            in the search path.
        cwd: Working directory used to resolve ``.ravn/personas/``.
             Defaults to the process working directory at construction time.
    """

    def __init__(
        self,
        persona_dirs: list[str] | None = None,
        *,
        include_builtin: bool = True,
        cwd: Path | None = None,
    ) -> None:
        self._include_builtin = include_builtin
        self._cwd = cwd or Path.cwd()

        if persona_dirs is not None:
            self._persona_dirs: list[Path] | None = [Path(d).expanduser() for d in persona_dirs]
        else:
            self._persona_dirs = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_dirs(self) -> list[Path]:
        """Return ordered directories to search (highest priority first).

        When *persona_dirs* was supplied explicitly it forms the list
        (with the bundled directory appended when *include_builtin* is
        ``True``); otherwise the default three-layer discovery is used:
          1. Project-local: ``<cwd>/.ravn/personas/``
          2. User-global: ``~/.ravn/personas/``
          3. Bundled set: ``src/personas/builtin/`` (when *include_builtin* is ``True``)
        """
        if self._persona_dirs is not None:
            dirs = list(self._persona_dirs)
            if self._include_builtin and _BUILTIN_PERSONAS_DIR not in dirs:
                dirs.append(_BUILTIN_PERSONAS_DIR)
            return dirs
        dirs = [
            self._cwd / ".ravn" / "personas",
            Path.home() / ".ravn" / "personas",
        ]
        if self._include_builtin:
            dirs.append(_BUILTIN_PERSONAS_DIR)
        return dirs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, name: str) -> PersonaConfig | None:
        """Load a persona by name, with outcome instruction injected if applicable.

        Iterates ``_resolve_dirs()`` checking for ``<name>.yaml``.
        Returns ``None`` when the name cannot be resolved.

        If the persona declares a ``produces.schema``, the outcome block
        instruction is automatically appended to its system prompt.
        """
        for directory in self._resolve_dirs():
            file_path = directory / f"{name}.yaml"
            if file_path.is_file():
                persona = self.load_from_file(file_path)
                if persona is not None:
                    return apply_outcome_instruction(persona)

        return None

    def load_portable(
        self,
        persona_id: str,
        revision: str,
    ) -> PortablePersonaDefinition | None:
        """Load exact source-authored content without outcome prompt injection."""
        validate_persona_identifier(persona_id)
        validate_persona_identifier(revision, field="Persona revision")
        document = self.load_current_portable(persona_id)
        if document is None or document.revision != revision:
            return None
        return document

    def load_current_portable(
        self,
        persona_id: str,
    ) -> PortablePersonaDefinition | None:
        """Load current source-authored content with a content-derived revision."""
        validate_persona_identifier(persona_id)
        for directory in self._resolve_dirs():
            file_path = directory / f"{persona_id}.yaml"
            if not file_path.is_file():
                continue
            config = self.load_from_file(file_path)
            if config is None:
                return None
            return portable_persona_from_config(
                config,
                persona_id=persona_id,
            )
        return None

    def load_path(self, path: Path) -> PersonaConfig | None:
        """Load a persona from a path the operator named directly.

        Mirrors :meth:`load` — including outcome instruction injection — for a
        file addressed by path rather than by registry name, so
        ``--persona ./reviewer.yaml`` behaves exactly like ``--persona reviewer``.
        Returns ``None`` when the file is unreadable or malformed.
        """
        persona = self.load_from_file(path)
        if persona is None:
            return None
        return apply_outcome_instruction(persona)

    def load_from_file(self, path: Path) -> PersonaConfig | None:
        """Parse a persona YAML file without injecting outcome instructions.

        Returns ``None`` when the file is unreadable or malformed rather than
        raising, so callers can treat missing personas as a soft error.  An
        unrecognised ``permission_mode`` is not malformed-and-skippable: it
        raises :class:`ValueError` naming the file.

        Note: outcome instruction injection happens in :meth:`load` and
        :meth:`load_path`, not here.
        """
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        try:
            return parse_persona(text)
        except PersonaDocumentError as exc:
            raise PersonaDocumentError(f"{path}: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc

    def list_names(self) -> list[str]:
        """Return a sorted list of all resolvable persona names.

        Union of all directory YAML stems (including bundled when enabled).
        """
        names: set[str] = set()
        for directory in self._resolve_dirs():
            if not directory.is_dir():
                continue
            for p in directory.glob("*.yaml"):
                names.add(p.stem)
        return sorted(names)

    # ------------------------------------------------------------------
    # PersonaRegistryPort — write operations
    # ------------------------------------------------------------------

    def save(self, config: PersonaConfig) -> None:
        """Persist *config* as YAML.

        Saves to the first explicitly configured *persona_dir* when one was
        provided at construction time.  Falls back to ``~/.ravn/personas/``
        (user-global) when the adapter is operating in default mode.
        """
        if self._persona_dirs:
            dest_dir = self._persona_dirs[0]
        else:
            dest_dir = Path.home() / ".ravn" / "personas"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{config.name}.yaml"
        payload: dict[str, Any] = config.to_dict()
        dest.write_text(yaml.dump(payload, allow_unicode=True), encoding="utf-8")

    def delete(self, name: str) -> bool:
        """Remove the user-defined persona file for *name*.

        Returns ``True`` when a file was found and removed.  Returns ``False``
        when *name* is a pure built-in with no user-defined override file.
        Files in the bundled personas directory are never deleted.
        """
        for directory in self._resolve_dirs():
            if directory == _BUILTIN_PERSONAS_DIR:
                continue
            file_path = directory / f"{name}.yaml"
            if file_path.is_file():
                file_path.unlink()
                return True
        return False

    def is_builtin(self, name: str) -> bool:
        """Return ``True`` when *name* is a built-in persona."""
        return (_BUILTIN_PERSONAS_DIR / f"{name}.yaml").is_file()

    def load_all(self) -> list[PersonaConfig]:
        """Return all resolvable personas with outcome instructions injected."""
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona is not None:
                result.append(persona)
        return result

    def source(self, name: str) -> str:
        """Return the file path that provides *name*, or ``'[built-in]'``.

        Returns ``'[built-in]'`` when the persona is resolved from the
        bundled personas directory.  Returns an empty string when the
        persona cannot be resolved at all.
        """
        for directory in self._resolve_dirs():
            file_path = directory / f"{name}.yaml"
            if file_path.is_file():
                if directory == _BUILTIN_PERSONAS_DIR:
                    return "[built-in]"
                return str(file_path)
        return ""

    def list_builtin_names(self) -> list[str]:
        """Return a sorted list of built-in persona names."""
        if not _BUILTIN_PERSONAS_DIR.is_dir():
            return []
        return sorted(p.stem for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"))

    def find_consumers(self, event_type: str) -> list[PersonaConfig]:
        """Return all personas that declare they consume the given event type.

        Used by the pipeline executor to validate pipeline definitions.
        Returns personas with outcome instructions already injected (via :meth:`load`).
        """
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona and event_type in persona.consumes.event_types:
                result.append(persona)
        return result

    def find_producers(self, event_type: str) -> list[PersonaConfig]:
        """Return all personas that produce the given event type.

        Used by the pipeline executor to validate pipeline definitions.
        Returns personas with outcome instructions already injected (via :meth:`load`).
        """
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona and persona.produces.event_type == event_type:
                result.append(persona)
        return result

    def find_contributors(self, target: str) -> list[PersonaConfig]:
        """Return all personas whose ``fan_in.contributes_to`` matches *target*.

        Used by the fan-in buffer to determine how many contributor outcomes
        must be collected before the aggregate is ready.
        """
        result: list[PersonaConfig] = []
        for name in self.list_names():
            persona = self.load(name)
            if persona and persona.fan_in.contributes_to == target:
                result.append(persona)
        return result

"""Filesystem-backed persistence for Ravn wardens."""

from __future__ import annotations

import os
import re
import zlib
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ravn.ports.warden_deployer import WardenDeploymentPort
from ravn.warden.artifacts import confined_path, runtime_config_path, service_label
from ravn.warden.models import WardenSpec
from ravn.warden.observability import latest_warden_dream

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _default_wardens_dir() -> Path:
    root = os.environ.get("RAVN_WARDENS_DIR", "").strip()
    if root:
        return Path(root).expanduser()
    return Path.home() / ".ravn" / "wardens"


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "warden"


class WardenStore:
    """Store and load wardens from ``~/.ravn/wardens/<id>/warden.yaml``."""

    def __init__(
        self,
        root: Path | None = None,
        deployer_factory: Callable[[WardenSpec], WardenDeploymentPort] | None = None,
    ) -> None:
        self._root = root or _default_wardens_dir()
        self._deployer_factory = deployer_factory

    @property
    def root(self) -> Path:
        return self._root

    def list(self) -> list[WardenSpec]:
        """Return all readable wardens sorted by name, then id."""
        if not self._root.is_dir():
            return []

        specs: list[WardenSpec] = []
        for spec_path in sorted(self._root.glob("*/warden.yaml")):
            spec = self.load_from_file(spec_path)
            if spec is not None:
                specs.append(self._enrich(spec))
        return sorted(specs, key=lambda spec: (spec.name.lower(), spec.id))

    def get(self, warden_id: str) -> WardenSpec | None:
        """Return one warden by id, or ``None`` when it does not exist."""
        spec = self.load_from_file(self.spec_path(warden_id))
        return self._enrich(spec) if spec is not None else None

    def save(self, spec: WardenSpec) -> WardenSpec:
        """Persist *spec* and return it."""
        spec = self._enrich(spec)
        spec_dir = self.warden_dir(spec.id)
        spec_dir.mkdir(parents=True, exist_ok=True)
        payload = spec.model_dump(mode="json")
        self.spec_path(spec.id).write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
        return spec

    def delete(self, warden_id: str) -> bool:
        """Delete a persisted warden spec file if present."""
        path = self.spec_path(warden_id)
        if not path.exists():
            return False
        path.unlink()
        with suppress(OSError):
            self.warden_dir(warden_id).rmdir()
        return True

    def create(self, spec: WardenSpec) -> WardenSpec:
        """Persist a new warden, allocating a unique id when needed."""
        allocated = self._enrich(
            spec.model_copy(update={"id": self.allocate_id(spec.id or spec.name)})
        )
        return self.save(allocated)

    def install(self, warden_id: str, workspace_root: Path | None = None) -> WardenSpec | None:
        """Generate local service artifacts and register a warden."""
        spec = self.get(warden_id)
        if spec is None:
            return None

        deployer = self._deployer(spec)
        result = deployer.install(
            spec,
            warden_dir=self.warden_dir(warden_id),
            workspace_root=workspace_root,
        )

        updated = spec.model_copy(
            update={
                "runtime": spec.runtime.model_copy(
                    update={
                        "state": result.runtime_state,
                        "last_started_at": datetime.now(UTC)
                        if result.runtime_state == "active"
                        else spec.runtime.last_started_at,
                    }
                ),
                "supervisor": result.supervisor,
            }
        )
        return self.save(updated)

    def start(self, warden_id: str) -> WardenSpec | None:
        """Start a previously installed warden through its deployment backend."""
        spec = self.get(warden_id)
        if spec is None or not spec.supervisor.installed:
            return None

        deployer = self._deployer(spec)
        result = deployer.start(spec, warden_dir=self.warden_dir(warden_id))
        updated = spec.model_copy(
            update={
                "runtime": spec.runtime.model_copy(
                    update={
                        "state": result.runtime_state,
                        "last_started_at": datetime.now(UTC),
                    }
                ),
                "supervisor": result.supervisor,
            }
        )
        return self.save(updated)

    def stop(self, warden_id: str) -> WardenSpec | None:
        """Stop an installed warden through its deployment backend."""
        spec = self.get(warden_id)
        if spec is None or not spec.supervisor.installed:
            return None

        deployer = self._deployer(spec)
        result = deployer.stop(spec, warden_dir=self.warden_dir(warden_id))
        updated = spec.model_copy(
            update={
                "runtime": spec.runtime.model_copy(
                    update={
                        "state": result.runtime_state,
                    }
                ),
                "supervisor": result.supervisor,
            }
        )
        return self.save(updated)

    def uninstall(self, warden_id: str) -> WardenSpec | None:
        """Remove deployment artifacts while keeping the persisted warden spec."""
        spec = self.get(warden_id)
        if spec is None:
            return None

        deployer = self._deployer(spec)
        result = deployer.uninstall(spec, warden_dir=self.warden_dir(warden_id))
        updated = spec.model_copy(
            update={
                "runtime": spec.runtime.model_copy(
                    update={
                        "state": result.runtime_state,
                    }
                ),
                "supervisor": result.supervisor,
            }
        )
        return self.save(updated)

    def observe(self, warden_id: str) -> WardenSpec | None:
        """Refresh live backend status for one persisted warden."""
        spec = self.get(warden_id)
        if spec is None:
            return None

        deployer = self._deployer(spec)
        result = deployer.observe(spec, warden_dir=self.warden_dir(warden_id))
        updated = spec.model_copy(
            update={
                "runtime": spec.runtime.model_copy(
                    update={
                        "state": _runtime_state_from_observation(
                            result.supervisor.observation.status
                        ),
                    }
                ),
                "supervisor": result.supervisor,
            }
        )
        return self.save(updated)

    def allocate_id(self, preferred: str) -> str:
        """Return a unique slug for *preferred* within the store root."""
        base = _slugify(preferred)
        candidate = base
        counter = 2
        while self.spec_path(candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def warden_dir(self, warden_id: str) -> Path:
        return confined_path(self._root, warden_id)

    def spec_path(self, warden_id: str) -> Path:
        return self.warden_dir(warden_id) / "warden.yaml"

    def runtime_config_path(self, warden_id: str) -> Path:
        return runtime_config_path(self.warden_dir(warden_id))

    @staticmethod
    def load_from_file(path: Path) -> WardenSpec | None:
        """Parse one warden spec file, returning ``None`` on read/parse errors."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        try:
            raw = yaml.safe_load(text)
        except Exception:
            return None

        if not isinstance(raw, dict):
            return None

        try:
            return WardenSpec.model_validate(raw)
        except Exception:
            return None

    def _enrich(self, spec: WardenSpec) -> WardenSpec:
        expertise = spec.operator.expertise or list(spec.mimir.category_scope)
        tools = list(spec.operator.tools)
        if not tools:
            tools = ["ravn", "mimir"]
            if spec.features.source_trigger_enabled:
                tools.append("web")
            if spec.features.dream_cycle_enabled:
                tools.append("dream")

        role = spec.operator.role or self._infer_role(spec.persona)
        bio = spec.operator.bio or self._default_bio(spec)
        rune = spec.operator.rune or "ᚱ"
        console_port = spec.console.port or self._default_console_port(spec.id or spec.name)
        latest_dream = latest_warden_dream(spec)
        pages_touched = (
            latest_dream.pages_updated if latest_dream is not None else spec.runtime.pages_touched
        )

        return spec.model_copy(
            update={
                "console": spec.console.model_copy(update={"port": console_port}),
                "runtime": spec.runtime.model_copy(
                    update={
                        "pages_touched": pages_touched,
                        "last_dream": latest_dream,
                    }
                ),
                "operator": spec.operator.model_copy(
                    update={
                        "rune": rune,
                        "bio": bio,
                        "expertise": expertise,
                        "tools": tools,
                        "role": role,
                    }
                ),
            }
        )

    @staticmethod
    def _infer_role(persona: str) -> str:
        mapping = {
            "mimir-warden": "knowledge",
            "research-and-distill": "index",
            "mimir-curator": "knowledge",
            "draft-a-note": "write",
            "produce-recap": "report",
            "reporter": "report",
            "reviewer": "review",
            "verifier": "verify",
            "qa-agent": "verify",
            "planning-agent": "plan",
            "coordinator": "coord",
            "investigator": "investigate",
            "health-auditor": "observe",
        }
        if persona in mapping:
            return mapping[persona]

        label = persona.lower()
        if "index" in label or "curat" in label or "distill" in label:
            return "index"
        if "draft" in label or "write" in label:
            return "write"
        if "report" in label or "recap" in label:
            return "report"
        if "verify" in label or "qa" in label or "validat" in label:
            return "verify"
        if "plan" in label:
            return "plan"
        if "coord" in label:
            return "coord"
        if "investig" in label or "research" in label:
            return "investigate"
        if "observe" in label or "audit" in label or "health" in label:
            return "observe"
        if "review" in label:
            return "review"
        return "autonomy"

    @staticmethod
    def _default_bio(spec: WardenSpec) -> str:
        if spec.profile:
            return f"{spec.name} runs the {spec.profile} profile."
        mounts = (
            ", ".join(spec.mimir.read_mount_names or spec.mimir.mount_names)
            if (spec.mimir.read_mount_names or spec.mimir.mount_names)
            else "attached mounts"
        )
        return f"{spec.name} runs the {spec.persona} persona across {mounts}."

    @staticmethod
    def _default_console_port(seed: str) -> int:
        normalized = str(seed or "warden").strip().encode("utf-8")
        return 8400 + (zlib.adler32(normalized) % 1000)

    def service_label(self, warden_id: str) -> str:
        return service_label(warden_id)

    def _deployer(self, spec: WardenSpec) -> WardenDeploymentPort:
        if self._deployer_factory is None:
            msg = "WardenStore requires a deployment adapter factory for install/start operations"
            raise RuntimeError(msg)
        return self._deployer_factory(spec)


def _runtime_state_from_observation(status: str) -> str:
    if status == "running":
        return "active"
    if status == "missing":
        return "offline"
    return "idle"

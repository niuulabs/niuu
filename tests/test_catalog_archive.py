"""The archived catalogue stays archived: not shipped, not seeded, and not named by kept code.

``archive/`` holds bundled Ting workflows and built-in personas that are out of every
build and deployment (``archive/README.md``). The shipped code carries the list of what
is archived, because the archive itself is not shipped. These tests keep four things in
agreement and fail loudly when they drift:

* the archive and the shipped lists name the same items, and the archived files are the
  released documents (the version lock);
* no archived file is packaged, copied into an image or rendered by a chart;
* nothing that is kept depends on an archived item;
* a kept component that names one stops at startup instead of resolving to "not found".
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from personas.adapters.loader import FilesystemPersonaAdapter
from personas.archived import (
    ARCHIVED_BUILTIN_PERSONAS,
    ArchivedCatalogError,
    reject_archived_persona,
)
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.services.workflow_migration import workflow_catalog_migration_is_current
from ting.domain.workflow_document import load_workflow_document, workflow_document_revision
from ting.system_workflows import (
    ARCHIVED_SYSTEM_WORKFLOW_IDS,
    RETIRED_SYSTEM_WORKFLOW_IDS,
    load_bundled_workflow,
    load_system_workflows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = REPO_ROOT / "archive"
ARCHIVED_PERSONA_FILES = sorted((ARCHIVE / "personas").glob("*.yaml"))
ARCHIVED_WORKFLOW_FILES = sorted((ARCHIVE / "workflows").glob("*.yaml"))
SHIPPED_PERSONAS = REPO_ROOT / "src" / "personas" / "builtin"
SHIPPED_WORKFLOWS = REPO_ROOT / "src" / "ting" / "workflows"
LOCK_PATH = REPO_ROOT / "tests" / "test_ting" / "data" / "bundled_workflow_versions.lock.json"
MIGRATION = REPO_ROOT / "migrations" / "ting" / "000059_detach_archived_system_workflows.up.sql"

# Code that is allowed to name an archived item: the lists themselves.
LIST_MODULES = {
    REPO_ROOT / "src" / "personas" / "archived.py",
    REPO_ROOT / "src" / "personas" / "archived-personas.txt",
    REPO_ROOT / "src" / "ting" / "system_workflows.py",
}
# Product code that still looks an archived workflow up by name, and answers a missing one with
# a loud error that names the remedy (an HTTP 404 saying it is archived), never a substitute.
# Each is a decision for the owner of that surface: archive it, or restore the workflow.
NAMES_ARCHIVED_ON_PURPOSE = {
    REPO_ROOT / "src" / "ting" / "api" / "research.py": {"Research Campaign", "research-campaign"},
    REPO_ROOT / "src" / "ting" / "api" / "specs.py": {"Specification Stack", "specification-stack"},
    REPO_ROOT / "src" / "ting" / "api" / "sagas.py": {"Saga Planning"},
    REPO_ROOT / "src" / "ting" / "domain" / "services" / "review_list.py": {"Saga Planning"},
}
# Everything that is shipped or deployed. ``web-next`` is left out on purpose: its mock
# data and test fixtures carry display strings, and it has no catalogue of its own.
SHIPPED_TREES = ("src", "charts", "containers", "scripts")


def _archived_workflow_documents() -> dict[Path, object]:
    return {
        path: load_workflow_document(path.read_text(encoding="utf-8"))
        for path in ARCHIVED_WORKFLOW_FILES
    }


def _archived_workflow_slugs() -> set[str]:
    return {path.stem for path in ARCHIVED_WORKFLOW_FILES}


def _archived_workflow_names() -> set[str]:
    return {
        yaml.safe_load(path.read_text(encoding="utf-8"))["name"] for path in ARCHIVED_WORKFLOW_FILES
    }


def _archived_tokens() -> set[str]:
    """Every spelling of an archived item: persona names, workflow ids, slugs and names."""
    # A one-word persona (closer, publisher) is also ordinary English across the code base;
    # kept workflows are checked for those by their pins, which name them exactly.
    return (
        {path.stem for path in ARCHIVED_PERSONA_FILES if "-" in path.stem}
        | {str(document.id) for document in _archived_workflow_documents().values()}
        | _archived_workflow_slugs()
        | _archived_workflow_names()
    )


def _text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(
            part in {"node_modules", "__pycache__"} for part in path.parts
        ):
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


# --- The archive and the shipped lists agree --------------------------------------------


def test_the_archive_is_not_empty_and_has_a_readme() -> None:
    assert ARCHIVED_PERSONA_FILES
    assert ARCHIVED_WORKFLOW_FILES
    assert (ARCHIVE / "README.md").is_file()


def test_the_archived_persona_list_is_the_archive() -> None:
    assert {path.stem for path in ARCHIVED_PERSONA_FILES} == ARCHIVED_BUILTIN_PERSONAS


def test_the_archived_workflow_list_is_the_archive() -> None:
    ids = {document.id for document in _archived_workflow_documents().values()}

    assert ids == ARCHIVED_SYSTEM_WORKFLOW_IDS
    assert ARCHIVED_SYSTEM_WORKFLOW_IDS <= RETIRED_SYSTEM_WORKFLOW_IDS


def test_the_readme_describes_every_archived_item() -> None:
    readme = (ARCHIVE / "README.md").read_text(encoding="utf-8")

    for path in [*ARCHIVED_PERSONA_FILES, *ARCHIVED_WORKFLOW_FILES]:
        assert f"`{path.name}`" in readme, f"archive/README.md does not describe {path.name}"


def test_nothing_archived_is_also_shipped() -> None:
    for path in ARCHIVED_PERSONA_FILES:
        assert not (SHIPPED_PERSONAS / path.name).exists(), path.name
    for path in ARCHIVED_WORKFLOW_FILES:
        assert not (SHIPPED_WORKFLOWS / path.name).exists(), path.name
    shipped = {workflow.id for workflow in load_system_workflows()}
    assert not shipped & ARCHIVED_SYSTEM_WORKFLOW_IDS
    assert not set(FilesystemPersonaAdapter().list_builtin_names()) & ARCHIVED_BUILTIN_PERSONAS


def test_archived_workflows_are_the_released_documents() -> None:
    """Archived files are unchanged: each still has the revision the version lock recorded."""
    locked = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

    for path, document in _archived_workflow_documents().items():
        key = f"{document.id}@{document.version}"
        assert key in locked, f"{path.name} is not in the version lock"
        assert locked[key] == workflow_document_revision(document), (
            f"{path.name} changed while archived; restore it from git history instead"
        )


def test_the_detach_migration_covers_exactly_the_archived_workflows() -> None:
    ids = set(
        re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", MIGRATION.read_text()
        )
    )

    assert {UUID(value) for value in ids} == ARCHIVED_SYSTEM_WORKFLOW_IDS


@pytest.mark.asyncio
async def test_a_catalog_migrated_while_they_shipped_still_starts() -> None:
    """The file-catalog marker lists the archived ids; the repo (rightly) serves none of them.

    Without the archived ids in the retired set the startup guard would find them
    missing and refuse to start Ting on a cluster that was migrated while they shipped.
    """
    served = {workflow.id for workflow in load_system_workflows()}
    marker = {
        "source_count": 3,
        "source_max_updated_at": None,
        "inventory_ids": [
            str(workflow_id) for workflow_id in served | ARCHIVED_SYSTEM_WORKFLOW_IDS
        ],
        "deleted_ids": [],
    }

    class _Catalog:
        def read_migration_marker(self) -> dict:
            return marker

        async def get_workflow(self, workflow_id: UUID) -> object | None:
            return object() if workflow_id in served else None

    kwargs = {"legacy_count": 3, "legacy_max_updated_at": None}
    assert await workflow_catalog_migration_is_current(
        _Catalog(), retired_bundled_ids=RETIRED_SYSTEM_WORKFLOW_IDS, **kwargs
    )
    assert not await workflow_catalog_migration_is_current(
        _Catalog(), retired_bundled_ids=frozenset(), **kwargs
    )


# --- Not shipped ------------------------------------------------------------------------


def _wheel_config() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "hatch"
    ]["build"]["targets"]["wheel"]


def test_the_wheel_does_not_package_the_archive() -> None:
    config = _wheel_config()
    packaged = [REPO_ROOT / entry for entry in config["packages"]]
    packaged += [REPO_ROOT / source for source in config.get("force-include", {})]

    for root in packaged:
        assert not ARCHIVE.is_relative_to(root) and not root.is_relative_to(ARCHIVE), root
        archived_stems = ARCHIVED_BUILTIN_PERSONAS | _archived_workflow_slugs()
        for path in root.rglob("*.yaml") if root.is_dir() else ():
            assert path.stem not in archived_stems, f"{path} is archived but packaged"


def test_the_built_wheel_file_list_has_no_archived_file() -> None:
    wheel = pytest.importorskip("hatchling.builders.wheel")

    entries = [
        file.distribution_path
        for file in wheel.WheelBuilder(str(REPO_ROOT)).recurse_included_files()
    ]
    names = {Path(entry).stem for entry in entries if entry.endswith(".yaml")}

    assert not names & (ARCHIVED_BUILTIN_PERSONAS | _archived_workflow_slugs())
    assert not [entry for entry in entries if entry.startswith("archive")]


def test_the_binary_build_does_not_bundle_the_archive() -> None:
    from cli.build import DATA_DIR_MAPPINGS, DATA_FILE_MAPPINGS

    for source, _destination in [*DATA_DIR_MAPPINGS, *DATA_FILE_MAPPINGS]:
        assert "archive" not in source.relative_to(REPO_ROOT).parts, source


def test_no_image_copies_the_archive() -> None:
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "archive" in [line.strip() for line in ignore], ".dockerignore must list archive"

    for dockerfile in sorted((REPO_ROOT / "containers").rglob("Dockerfile*")):
        for number, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.upper().startswith(("COPY", "ADD")):
                continue
            sources = [part for part in stripped.split()[1:-1] if not part.startswith("--")]
            for source in sources:
                assert not source.startswith("archive"), f"{dockerfile.name}:{number}: {line}"
                assert source not in {".", "./", "*"}, (
                    f"{dockerfile.name}:{number} copies the whole context: {line}"
                )


def test_nothing_shipped_or_deployed_names_an_archived_item() -> None:
    """Charts, images, scripts and source never name an archived persona or workflow."""
    tokens = _archived_tokens()
    named: list[str] = []

    for tree in SHIPPED_TREES:
        for path, text in _text_files(REPO_ROOT / tree):
            if path in LIST_MODULES:
                continue
            allowed = NAMES_ARCHIVED_ON_PURPOSE.get(path, set())
            named += [
                f"{path.relative_to(REPO_ROOT)}: {token}"
                for token in tokens - allowed
                if token in text
            ]

    assert not named, "kept files name archived items:\n" + "\n".join(named)


# --- Kept items do not depend on archived ones ------------------------------------------


@pytest.mark.parametrize("path", sorted(SHIPPED_WORKFLOWS.glob("*.yaml")), ids=lambda p: p.stem)
def test_a_shipped_workflow_does_not_depend_on_an_archived_item(path: Path) -> None:
    document = load_workflow_document(path.read_text(encoding="utf-8"))
    personas = {dependency.id for dependency in document.persona_dependencies.values()}
    children = {dependency.id for dependency in document.workflow_dependencies.values()}

    assert not personas & ARCHIVED_BUILTIN_PERSONAS
    assert not children & ARCHIVED_SYSTEM_WORKFLOW_IDS
    assert document.id not in ARCHIVED_SYSTEM_WORKFLOW_IDS
    text = path.read_text(encoding="utf-8")
    for token in _archived_tokens():
        assert token not in text, f"{path.name} names the archived {token!r}"


@pytest.mark.parametrize("path", sorted(SHIPPED_PERSONAS.glob("*.yaml")), ids=lambda p: p.stem)
def test_a_shipped_persona_does_not_name_an_archived_item(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    for token in _archived_tokens():
        assert token not in text, f"{path.name} names the archived {token!r}"


# --- Loud, not silent -------------------------------------------------------------------


def test_a_shipped_workflow_file_with_an_archived_id_is_refused(tmp_path: Path) -> None:
    bundle = tmp_path / "workflows"
    bundle.mkdir()
    restored = bundle / ARCHIVED_WORKFLOW_FILES[0].name
    shutil.copy(ARCHIVED_WORKFLOW_FILES[0], restored)

    with pytest.raises(WorkflowDocumentError, match="archived"):
        load_system_workflows(bundle)
    with pytest.raises(WorkflowDocumentError, match=r"archive/README\.md"):
        load_bundled_workflow(restored, bundle_root=tmp_path)


def test_a_workflow_pinning_an_archived_persona_is_refused(tmp_path: Path) -> None:
    source = yaml.safe_load((SHIPPED_WORKFLOWS / "code-review-merge.yaml").read_text())
    alias, dependency = next(iter(source["persona_dependencies"].items()))
    dependency["id"] = sorted(ARCHIVED_BUILTIN_PERSONAS)[0]
    bundle = tmp_path / "workflows"
    bundle.mkdir()
    document = bundle / "pinned.yaml"
    document.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowDocumentError, match=rf"{alias!r}.*archived") as raised:
        load_bundled_workflow(document, bundle_root=tmp_path)

    assert "archived-personas.txt" in str(raised.value)


def test_a_workflow_depending_on_an_archived_workflow_is_refused(tmp_path: Path) -> None:
    child = yaml.safe_load((ARCHIVE / "workflows" / "research-thread.yaml").read_text())
    assert UUID(child["id"]) in ARCHIVED_SYSTEM_WORKFLOW_IDS
    source = yaml.safe_load((SHIPPED_WORKFLOWS / "code-review-merge.yaml").read_text())
    revision = workflow_document_revision(load_workflow_document(yaml.safe_dump(child)))
    source["schema_version"] = 2
    source["workflow_dependencies"] = {
        "child": {
            "id": child["id"],
            "revision": revision,
            "digest": revision,
            "path": "workflows/research-thread.yaml",
        }
    }
    bundle = tmp_path / "workflows"
    bundle.mkdir()
    document = bundle / "parent.yaml"
    document.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowDocumentError, match=r"'child'.*archived workflow"):
        load_bundled_workflow(document, bundle_root=tmp_path)


@pytest.mark.parametrize("name", sorted(ARCHIVED_BUILTIN_PERSONAS))
def test_config_naming_an_archived_persona_stops_startup(name: str) -> None:
    from skuld.config import MeshConfig

    with pytest.raises(ValueError, match=rf"mesh\.persona.*{name}.*archived"):
        MeshConfig(persona=name)
    with pytest.raises(ArchivedCatalogError, match=r"archive/personas"):
        reject_archived_persona(name, where="anything")


def test_config_naming_a_shipped_persona_still_loads() -> None:
    from skuld.config import MeshConfig

    assert MeshConfig().persona == "coder"
    assert reject_archived_persona("coder", where="x") == "coder"

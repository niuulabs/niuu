"""Authoring builds feed the real portable importer without hand-maintained pins."""

import io
import zipfile

import pytest
import yaml
from typer.testing import CliRunner

from ting.adapters.workflow_bundle import WorkflowBundleCodec
from ting.adapters.workflow_source_files import FilesystemWorkflowSourceReader
from ting.domain.services.workflow_authoring import compile_workflow
from ting.domain.services.workflow_sharing import plan_workflow_import
from ting.domain.workflow_document import load_workflow_document, workflow_document_revision
from ting.workflow_authoring import create_workflow_commands, starter_sources


class SourceReader:
    def __init__(self, sources):
        self.sources = sources

    def read(self, path):
        return yaml.safe_dump(self.sources[path])


def test_build_is_repeatable_and_source_change_invalidates_pin():
    sources = starter_sources(name="Editorial review", model="test-model")
    first = compile_workflow("workflow.yaml", SourceReader(sources))
    assert first == compile_workflow("workflow.yaml", SourceReader(sources))
    sources["personas/reviewer.yaml"]["system_prompt_template"] += " Check accessibility."
    second = compile_workflow("workflow.yaml", SourceReader(sources))
    assert (
        first.document.persona_dependencies["reviewer"].digest
        != second.document.persona_dependencies["reviewer"].digest
    )
    assert workflow_document_revision(first.document) != workflow_document_revision(second.document)


def test_build_bundle_imports_through_existing_portable_contract():
    sources = starter_sources(name="Editorial review", model="test-model")
    result = compile_workflow("workflow.yaml", SourceReader(sources))
    codec = WorkflowBundleCodec(
        max_upload_bytes=1_000_000, max_expanded_bytes=1_000_000, max_entries=10
    )
    contents = codec.read(codec.write(result.yaml, result.files), filename="review.zip")
    document = load_workflow_document(contents.workflow)
    plan = plan_workflow_import(document, contents.files, source=None, mappings={}, bindings={})
    assert not plan.errors
    assert not plan.requirements
    assert set(plan.persona_definitions) == {"author", "reviewer"}
    assert "repository" not in result.yaml
    packed = codec.write(result.yaml, result.files)
    assert packed == codec.write(result.yaml, dict(reversed(list(result.files.items()))))
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert {entry.date_time for entry in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_nested_workflows_are_pinned_and_packaged():
    sources = starter_sources(name="Parent", model="test-model")
    child = starter_sources(name="Child", model="test-model")
    sources["workflow.yaml"]["workflow_dependencies"] = {"child": {"source": "children/child.yaml"}}
    sources["children/child.yaml"] = child["workflow.yaml"]
    for name in ("author", "reviewer"):
        sources[f"children/personas/{name}.yaml"] = child[f"personas/{name}.yaml"]
    result = compile_workflow("workflow.yaml", SourceReader(sources))
    dependency = result.document.workflow_dependencies["child"]
    assert dependency.path in result.files
    parsed = load_workflow_document(result.files[dependency.path])
    assert workflow_document_revision(parsed) == dependency.digest


def test_cycles_rejected_with_source_location():
    sources = starter_sources(name="Cycle", model="test-model")
    sources["workflow.yaml"]["workflow_dependencies"] = {"self": {"source": "workflow.yaml"}}
    with pytest.raises(ValueError, match="Cyclic workflow sources"):
        compile_workflow("workflow.yaml", SourceReader(sources))


@pytest.mark.parametrize(
    "source", ["../outside.yaml", "/outside.yaml", "a\\b.yaml", "secret.txt", ""]
)
def test_outside_and_non_yaml_sources_rejected(source):
    sources = starter_sources(name="Invalid", model="test-model")
    sources["workflow.yaml"]["persona_dependencies"]["author"] = {"source": source}
    with pytest.raises(ValueError, match="[Ss]ource"):
        compile_workflow("workflow.yaml", SourceReader(sources))


def test_invalid_graph_and_persona_bindings_fail_before_output():
    sources = starter_sources(name="Invalid", model="test-model")
    sources["workflow.yaml"]["graph"]["nodes"][1]["stageMembers"][0]["personaId"] = "unknown"
    with pytest.raises(ValueError, match="undeclared persona"):
        compile_workflow("workflow.yaml", SourceReader(sources))


def test_source_reader_rejects_symlinks_outside_project_and_large_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("secret: true")
    (root / "link.yaml").symlink_to(outside)
    reader = FilesystemWorkflowSourceReader(str(root), max_source_bytes=4)
    with pytest.raises(ValueError, match="escapes"):
        reader.read("link.yaml")
    (root / "large.yaml").write_text("abcdef")
    with pytest.raises(ValueError, match="size limit"):
        reader.read("large.yaml")


def test_cli_init_check_build_and_no_overwrite(tmp_path):
    runner = CliRunner()
    app = create_workflow_commands()
    project = tmp_path / "review"
    assert runner.invoke(app, ["init", str(project), "--model", "test-model"]).exit_code == 0
    original = (project / "workflow.yaml").read_bytes()
    assert runner.invoke(app, ["init", str(project), "--model", "test-model"]).exit_code == 1
    assert (project / "workflow.yaml").read_bytes() == original
    assert runner.invoke(app, ["check", str(project / "workflow.yaml")]).exit_code == 0
    output = tmp_path / "review.zip"
    args = ["build", str(project / "workflow.yaml"), "-o", str(output)]
    assert runner.invoke(app, args).exit_code == 0
    original_bundle = output.read_bytes()
    assert runner.invoke(app, args).exit_code == 1
    assert output.read_bytes() == original_bundle


def test_cli_check_reports_duplicate_yaml_keys(tmp_path):
    source = tmp_path / "workflow.yaml"
    source.write_text("name: one\nname: two\n")
    result = CliRunner().invoke(create_workflow_commands(), ["check", str(source)])
    assert result.exit_code == 1
    assert "Duplicate YAML" in result.output


def test_authoring_rejects_mixed_pins_and_source():
    sources = starter_sources(name="Invalid", model="test-model")
    sources["workflow.yaml"]["persona_dependencies"]["author"]["digest"] = "sha256:bad"
    with pytest.raises(ValueError, match="only source"):
        compile_workflow("workflow.yaml", SourceReader(sources))


def test_depth_limit_and_environment_bound_persona_rejected():
    sources = starter_sources(name="Invalid", model="test-model")
    with pytest.raises(ValueError, match="nesting exceeds"):
        compile_workflow("workflow.yaml", SourceReader(sources), max_depth=0)
    sources["personas/author.yaml"]["executor"] = {"adapter": "local.Executor"}
    with pytest.raises(ValueError, match="environment-specific"):
        compile_workflow("workflow.yaml", SourceReader(sources))


def test_authoring_reuses_export_credential_rejection():
    sources = starter_sources(name="Invalid", model="test-model")
    sources["workflow.yaml"]["graph"]["resource"] = {"apiKey": "inline-credential"}
    with pytest.raises(ValueError, match="inline credential"):
        compile_workflow("workflow.yaml", SourceReader(sources))

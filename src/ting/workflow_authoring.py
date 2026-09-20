"""Offline workflow authoring commands and their composition root."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import typer
import yaml

from ting.adapters.workflow_bundle import WorkflowBundleCodec
from ting.adapters.workflow_source_files import FilesystemWorkflowSourceReader
from ting.domain.services.workflow_authoring import WorkflowBuild, compile_workflow


def _build(source: Path) -> WorkflowBuild:
    try:
        return compile_workflow(source.name, FilesystemWorkflowSourceReader(str(source.parent)))
    except (OSError, ValueError) as exc:
        typer.echo(f"Workflow validation failed: {exc}", err=True)
        raise typer.Exit(1) from exc


def create_workflow_commands() -> typer.Typer:
    app = typer.Typer(help="Create, validate, and package portable workflow sources.")

    @app.command("check")
    def check(source: Path = typer.Argument(exists=True, dir_okay=False)) -> None:
        """Validate a workflow and its local persona/child dependencies offline."""
        build = _build(source)
        typer.echo(f"Valid: {build.document.name} ({len(build.files)} pinned dependencies)")

    @app.command("build")
    def build(
        source: Path = typer.Argument(exists=True, dir_okay=False),
        output: Path = typer.Option(..., "--output", "-o", help="New importable .zip bundle."),
    ) -> None:
        """Pin dependency content and package it for the existing workflow importer."""
        result = _build(source)
        if output.suffix.lower() != ".zip":
            raise typer.BadParameter("Output must end in .zip")
        payload = WorkflowBundleCodec.write(result.yaml, result.files)
        try:
            with output.open("xb") as stream:
                stream.write(payload)
        except OSError as exc:
            typer.echo(f"Cannot write workflow bundle: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Built {output}. Import this bundle from the Workflows page.")

    @app.command("init")
    def initialize(
        directory: Path = typer.Argument(help="New project directory."),
        name: str = typer.Option("Reviewed artifact", help="Workflow display name."),
        model: str = typer.Option(..., help="A model available in your configured gateway."),
    ) -> None:
        """Create a working author/reviewer workflow with a revision loop."""
        if not model.strip() or not name.strip():
            raise typer.BadParameter("Name and model must not be empty")
        sources = starter_sources(name=name, model=model)
        try:
            directory.mkdir(parents=False, exist_ok=False)
            (directory / "personas").mkdir()
            for path, content in sources.items():
                (directory / path).write_text(
                    yaml.safe_dump(content, sort_keys=False), encoding="utf-8"
                )
        except OSError as exc:
            typer.echo(f"Cannot create workflow project: {exc}", err=True)
            raise typer.Exit(1) from exc
        _build(directory / "workflow.yaml")
        typer.echo(
            f"Created {directory / 'workflow.yaml'}. "
            "Edit the persona instructions, then check/build."
        )

    return app


def starter_sources(*, name: str, model: str) -> dict[str, dict]:
    """Instantiate the bundled YAML starter with a new identity and chosen model."""
    from importlib.resources import files

    template = files("ting").joinpath("workflow_templates/review")
    sources = {
        path: yaml.safe_load(template.joinpath(path).read_text(encoding="utf-8"))
        for path in ("workflow.yaml", "personas/author.yaml", "personas/reviewer.yaml")
    }
    workflow = sources["workflow.yaml"]
    workflow["id"] = str(uuid4())
    workflow["name"] = name
    for node in workflow["graph"]["nodes"]:
        for member in node.get("stageMembers", []):
            member["model"] = model
    return sources

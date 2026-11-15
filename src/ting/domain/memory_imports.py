"""Memory imports: a project's tickets brought into its memory as sources.

An import is a record Ting keeps for one project: which team integration of the project it
reads with, which tracker project it reads, and how far it has got. A run reads the tickets
updated after the import's cursor, hands each (with its comments) to the memory gateway as
a source, and moves the cursor only over tickets the gateway accepted. When the import
asks for it, the sources a run created are then compiled into the project's pages by the
bundled memory-compile workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

#: The one connector there is. GitHub pull requests and Slack threads come later.
CONNECTOR_LINEAR = "linear"
SUPPORTED_CONNECTORS = (CONNECTOR_LINEAR,)

#: ``kind`` of a source that is a ticket.
SOURCE_KIND_TICKET = "ticket"

#: Id of the bundled ``memory-compile`` workflow (``src/ting/workflows/memory-compile.yaml``).
MEMORY_COMPILE_WORKFLOW_ID = UUID("7b46fab6-5793-4033-a9bf-5129d630b5ce")

#: The prompt of the compile run launched after an import brought new sources.
COMPILE_PROMPT = "Compile these sources into the project's memory"
#: Key of the run's launch context that lists the new source ids.
COMPILE_CONTEXT_SOURCE_IDS = "source_ids"


class MemoryImportError(RuntimeError):
    """An import could not do what it is configured to do."""


class ImportConnectionError(MemoryImportError):
    """The import's team integration cannot be used; the message says what to fix."""


class SourceGatewayError(MemoryImportError):
    """The memory gateway did not accept a source."""


class CompileLaunchError(MemoryImportError):
    """The compile workflow could not be started for the sources an import brought."""


class MemoryImportExistsError(MemoryImportError):
    """The project already imports this tracker project through this connection."""


@dataclass(frozen=True)
class MemoryImport:
    id: UUID
    project_id: UUID
    tenant_id: str
    #: The person who set it up; the compile runs launch as them.
    owner_id: str
    connector: str
    #: The project's team integration the import reads with.
    connection_id: str
    #: The tracker's own id of what is read (a Linear project id).
    tracker_project_id: str
    compile: bool
    #: Upstream ``updatedAt`` of the newest ticket the gateway accepted; ``None`` before any.
    cursor: datetime | None
    created_at: datetime
    last_run_at: datetime | None = None
    last_error: str = ""
    #: Sources created by earlier runs that the compile workflow has not been started for.
    pending_source_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def layer(self) -> str:
        """The memory layer the import writes to."""
        return f"project:{self.project_id}"


@dataclass(frozen=True)
class IssueComment:
    author: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class UpdatedIssue:
    """A tracker ticket as an import reads it: its text, its comments and when it changed."""

    id: str
    identifier: str
    title: str
    description: str
    status: str
    assignee: str | None
    labels: tuple[str, ...]
    url: str
    created_at: datetime | None
    updated_at: datetime
    comments: tuple[IssueComment, ...] = ()


@dataclass(frozen=True)
class SourceWrite:
    """What is sent to the memory gateway for one ticket."""

    tenant_id: str
    layer: str
    connector: str
    external_id: str
    title: str
    content: str
    kind: str
    origin_url: str
    fetched_at: datetime
    upstream_updated_at: datetime


@dataclass(frozen=True)
class SourceAccepted:
    """The gateway's answer: the source the ticket is, and whether this made a new one."""

    source_id: str
    layer: str
    new: bool


@dataclass(frozen=True)
class ImportRunResult:
    """What one run of an import did."""

    read: int
    new_sources: int
    unchanged: int
    cursor: datetime | None
    #: Id of the compile run started, or ``None`` when none was due.
    compile_run: str | None = None
    new_source_ids: tuple[str, ...] = field(default_factory=tuple)


def issue_source_content(issue: UpdatedIssue) -> str:
    """The ticket's text with its comments, as fetched.

    Deterministic for a given ticket: nothing in it depends on when it was read, so the
    gateway can tell a ticket that did not change (same content) from one that did.
    """
    lines = [f"# {issue.identifier} {issue.title}", ""]
    lines.append(f"Status: {issue.status}")
    if issue.assignee:
        lines.append(f"Assignee: {issue.assignee}")
    if issue.labels:
        lines.append(f"Labels: {', '.join(issue.labels)}")
    if issue.url:
        lines.append(f"URL: {issue.url}")
    lines.extend(["", "## Description", "", issue.description.strip() or "(no description)"])
    lines.extend(["", "## Comments"])
    if not issue.comments:
        lines.extend(["", "(no comments)"])
    for comment in issue.comments:
        lines.extend(
            ["", f"### {comment.author or 'unknown'}, {comment.created_at.isoformat()}", ""]
        )
        lines.append(comment.body.strip())
    return "\n".join(lines) + "\n"

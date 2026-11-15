"""Import a retired Markdown wiki into a memory layer.

The wiki adapter kept, under one root::

    wiki/<path>.md     two-zone pages (src/mimir/FORMAT.md); wiki/index.md and
                       wiki/log.md were derived catalogues, not pages
    raw/<id>.json      immutable sources: source_id, title, content, source_type,
                       origin_url, content_hash, ingested_at

Every page becomes a page of the target layer (its compiled truth, then each
timeline entry appended), and every raw source a source, written at the trust of
the operator who runs the import. Evidence that was free text in the wiki (a
timeline row's ``[Source: ...]``) is kept in the row's detail; the row cites the
file it was imported from, plus the source id when the attribution names one.

A page that cannot be imported stops the import with the file named: nothing is
skipped silently. Files that are not pages (the derived catalogues, hidden files,
anything that is not ``.md``) and tool-output sources, which are not sources,
are listed in the report with the reason. Running the import again after a fix
skips pages already imported unchanged, and stops at a page that differs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from mimir.compiled_truth import parse_page
from mimir.memory.models import (
    Attribution,
    Evidence,
    EvidenceKind,
    Layer,
    LayerKind,
    MemoryStoreError,
    NewSource,
    NewTimelineRow,
    PageWrite,
    RevisionConflictError,
    SourceKind,
)
from mimir.ports.memory_backend import MemoryBackend
from niuu.domain.mimir import OPERATIONAL_SOURCE_TYPES, compute_content_hash, compute_source_id
from niuu.domain.mimir_facts import FactCaller

_DERIVED = frozenset({"index.md", "log.md"})
_FRONTMATTER = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)
_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SOURCE_ID = re.compile(r"\bsrc_[0-9a-f]{16}\b")

#: Source types the old wiki wrote free-form, and the kind each is kept as. A type that is
#: neither a current kind nor listed here stops the import, naming it: add it here.
LEGACY_SOURCE_TYPES: dict[str, SourceKind] = {
    "paper": SourceKind.DOCUMENT,
    "local-doc": SourceKind.DOCUMENT,
    "local": SourceKind.DOCUMENT,
    "markdown": SourceKind.DOCUMENT,
    "csv": SourceKind.DOCUMENT,
    "code": SourceKind.DOCUMENT,
    "workflow-context": SourceKind.DOCUMENT,
    "source": SourceKind.DOCUMENT,
    "documentation": SourceKind.DOCUMENT,
    "note": SourceKind.DOCUMENT,
}
_ZONE = re.compile(r"^## (Compiled Truth|Timeline)\s*$", re.MULTILINE)


class WikiImportError(Exception):
    """A file could not be imported. The message names it and what was imported before."""


@dataclass
class ImportReport:
    pages: int = 0
    pages_already_imported: int = 0
    timeline_rows: int = 0
    sources: int = 0
    sources_already_held: int = 0
    not_imported: list[tuple[str, str]] = field(default_factory=list)
    #: Sources kept under an older id scheme (before 2026-07-25 the id was not the content
    #: hash): old id -> the id the source is stored under now. Pages citing the old id
    #: are rewritten to cite the stored one.
    rekeyed: dict[str, str] = field(default_factory=dict)
    #: Old free-form source types kept as a current kind: type -> how many.
    legacy_types: dict[str, int] = field(default_factory=dict)
    #: Timeline rows repeated word for word on the same day, kept once (gbrain stores
    #: one row per page, date, summary and source): page -> rows collapsed.
    repeated_rows: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"{self.pages} page(s) ({self.pages_already_imported} already imported), "
            f"{self.timeline_rows} timeline row(s), {self.sources} source(s) "
            f"({self.sources_already_held} already held), "
            f"{len(self.not_imported)} file(s) not imported"
            + (
                f", {len(self.rekeyed)} source id(s) from the older id scheme re-keyed"
                if self.rekeyed
                else ""
            )
            + (
                ", older source types kept as documents: "
                + ", ".join(f"{t} ({n})" for t, n in sorted(self.legacy_types.items()))
                if self.legacy_types
                else ""
            )
            + (
                f", {sum(self.repeated_rows.values())} repeated timeline row(s) kept once "
                f"({', '.join(f'{p}: {n}' for p, n in sorted(self.repeated_rows.items()))})"
                if self.repeated_rows
                else ""
            )
        )


def _import_layer(layer: Layer) -> Layer:
    if layer.kind not in (LayerKind.ORG, LayerKind.PROJECT):
        raise WikiImportError(
            f"a wiki imports into the org layer or a project layer, not {layer.kind.value}"
        )
    return layer


def _evidence(path: str) -> Evidence:
    return Evidence(EvidenceKind.IMPORT, path)


def _compiled_truth(content: str) -> str:
    """The page's current state: its Compiled Truth zone, or its body when it has no zones."""
    parsed = parse_page(content).compiled_truth.strip()
    if parsed:
        return parsed
    body = _FRONTMATTER.sub("", content, count=1)
    zone = _ZONE.search(body)
    return (body[: zone.start()] if zone else body).strip()


async def import_wiki(
    backend: MemoryBackend, root: Path, layer: Layer, caller: FactCaller
) -> ImportReport:
    """Import the wiki under *root* into *layer*, attributed to *caller*."""
    target = _import_layer(layer)
    wiki = root / "wiki"
    if not wiki.is_dir():
        raise WikiImportError(
            f"{root} has no wiki/ directory; point --root at the wiki root (the directory "
            "holding wiki/ and raw/)"
        )
    report = ImportReport()
    await backend.create_namespace(target.namespace)
    raw = root / "raw"
    if raw.is_dir():
        for path in sorted(raw.iterdir()):
            await _import_source(backend, root, path, target, caller, report)
    for path in sorted(wiki.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(wiki)
        reason = _not_a_page(relative)
        if reason:
            report.not_imported.append((f"wiki/{relative.as_posix()}", reason))
            continue
        try:
            await _import_page(backend, path, relative, target, caller, report)
        except (MemoryStoreError, ValueError, OSError) as exc:
            raise WikiImportError(
                f"wiki/{relative.as_posix()} could not be imported: {exc}. Imported before "
                f"it: {report.summary()}"
            ) from exc
    return report


def _not_a_page(relative: Path) -> str:
    if any(part.startswith(".") for part in relative.parts):
        return "hidden file"
    if relative.suffix != ".md":
        return "not a Markdown page"
    if relative.parent == Path(".") and relative.name in _DERIVED:
        return "derived catalogue of the old wiki"
    return ""


async def _import_source(
    backend: MemoryBackend,
    root: Path,
    path: Path,
    layer: Layer,
    caller: FactCaller,
    report: ImportReport,
) -> None:
    name = f"raw/{path.name}"
    if path.name.startswith(".") or path.suffix != ".json":
        report.not_imported.append((name, "not a raw source"))
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        content = str(data["content"])
        source_id = str(data["source_id"])
        kind = str(data.get("source_type") or "document")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WikiImportError(f"{name} is not a raw source record: {exc}") from exc
    # A source is intact when its id is its content's hash (the current scheme) or, for a
    # source kept under the older id scheme, when its recorded content_hash still matches.
    current_id = compute_source_id(content) == source_id
    recorded_hash = str(data.get("content_hash") or "")
    if recorded_hash and compute_content_hash(content) != recorded_hash:
        raise WikiImportError(
            f"{name} failed its integrity check: its content does not match its content_hash"
        )
    if not current_id and not recorded_hash:
        raise WikiImportError(
            f"{name} failed its integrity check: its content does not match its id {source_id} "
            "and it records no content_hash to check it by"
        )
    if kind in OPERATIONAL_SOURCE_TYPES:
        report.not_imported.append((name, f"{kind} is not a source; it is not carried over"))
        return
    if kind in LEGACY_SOURCE_TYPES:
        source_kind = LEGACY_SOURCE_TYPES[kind]
        report.legacy_types[kind] = report.legacy_types.get(kind, 0) + 1
    else:
        try:
            source_kind = SourceKind(kind)
        except ValueError:
            raise WikiImportError(
                f"{name} has an unknown source_type {kind!r}; add it to "
                "mimir.memory.importer.LEGACY_SOURCE_TYPES with the kind it should be kept as"
            ) from None
    origin = data.get("origin_url") or None
    cited = [_evidence(name)]
    if origin and str(origin).startswith(("http://", "https://")):
        cited.insert(0, Evidence(EvidenceKind.URL, str(origin)))
    try:
        record, new = await backend.put_source(
            layer.namespace,
            NewSource(
                title=str(data.get("title") or source_id),
                content=content,
                kind=source_kind,
                origin_url=origin,
            ),
            Attribution.by(caller, tuple(cited), datetime.now(UTC)),
        )
    except MemoryStoreError as exc:
        raise WikiImportError(
            f"{name} could not be imported: {exc}. Imported before it: {report.summary()}"
        ) from exc
    if record.id != source_id:
        if current_id:
            raise WikiImportError(f"{name} was stored as {record.id}, not {source_id}")
        report.rekeyed[source_id] = record.id
    if new:
        report.sources += 1
    else:
        report.sources_already_held += 1


async def _import_page(
    backend: MemoryBackend,
    path: Path,
    relative: Path,
    layer: Layer,
    caller: FactCaller,
    report: ImportReport,
) -> None:
    name = f"wiki/{relative.as_posix()}"
    slug = relative.with_suffix("").as_posix().lower()
    content = path.read_text(encoding="utf-8")
    parsed = parse_page(content)
    truth = _compiled_truth(content)
    if not truth:
        raise WikiImportError(f"{name} has no content to import")
    heading = _HEADING.search(_FRONTMATTER.sub("", content, count=1))
    title = str(parsed.frontmatter.get("title") or (heading.group(1) if heading else slug))
    cited = (
        *(
            Evidence(EvidenceKind.SOURCE, report.rekeyed.get(sid, sid))
            for sid in parsed.source_ids
            if _SOURCE_ID.match(sid)
        ),
        _evidence(name),
    )
    rows = []
    for entry in parsed.timeline_entries:
        if not entry.date:
            raise WikiImportError(
                f"{name} has a timeline entry without a date ({entry.raw!r}); give it one "
                "(- YYYY-MM-DD: ...) and run the import again"
            )
        row_evidence = [
            Evidence(EvidenceKind.SOURCE, report.rekeyed.get(s, s))
            for s in _SOURCE_ID.findall(entry.source)
        ]
        rows.append(
            NewTimelineRow(
                on=date.fromisoformat(entry.date),
                summary=entry.description.strip() or entry.raw,
                evidence=(*row_evidence, _evidence(name)),
                detail=f"Source: {entry.source}" if entry.source else "",
            )
        )
    unique = list(dict.fromkeys(rows))
    if len(unique) != len(rows):
        report.repeated_rows[name] = len(rows) - len(unique)
        rows = unique
    now = datetime.now(UTC)
    page = PageWrite(
        compiled_truth=truth,
        title=title,
        page_type=parsed.page_type.value if parsed.page_type else "",
        evidence=cited,
    )
    try:
        await backend.put_page(
            layer.namespace, slug, page, Attribution.by(caller, cited, now), expected_revision=None
        )
    except RevisionConflictError:
        existing = await backend.get_page(layer.namespace, slug)
        if " ".join(existing.compiled_truth.split()) != " ".join(truth.split()):
            raise WikiImportError(
                f"{name}: page {slug} already exists in {layer.id} with different content; "
                "remove it there or import into another layer"
            ) from None
        # Rows gbrain derived from dated lines in the page text carry no seal; only the
        # rows this import wrote count.
        written = [r for r in existing.timeline if r.attribution.attested]
        if len(written) != len(rows):
            raise WikiImportError(
                f"{name}: page {slug} was imported with {len(written)} of "
                f"{len(rows)} timeline rows; remove the page from {layer.id} and import again"
            ) from None
        report.pages_already_imported += 1
        return
    for row in rows:
        await backend.append_timeline(
            layer.namespace, slug, row, Attribution.by(caller, row.evidence, now)
        )
        report.timeline_rows += 1
    report.pages += 1

"""CompositeMimirAdapter — fan-out across multiple MimirPort instances.

Reads from all mounted Mímirs in priority order (lower ``read_priority`` first)
and merges results.  Routes writes based on category-prefix config or explicit
agent override — exactly parallel to ``CompositeMeshAdapter``.

Example (two mounts — local filesystem + shared HTTP service)::

    from mimir.adapters.markdown import MarkdownMimirAdapter
    from ravn.adapters.mimir.http import HttpMimirAdapter
    from ravn.adapters.mimir.composite import CompositeMimirAdapter
    from ravn.domain.mimir import MimirAuth, MimirMount, WriteRouting

    local = MarkdownMimirAdapter(root="~/.ravn/mimir")
    shared = HttpMimirAdapter(
        base_url="https://mimir.odin.niuu.world",
        auth=MimirAuth(type="spiffe", trust_domain="niuu.world"),
    )

    routing = WriteRouting(
        rules=[
            ("self/", ["local"]),
            ("technical/", ["local", "shared"]),
            ("household/", ["shared"]),
        ],
        default=["local"],
    )

    adapter = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local, role="local", read_priority=0),
            MimirMount(name="shared", port=shared, role="shared", read_priority=1),
        ],
        write_routing=routing,
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from niuu.domain.mimir import (
    LintIssue,
    MimirLintReport,
    MimirMountSummary,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    ThreadOwnershipError,
    ThreadState,
)
from niuu.ports.mimir import MimirPort
from ravn.domain.exceptions import MimirUnavailableError
from ravn.domain.mimir import MimirMount, WriteRouting

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _mount_failed(operation: str, mount_name: str, exc: Exception) -> MimirUnavailableError:
    """The error every fan-out raises when one mount cannot do its part.

    A composite that skips a failing mount answers from a corpus that is not
    the configured one, and says nothing about it: reads come back thin, writes
    report success having landed nowhere. That is indistinguishable from a
    small corpus or an idle resident, which is why it survives for months. See
    ``.claude/rules/no-fallbacks.md``.
    """
    return MimirUnavailableError(
        f"Mímir mount {mount_name!r} failed during {operation}: {exc}. "
        f"Fix or remove that mount — results from the remaining mounts would "
        f"be silently incomplete."
    )


def _unknown_mount(operation: str, name: str, known: list[str]) -> MimirUnavailableError:
    """Write routing named a mount that is not mounted."""
    return MimirUnavailableError(
        f"write routing for {operation} names unknown Mímir mount {name!r}; "
        f"mounted: {known or '(none)'}. Fix the routing rules or mount it — "
        f"writes to a mount that does not exist go nowhere."
    )


class CompositeMimirAdapter(MimirPort):
    """Fan-out across multiple MimirPort instances with configurable routing.

    Read operations merge results from all mounts in ``read_priority`` order
    (de-duplicated by page path).  Write operations are routed by category
    prefix or explicit ``mimir=`` override.

    **A mount that fails fails the call.** Every fan-out here used to catch,
    log and carry on, which meant a configured mount could be unreachable for
    months while reads quietly returned a fraction of the corpus and writes
    reported success having landed nowhere. The only failure a mount is allowed
    to answer with is ``FileNotFoundError`` on a single-page read — that is the
    protocol saying "not here, ask the next mount", not a mount that could not
    answer. See ``.claude/rules/no-fallbacks.md``.

    Args:
        mounts:        Ordered list of Mímir mounts.
        write_routing: Category-prefix write routing configuration.
    """

    def __init__(
        self,
        mounts: list[MimirMount],
        write_routing: WriteRouting | None = None,
        *,
        read_retry_max_seconds: float = 0.0,
        read_retry_initial_backoff_seconds: float = 1.0,
        read_retry_max_backoff_seconds: float = 10.0,
    ) -> None:
        self._mounts = sorted(mounts, key=lambda m: m.read_priority)
        self._mount_map = {m.name: m for m in mounts}
        self._write_routing = write_routing or WriteRouting()
        # Retrying a read no mount could answer defaults off: callers that treat
        # an unanswerable read as fatal (provenance verification) opt in through
        # config, so nothing else silently gains a multi-second stall.
        self._read_retry_max_seconds = read_retry_max_seconds
        self._read_retry_initial_backoff_seconds = read_retry_initial_backoff_seconds
        self._read_retry_max_backoff_seconds = read_retry_max_backoff_seconds

    def _mount_names(self) -> list[str]:
        return sorted(self._mount_map)

    def ingest_targets(self, explicit: str | None = None) -> list[str]:
        """Return the mount names an ingest should target.

        Explicit routing wins. Otherwise we prefer the configured default write
        targets and fall back to all mounted instances to preserve the legacy
        fan-out behavior.
        """
        if explicit:
            return [explicit] if explicit in self._mount_map else []

        configured_default = [
            mount_name
            for mount_name in self._write_routing.default
            if mount_name in self._mount_map
        ]
        if configured_default:
            return configured_default
        return [mount.name for mount in self._mounts]

    # ------------------------------------------------------------------
    # MimirPort — read operations (fan-out, merge, de-dup by path)
    # ------------------------------------------------------------------

    async def ingest(self, source: MimirSource) -> list[str]:
        """Ingest into all mounts, merge returned page paths."""
        all_paths: list[str] = []
        for mount in self._mounts:
            try:
                paths = await mount.port.ingest(source)
            except Exception as exc:
                raise _mount_failed("ingest", mount.name, exc) from exc
            all_paths.extend(paths)
        return list(dict.fromkeys(all_paths))

    async def ingest_to(self, source: MimirSource, mount_name: str) -> list[str]:
        """Ingest into a specific mount only.

        Used by workflow-scoped memory producers that need deterministic routing
        to one mounted Mimir instance.
        """
        mount = self._mount_map.get(mount_name)
        if mount is None:
            raise ValueError(f"Unknown Mimir mount: {mount_name}")
        return await mount.port.ingest(source)

    async def query(self, question: str) -> MimirQueryResult:
        """Query all mounts in priority order, merge sources (de-dup by path)."""
        seen_paths: set[str] = set()
        merged_sources: list[MimirPage] = []

        for mount in self._mounts:
            try:
                result = await mount.port.query(question)
            except Exception as exc:
                raise _mount_failed("query", mount.name, exc) from exc
            for page in result.sources:
                if page.meta.path not in seen_paths:
                    seen_paths.add(page.meta.path)
                    merged_sources.append(page)

        # answer stays empty by design: Mímir's synthesis is precomputed into
        # the Compiled Truth zone by a Ravn, not composed at query time.
        return MimirQueryResult(question=question, answer="", sources=merged_sources)

    async def search(self, query: str) -> list[MimirPage]:
        """Search all mounts in priority order, de-dup by path."""
        seen_paths: set[str] = set()
        results: list[MimirPage] = []

        for mount in self._mounts:
            try:
                pages = await mount.port.search(query)
            except Exception as exc:
                raise _mount_failed("search", mount.name, exc) from exc
            for page in pages:
                if page.meta.path not in seen_paths:
                    seen_paths.add(page.meta.path)
                    results.append(page)

        return results

    async def get_page(self, path: str) -> MimirPage:
        """Read full page from the first mount (in priority order) that has it.

        ``FileNotFoundError`` from a mount means "not here, ask the next one" —
        a protocol-level answer, not a failure. Anything else means the mount
        could not answer, and a mount that could not answer might have been the
        one holding the page, so it must not be reported as "not found".
        """
        for mount in self._mounts:
            try:
                return await mount.port.get_page(path)
            except FileNotFoundError:
                continue
            except Exception as exc:
                raise _mount_failed("get_page", mount.name, exc) from exc
        raise FileNotFoundError(f"Mímir page not found in any mount: {path}")

    async def read_page(self, path: str) -> str:
        """Read from the first mount (in priority order) that has the page.

        Shares ``get_page``'s absence semantics: only a mount that answered can
        rule the page out.
        """
        for mount in self._mounts:
            try:
                return await mount.port.read_page(path)
            except FileNotFoundError:
                continue
            except Exception as exc:
                raise _mount_failed("read_page", mount.name, exc) from exc
        raise FileNotFoundError(f"Mímir page not found in any mount: {path}")

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        """List pages from all mounts in priority order, de-dup by path."""
        seen_paths: set[str] = set()
        results: list[MimirPageMeta] = []

        for mount in self._mounts:
            try:
                pages = await mount.port.list_pages(category=category, prefix=prefix)
            except Exception as exc:
                raise _mount_failed("list_pages", mount.name, exc) from exc
            for meta in pages:
                if meta.path not in seen_paths:
                    seen_paths.add(meta.path)
                    results.append(meta)

        return results

    async def summarize(self) -> MimirMountSummary:
        """Aggregate every mount's summary.

        Counts are summed across mounts rather than de-duplicated by page path:
        de-duplication would mean listing the pages, which is exactly the
        corpus walk this call exists to avoid.

        A mount that cannot answer makes the totals wrong, so this raises. The
        summary feeds dashboards and ``/mimir/mounts``; a page count that
        silently omits a mount reads as a shrinking corpus, which is a worse
        signal than an outage that says so.
        """
        page_count = 0
        source_count = 0
        lint_issues = 0
        categories: set[str] = set()
        last_write = None
        lint_checked_at = None

        for mount in self._mounts:
            try:
                summary = await mount.port.summarize()
            except Exception as exc:
                raise _mount_failed("summarize", mount.name, exc) from exc

            page_count += summary.page_count
            source_count += summary.source_count
            lint_issues += summary.lint_issues
            categories.update(summary.categories)
            if summary.last_write is not None and (
                last_write is None or summary.last_write > last_write
            ):
                last_write = summary.last_write
            if summary.lint_checked_at is not None and (
                lint_checked_at is None or summary.lint_checked_at > lint_checked_at
            ):
                lint_checked_at = summary.lint_checked_at

        return MimirMountSummary(
            page_count=page_count,
            source_count=source_count,
            categories=sorted(categories),
            last_write=last_write,
            lint_issues=lint_issues,
            lint_checked_at=lint_checked_at,
        )

    async def read_source(self, source_id: str) -> MimirSource | None:
        """Return raw source from the first mount that has it.

        None means every mount answered and none had it. If no mount could
        answer at all, that is not absence — retry within the configured budget
        (a restarting Mímir is unreachable for as long as it takes to rebuild
        its index) and then raise, so callers do not report a Mímir outage as a
        missing source.
        """
        return await self._read_source_with_retry(
            source_id,
            lambda port: port.read_source(source_id),
        )

    async def read_source_excerpt(
        self,
        source_id: str,
        max_chars: int,
    ) -> MimirSource | None:
        """Bounded read from the first mount that has it.

        Shares ``read_source``'s outage semantics — an unanswerable read
        retries and then raises rather than passing for absence.
        """
        return await self._read_source_with_retry(
            source_id,
            lambda port: port.read_source_excerpt(source_id, max_chars),
        )

    async def _read_source_with_retry(
        self,
        source_id: str,
        read: Callable[[MimirPort], Awaitable[MimirSource | None]],
    ) -> MimirSource | None:
        """Fan out *read* across mounts, retrying while no mount can answer."""
        deadline = time.monotonic() + self._read_retry_max_seconds
        backoff = self._read_retry_initial_backoff_seconds
        attempt = 0

        while True:
            attempt += 1
            source, failures = await self._read_source_once(source_id, read)
            if source is not None:
                return source
            if not failures:
                # Every mount answered and none had it — real absence.
                return None
            # Any mount that could not answer might have been the one holding
            # it. Previously absence was concluded whenever *some* mount
            # answered, so a single broken mount turned a present source into a
            # missing one — the provenance-defect report an outage cannot fix.

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MimirUnavailableError(
                    f"could not read source {source_id} from any mount "
                    f"after {attempt} attempt(s) — " + "; ".join(failures)
                )

            delay = min(backoff, remaining)
            logger.warning(
                "composite mimir: no mount could read source %s (attempt %d); "
                "retrying in %.1fs — %s",
                _sanitize_log(source_id),
                attempt,
                delay,
                _sanitize_log("; ".join(failures)),
            )
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, self._read_retry_max_backoff_seconds)

    async def _read_source_once(
        self,
        source_id: str,
        read: Callable[[MimirPort], Awaitable[MimirSource | None]],
    ) -> tuple[MimirSource | None, list[str]]:
        """Try every mount once; return the source (if any) and per-mount failures."""
        failures: list[str] = []
        for mount in self._mounts:
            try:
                source = await read(mount.port)
                if source is not None:
                    return source, failures
            except Exception as exc:
                failures.append(f"{mount.name}: {exc}")
                logger.warning(
                    "composite mimir: read_source failed on %s: %s",
                    _sanitize_log(mount.name),
                    _sanitize_log(exc),
                )
        return None, failures

    async def read_source_from_mount(
        self,
        source_id: str,
        mount_name: str,
    ) -> MimirSource | None:
        """Return a raw source from the named mount only."""
        mount = self._mount_map.get(mount_name)
        if mount is None:
            return None
        try:
            return await mount.port.read_source(source_id)
        except Exception as exc:
            raise _mount_failed("read_source_from_mount", mount_name, exc) from exc

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        """List sources from all mounts, de-duplicated by source_id.

        When *unprocessed_only* is True, only sources not referenced by any page
        across all mounts are returned.
        """
        seen_ids: set[str] = set()
        results: list[MimirSourceMeta] = []

        for mount in self._mounts:
            try:
                sources = await mount.port.list_sources(unprocessed_only=unprocessed_only)
            except Exception as exc:
                raise _mount_failed("list_sources", mount.name, exc) from exc
            for meta in sources:
                if meta.source_id not in seen_ids:
                    seen_ids.add(meta.source_id)
                    meta.mount_name = mount.name
                    results.append(meta)

        return results

    async def lint(self, fix: bool = False) -> MimirLintReport:
        """Run lint on all mounts, merge issue lists.

        A mount that could not be linted must not be silently dropped: the
        merged report would read as a clean bill of health over a corpus that
        was never checked.
        """
        all_issues: list[LintIssue] = []
        pages_checked = 0

        for mount in self._mounts:
            try:
                report = await mount.port.lint(fix=fix)
            except Exception as exc:
                raise _mount_failed("lint", mount.name, exc) from exc
            all_issues.extend(report.issues)
            pages_checked += report.pages_checked

        return MimirLintReport(issues=all_issues, pages_checked=pages_checked)

    async def list_threads(
        self,
        state: ThreadState | None = None,
        limit: int = 100,
    ) -> list[MimirPage]:
        """List threads from all mounts in priority order, de-dup by path."""
        seen_paths: set[str] = set()
        results: list[MimirPage] = []

        for mount in self._mounts:
            try:
                pages = await mount.port.list_threads(state=state, limit=limit)
            except Exception as exc:
                raise _mount_failed("list_threads", mount.name, exc) from exc
            for page in pages:
                if page.meta.path not in seen_paths:
                    seen_paths.add(page.meta.path)
                    results.append(page)
                    if len(results) >= limit:
                        return results

        return results

    async def get_thread_queue(
        self,
        owner_id: str | None = None,
        limit: int = 50,
    ) -> list[MimirPage]:
        """Return open threads sorted by weight from all mounts, de-dup by path."""
        seen_paths: set[str] = set()
        results: list[MimirPage] = []

        for mount in self._mounts:
            try:
                pages = await mount.port.get_thread_queue(owner_id=owner_id, limit=limit)
            except Exception as exc:
                raise _mount_failed("get_thread_queue", mount.name, exc) from exc
            for page in pages:
                if page.meta.path not in seen_paths:
                    seen_paths.add(page.meta.path)
                    results.append(page)

        results.sort(key=lambda p: p.meta.thread_weight or 0.0, reverse=True)
        return results[:limit]

    async def update_thread_state(self, path: str, state: ThreadState) -> None:
        """Transition thread state on routed mounts."""
        target_names = self._write_routing.resolve(path)
        for name in target_names:
            mount = self._mount_map.get(name)
            if mount is None:
                raise _unknown_mount(f"update_thread_state({path!r})", name, self._mount_names())
            try:
                await mount.port.update_thread_state(path, state)
            except Exception as exc:
                raise _mount_failed("update_thread_state", name, exc) from exc

    async def assign_thread_owner(self, path: str, owner_id: str | None) -> None:
        """Claim thread ownership on routed mounts.

        Re-raises ``ThreadOwnershipError`` — callers must handle the race.
        """
        target_names = self._write_routing.resolve(path)
        for name in target_names:
            mount = self._mount_map.get(name)
            if mount is None:
                raise _unknown_mount(f"assign_thread_owner({path!r})", name, self._mount_names())
            try:
                await mount.port.assign_thread_owner(path, owner_id)
            except ThreadOwnershipError:
                raise
            except Exception as exc:
                raise _mount_failed("assign_thread_owner", name, exc) from exc

    async def update_thread_weight(
        self,
        path: str,
        weight: float,
        signals: dict | None = None,
    ) -> None:
        """Update thread weight on routed mounts (same routing as upsert_page)."""
        target_names = self._write_routing.resolve(path)
        for name in target_names:
            mount = self._mount_map.get(name)
            if mount is None:
                raise _unknown_mount(f"update_thread_weight({path!r})", name, self._mount_names())
            try:
                await mount.port.update_thread_weight(path, weight, signals)
            except Exception as exc:
                raise _mount_failed("update_thread_weight", name, exc) from exc

    # ------------------------------------------------------------------
    # MimirPort — write operations (routed)
    # ------------------------------------------------------------------

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        """Write *path* to the mounts selected by routing config or explicit *mimir*.

        Routing precedence:
        1. Explicit ``mimir=`` parameter (agent override — bypasses all rules).
        2. Category-prefix matching from ``write_routing.rules``.
        3. ``write_routing.default`` fallback.
        """
        target_names = self._write_routing.resolve(path, explicit=mimir)
        for name in target_names:
            mount = self._mount_map.get(name)
            if mount is None:
                raise _unknown_mount(f"upsert_page({path!r})", name, self._mount_names())
            try:
                await mount.port.upsert_page(path, content, meta=meta)
            except Exception as exc:
                raise _mount_failed("upsert_page", name, exc) from exc
            logger.debug(
                "composite mimir: wrote %s to mount %s",
                _sanitize_log(path),
                _sanitize_log(name),
            )

    async def delete_page(self, path: str, mimir: str | None = None) -> bool:
        """Delete *path* from the mounts selected by normal write routing."""
        deleted = False
        for name in self._write_routing.resolve(path, explicit=mimir):
            mount = self._mount_map.get(name)
            if mount is None:
                raise _unknown_mount(f"delete_page({path!r})", name, self._mount_names())
            try:
                deleted = await mount.port.delete_page(path) or deleted
            except Exception as exc:
                raise _mount_failed("delete_page", name, exc) from exc
        return deleted

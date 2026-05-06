"""Unit tests for Mímir thread support (NIU-560).

Tests cover:
- ThreadState enum values
- ThreadContextRef dataclass
- ThreadYamlSchema: round-trip, from_yaml validation, to_yaml serialisation
- ThreadSchemaError raised on missing/invalid fields
- ThreadOwnershipError raised on conflict
- slugify() utility
- MimirPageMeta thread fields (is_thread, thread_state, thread_weight)
- MarkdownMimirAdapter thread methods:
    - create_thread: directory creation, YAML + MD written, page returned
    - get_thread: reads YAML + MD; raises FileNotFoundError for missing
    - get_thread_queue: YAML-only hot path, never opens .md files
    - update_thread_state: updates YAML only
    - update_thread_weight: updates YAML only
    - assign_thread_owner: lock file, conflict detection
- Acceptance criteria:
    - threads/ directory auto-created on first write
    - get_thread_queue verified not to open .md files
    - YAML round-trip: write → read → same field values
    - ThreadYamlSchema.from_yaml() raises ThreadSchemaError on invalid data
    - assign_thread_owner raises ThreadOwnershipError on conflict
    - Markdown only read on get_thread()
    - Existing wiki/ pages unaffected
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from mimir.adapters.markdown import MarkdownMimirAdapter
from niuu.domain.mimir import (
    MimirPageMeta,
    ThreadContextRef,
    ThreadOwnershipError,
    ThreadSchemaError,
    ThreadState,
    ThreadYamlSchema,
    slugify,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Path) -> MarkdownMimirAdapter:
    return MarkdownMimirAdapter(root=tmp_path / "mimir")


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars() -> None:
    assert slugify("Retrieval Architecture (HNSW)!") == "retrieval-architecture-hnsw"


def test_slugify_multiple_spaces() -> None:
    assert slugify("  leading  trailing  ") == "leading-trailing"


def test_slugify_unicode_to_ascii() -> None:
    result = slugify("Móðir föður")
    # Should be ASCII-safe
    assert result.isascii()
    assert "-" in result


# ---------------------------------------------------------------------------
# ThreadState
# ---------------------------------------------------------------------------


def test_thread_state_values() -> None:
    assert ThreadState.open == "open"
    assert ThreadState.pulling == "pulling"
    assert ThreadState.blocked == "blocked"
    assert ThreadState.closed == "closed"
    assert ThreadState.dissolved == "dissolved"


def test_thread_state_from_string() -> None:
    assert ThreadState("open") == ThreadState.open
    assert ThreadState("pulling") == ThreadState.pulling
    assert ThreadState("closed") == ThreadState.closed


def test_thread_state_pulling_is_str() -> None:
    """ThreadState.pulling must be a plain string (StrEnum behaviour)."""
    assert isinstance(ThreadState.pulling, str)
    assert str(ThreadState.pulling) == "pulling"


# ---------------------------------------------------------------------------
# ThreadContextRef
# ---------------------------------------------------------------------------


def test_thread_context_ref_fields() -> None:
    ref = ThreadContextRef(
        ref_type="conversation", ref_id="session_abc", ref_summary="Evening discussion"
    )
    assert ref.ref_type == "conversation"
    assert ref.ref_id == "session_abc"
    assert ref.ref_summary == "Evening discussion"


# ---------------------------------------------------------------------------
# ThreadOwnershipError
# ---------------------------------------------------------------------------


def test_thread_ownership_error_message() -> None:
    err = ThreadOwnershipError("threads/my-thread", "agent-1")
    assert "agent-1" in str(err)
    assert err.path == "threads/my-thread"
    assert err.current_owner == "agent-1"


# ---------------------------------------------------------------------------
# ThreadYamlSchema — construction and round-trip
# ---------------------------------------------------------------------------


def test_thread_yaml_schema_round_trip(tmp_path: Path) -> None:
    """Write a schema to YAML then read it back — all fields must match."""
    now = datetime.now(UTC)
    schema = ThreadYamlSchema(
        title="Retrieval architecture",
        state=ThreadState.open,
        weight=0.85,
        created_at=now,
        updated_at=now,
        owner_id=None,
        next_action_hint="Compare HNSW vs flat",
        resolved_artifact_path=None,
        context_refs=[
            ThreadContextRef(ref_type="conversation", ref_id="s1", ref_summary="Evening chat")
        ],
        weight_signals={"age_days": 1.0, "mention_count": 2},
    )
    yaml_path = tmp_path / "test.yaml"
    schema.to_yaml(yaml_path)

    loaded = ThreadYamlSchema.from_yaml(yaml_path)
    assert loaded.title == schema.title
    assert loaded.state == schema.state
    assert abs(loaded.weight - schema.weight) < 1e-9
    assert loaded.next_action_hint == schema.next_action_hint
    assert loaded.owner_id is None
    assert loaded.resolved_artifact_path is None
    assert len(loaded.context_refs) == 1
    assert loaded.context_refs[0].ref_type == "conversation"
    assert loaded.context_refs[0].ref_id == "s1"
    assert loaded.weight_signals["mention_count"] == 2


def test_thread_yaml_schema_nullable_fields(tmp_path: Path) -> None:
    """Nullable fields default to None when absent from YAML."""
    now = datetime.now(UTC)
    schema = ThreadYamlSchema(
        title="Test Thread",
        state=ThreadState.open,
        weight=0.5,
        created_at=now,
        updated_at=now,
    )
    yaml_path = tmp_path / "nullable.yaml"
    schema.to_yaml(yaml_path)
    loaded = ThreadYamlSchema.from_yaml(yaml_path)
    assert loaded.owner_id is None
    assert loaded.next_action_hint is None
    assert loaded.resolved_artifact_path is None
    assert loaded.context_refs == []


# ---------------------------------------------------------------------------
# ThreadYamlSchema.from_yaml — validation errors
# ---------------------------------------------------------------------------


def test_from_yaml_raises_on_missing_required_field(tmp_path: Path) -> None:
    """Missing required fields must raise ThreadSchemaError."""
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("title: Only Title\n", encoding="utf-8")
    with pytest.raises(ThreadSchemaError, match="is required"):
        ThreadYamlSchema.from_yaml(yaml_path)


def test_from_yaml_raises_on_invalid_state(tmp_path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    yaml_path = tmp_path / "bad_state.yaml"
    yaml_path.write_text(
        f"title: T\nstate: flying\nweight: 0.5\ncreated_at: {now}\nupdated_at: {now}\n",
        encoding="utf-8",
    )
    with pytest.raises(ThreadSchemaError, match="not a valid ThreadState"):
        ThreadYamlSchema.from_yaml(yaml_path)


def test_from_yaml_raises_on_invalid_date(tmp_path: Path) -> None:
    yaml_path = tmp_path / "bad_date.yaml"
    yaml_path.write_text(
        "title: T\nstate: open\nweight: 0.5\ncreated_at: not-a-date\nupdated_at: also-bad\n",
        encoding="utf-8",
    )
    with pytest.raises(ThreadSchemaError, match="created_at"):
        ThreadYamlSchema.from_yaml(yaml_path)


def test_from_yaml_raises_on_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ThreadSchemaError):
        ThreadYamlSchema.from_yaml(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# MimirPageMeta thread fields
# ---------------------------------------------------------------------------


def test_mimir_page_meta_thread_fields() -> None:
    meta = MimirPageMeta(
        path="threads/retrieval-architecture",
        title="Retrieval architecture",
        summary="Compare HNSW vs flat",
        category="threads",
        updated_at=datetime.now(UTC),
        is_thread=True,
        thread_state=ThreadState.open,
        thread_weight=0.85,
    )
    assert meta.is_thread is True
    assert meta.thread_state == ThreadState.open
    assert meta.thread_weight == 0.85


def test_mimir_page_meta_defaults_to_not_thread() -> None:
    meta = MimirPageMeta(
        path="technical/foo.md",
        title="Foo",
        summary="Something.",
        category="technical",
        updated_at=datetime.now(UTC),
    )
    assert meta.is_thread is False
    assert meta.thread_state is None
    assert meta.thread_weight is None


# ---------------------------------------------------------------------------
# MarkdownMimirAdapter — threads/ directory auto-creation
# ---------------------------------------------------------------------------


def test_adapter_creates_threads_directory(tmp_path: Path) -> None:
    """threads/ directory must be created on adapter init."""
    _make_adapter(tmp_path)
    assert (tmp_path / "mimir" / "threads").is_dir()


# ---------------------------------------------------------------------------
# create_thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_thread_writes_yaml_and_md(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    page = await adapter.create_thread(title="Retrieval Architecture")

    slug = "retrieval-architecture"
    yaml_path = tmp_path / "mimir" / "threads" / f"{slug}.yaml"
    md_path = tmp_path / "mimir" / "threads" / f"{slug}.md"

    assert yaml_path.exists()
    assert md_path.exists()
    assert page.meta.is_thread is True
    assert page.meta.thread_state == ThreadState.open
    assert page.meta.path == f"threads/{slug}"


@pytest.mark.asyncio
async def test_create_thread_yaml_has_correct_state(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="State Test Thread")
    yaml_path = tmp_path / "mimir" / "threads" / "state-test-thread.yaml"
    schema = ThreadYamlSchema.from_yaml(yaml_path)
    assert schema.state == ThreadState.open


@pytest.mark.asyncio
async def test_create_thread_md_has_sections(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Section Test")
    md_path = tmp_path / "mimir" / "threads" / "section-test.md"
    content = md_path.read_text(encoding="utf-8")
    assert "## Context" in content
    assert "## What I know so far" in content
    assert "## Open questions" in content
    assert "## Next action" in content
    assert "## History" in content


@pytest.mark.asyncio
async def test_create_thread_with_context_refs(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    refs = [
        ThreadContextRef(ref_type="conversation", ref_id="sess_abc", ref_summary="Evening chat")
    ]
    page = await adapter.create_thread(title="Context Thread", context_refs=refs)
    assert page.meta.is_thread is True
    # MD history should include the context summary
    assert "Evening chat" in page.content


@pytest.mark.asyncio
async def test_create_thread_with_weight(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    page = await adapter.create_thread(title="Weighted Thread", weight=0.9)
    assert page.meta.thread_weight == 0.9


@pytest.mark.asyncio
async def test_create_thread_raises_if_duplicate(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Duplicate Thread")
    with pytest.raises(FileExistsError):
        await adapter.create_thread(title="Duplicate Thread")


# ---------------------------------------------------------------------------
# get_thread — loads YAML + MD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_thread_returns_full_content(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Full Thread")
    page = await adapter.get_thread("threads/full-thread")
    assert page.meta.title == "Full Thread"
    assert "## Context" in page.content


@pytest.mark.asyncio
async def test_get_thread_raises_for_missing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(FileNotFoundError):
        await adapter.get_thread("threads/nonexistent")


@pytest.mark.asyncio
async def test_get_thread_content_empty_when_md_missing(tmp_path: Path) -> None:
    """If MD file is missing (edge case), content should be empty string."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="No MD Thread")
    # Remove the markdown file manually
    md_path = tmp_path / "mimir" / "threads" / "no-md-thread.md"
    md_path.unlink()
    page = await adapter.get_thread("threads/no-md-thread")
    assert page.content == ""


# ---------------------------------------------------------------------------
# get_thread_queue — hot path, YAML only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_thread_queue_returns_open_threads(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Thread A", weight=0.7)
    await adapter.create_thread(title="Thread B", weight=0.9)
    pages = await adapter.get_thread_queue()
    titles = [p.meta.title for p in pages]
    assert "Thread A" in titles
    assert "Thread B" in titles


@pytest.mark.asyncio
async def test_get_thread_queue_sorted_by_weight_desc(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Low Weight", weight=0.1)
    await adapter.create_thread(title="High Weight", weight=0.9)
    pages = await adapter.get_thread_queue()
    assert pages[0].meta.title == "High Weight"
    assert pages[1].meta.title == "Low Weight"


@pytest.mark.asyncio
async def test_get_thread_queue_excludes_closed(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Open Thread")
    await adapter.create_thread(title="Closed Thread")
    await adapter.update_thread_state("threads/closed-thread", ThreadState.closed)
    pages = await adapter.get_thread_queue()
    titles = [p.meta.title for p in pages]
    assert "Open Thread" in titles
    assert "Closed Thread" not in titles


@pytest.mark.asyncio
async def test_get_thread_queue_excludes_dissolved(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Dissolved Thread")
    await adapter.update_thread_state("threads/dissolved-thread", ThreadState.dissolved)
    pages = await adapter.get_thread_queue()
    assert all(p.meta.title != "Dissolved Thread" for p in pages)


@pytest.mark.asyncio
async def test_get_thread_queue_respects_limit(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    for i in range(5):
        await adapter.create_thread(title=f"Thread {i}", weight=float(i) / 10)
    pages = await adapter.get_thread_queue(limit=3)
    assert len(pages) == 3


@pytest.mark.asyncio
async def test_get_thread_queue_never_opens_md_files(tmp_path: Path) -> None:
    """Hot path: get_thread_queue must NEVER open .md files."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Hot Path Thread")

    md_open_calls: list[str] = []
    original_open = Path.read_text

    def tracking_read_text(self: Path, *args, **kwargs) -> str:  # type: ignore[override]
        if self.suffix == ".md":
            md_open_calls.append(str(self))
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "read_text", tracking_read_text):
        await adapter.get_thread_queue()

    assert md_open_calls == [], f"get_thread_queue opened MD files: {md_open_calls}"


@pytest.mark.asyncio
async def test_get_thread_queue_filter_by_owner(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Owned Thread")
    await adapter.create_thread(title="Other Thread")
    await adapter.assign_thread_owner("threads/owned-thread", "agent-1")
    pages = await adapter.get_thread_queue(owner_id="agent-1")
    # The thread owned by agent-1 should appear; unowned threads also appear
    titles = [p.meta.title for p in pages]
    assert "Owned Thread" in titles


# ---------------------------------------------------------------------------
# update_thread_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_thread_state_changes_yaml(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="State Change Thread")
    await adapter.update_thread_state("threads/state-change-thread", ThreadState.closed)
    schema = ThreadYamlSchema.from_yaml(tmp_path / "mimir" / "threads" / "state-change-thread.yaml")
    assert schema.state == ThreadState.closed


@pytest.mark.asyncio
async def test_update_thread_state_raises_for_missing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(FileNotFoundError):
        await adapter.update_thread_state("threads/nonexistent", ThreadState.closed)


# ---------------------------------------------------------------------------
# update_thread_weight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_thread_weight_changes_yaml(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Weight Thread", weight=0.5)
    await adapter.update_thread_weight("threads/weight-thread", 0.99)
    schema = ThreadYamlSchema.from_yaml(tmp_path / "mimir" / "threads" / "weight-thread.yaml")
    assert abs(schema.weight - 0.99) < 1e-9


@pytest.mark.asyncio
async def test_update_thread_weight_raises_for_missing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(FileNotFoundError):
        await adapter.update_thread_weight("threads/nonexistent", 0.5)


@pytest.mark.asyncio
async def test_update_thread_weight_stores_signals(tmp_path: Path) -> None:
    """When signals are provided they must be persisted in the YAML."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Signal Thread", weight=0.5)
    signals = {"age_days": 3.0, "mention_count": 5}
    await adapter.update_thread_weight("threads/signal-thread", 0.75, signals=signals)
    schema = ThreadYamlSchema.from_yaml(tmp_path / "mimir" / "threads" / "signal-thread.yaml")
    assert abs(schema.weight - 0.75) < 1e-9
    assert schema.weight_signals["age_days"] == 3.0
    assert schema.weight_signals["mention_count"] == 5


@pytest.mark.asyncio
async def test_update_thread_weight_signals_none_preserves_existing(tmp_path: Path) -> None:
    """Passing signals=None must leave existing weight_signals untouched."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Preserve Signals Thread", weight=0.5)
    await adapter.update_thread_weight("threads/preserve-signals-thread", 0.6, signals={"x": 1.0})
    await adapter.update_thread_weight("threads/preserve-signals-thread", 0.7)
    schema = ThreadYamlSchema.from_yaml(
        tmp_path / "mimir" / "threads" / "preserve-signals-thread.yaml"
    )
    assert schema.weight_signals == {"x": 1.0}


# ---------------------------------------------------------------------------
# list_threads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_threads_returns_all_threads(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Thread Alpha")
    await adapter.create_thread(title="Thread Beta")
    pages = await adapter.list_threads()
    titles = [p.meta.title for p in pages]
    assert "Thread Alpha" in titles
    assert "Thread Beta" in titles


@pytest.mark.asyncio
async def test_list_threads_empty_when_no_threads(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    pages = await adapter.list_threads()
    assert pages == []


@pytest.mark.asyncio
async def test_list_threads_filtered_by_state(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Open Thread")
    await adapter.create_thread(title="Closed Thread")
    await adapter.update_thread_state("threads/closed-thread", ThreadState.closed)
    open_pages = await adapter.list_threads(state=ThreadState.open)
    titles = [p.meta.title for p in open_pages]
    assert "Open Thread" in titles
    assert "Closed Thread" not in titles


@pytest.mark.asyncio
async def test_list_threads_filtered_by_blocked_state(tmp_path: Path) -> None:
    """ThreadState.blocked must work as a filter value."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Open Thread 2")
    await adapter.create_thread(title="Blocked Thread")
    await adapter.update_thread_state("threads/blocked-thread", ThreadState.blocked)
    pages = await adapter.list_threads(state=ThreadState.blocked)
    titles = [p.meta.title for p in pages]
    assert "Blocked Thread" in titles
    assert "Open Thread 2" not in titles


@pytest.mark.asyncio
async def test_list_threads_filtered_by_pulling_state(tmp_path: Path) -> None:
    """ThreadState.pulling must work as a filter value."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Open Thread 2")
    await adapter.create_thread(title="Pulling Thread")
    await adapter.update_thread_state("threads/pulling-thread", ThreadState.pulling)
    pages = await adapter.list_threads(state=ThreadState.pulling)
    titles = [p.meta.title for p in pages]
    assert "Pulling Thread" in titles
    assert "Open Thread 2" not in titles


@pytest.mark.asyncio
async def test_list_threads_respects_limit(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    for i in range(5):
        await adapter.create_thread(title=f"Limited Thread {i}")
    pages = await adapter.list_threads(limit=3)
    assert len(pages) == 3


# ---------------------------------------------------------------------------
# assign_thread_owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_thread_owner_sets_owner(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Owner Thread")
    await adapter.assign_thread_owner("threads/owner-thread", "agent-1")
    schema = ThreadYamlSchema.from_yaml(tmp_path / "mimir" / "threads" / "owner-thread.yaml")
    assert schema.owner_id == "agent-1"


@pytest.mark.asyncio
async def test_assign_thread_owner_conflict_raises(tmp_path: Path) -> None:
    """assign_thread_owner raises ThreadOwnershipError when owner differs."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Conflict Thread")
    await adapter.assign_thread_owner("threads/conflict-thread", "agent-1")
    with pytest.raises(ThreadOwnershipError) as exc_info:
        await adapter.assign_thread_owner("threads/conflict-thread", "agent-2")
    assert exc_info.value.current_owner == "agent-1"


@pytest.mark.asyncio
async def test_assign_thread_owner_same_owner_is_idempotent(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Idempotent Thread")
    await adapter.assign_thread_owner("threads/idempotent-thread", "agent-1")
    # Same owner should succeed without error
    await adapter.assign_thread_owner("threads/idempotent-thread", "agent-1")


@pytest.mark.asyncio
async def test_assign_thread_owner_clear_owner(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Clear Owner Thread")
    await adapter.assign_thread_owner("threads/clear-owner-thread", "agent-1")
    await adapter.assign_thread_owner("threads/clear-owner-thread", None)
    schema = ThreadYamlSchema.from_yaml(tmp_path / "mimir" / "threads" / "clear-owner-thread.yaml")
    assert schema.owner_id is None


@pytest.mark.asyncio
async def test_assign_thread_owner_lock_file_removed_on_success(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Lock Thread")
    await adapter.assign_thread_owner("threads/lock-thread", "agent-1")
    lock_path = tmp_path / "mimir" / "threads" / "lock-thread.lock"
    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_assign_thread_owner_lock_file_removed_on_conflict(tmp_path: Path) -> None:
    """Lock file must be cleaned up even when ThreadOwnershipError is raised."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Conflict Lock Thread")
    await adapter.assign_thread_owner("threads/conflict-lock-thread", "agent-1")
    try:
        await adapter.assign_thread_owner("threads/conflict-lock-thread", "agent-2")
    except ThreadOwnershipError:
        pass
    lock_path = tmp_path / "mimir" / "threads" / "conflict-lock-thread.lock"
    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_assign_thread_owner_raises_for_missing(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    with pytest.raises(FileNotFoundError):
        await adapter.assign_thread_owner("threads/nonexistent", "agent-1")


# ---------------------------------------------------------------------------
# Acceptance: markdown only read in get_thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_thread_reads_markdown(tmp_path: Path) -> None:
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="MD Read Thread")
    # Modify the MD file directly
    md_path = tmp_path / "mimir" / "threads" / "md-read-thread.md"
    md_path.write_text("# MD Read Thread\n\nCustom content.", encoding="utf-8")
    page = await adapter.get_thread("threads/md-read-thread")
    assert "Custom content." in page.content


# ---------------------------------------------------------------------------
# Acceptance: existing wiki pages unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wiki_pages_unaffected_by_thread_operations(tmp_path: Path) -> None:
    """Thread operations must not affect existing wiki pages."""
    adapter = _make_adapter(tmp_path)
    content = "# My Wiki Page\n\nWiki content."
    await adapter.upsert_page("technical/my-page.md", content)
    # Now create a thread
    await adapter.create_thread(title="Parallel Thread")
    # Wiki page still reads correctly
    result = await adapter.read_page("technical/my-page.md")
    assert result == content
    # Thread doesn't show up in wiki list
    pages = await adapter.list_pages()
    paths = [p.path for p in pages]
    assert "threads/parallel-thread" not in paths


@pytest.mark.asyncio
async def test_thread_not_returned_by_search(tmp_path: Path) -> None:
    """Threads are stored outside wiki/ so search should not find them."""
    adapter = _make_adapter(tmp_path)
    await adapter.create_thread(title="Unique Zxqwerty Thread")
    results = await adapter.search("zxqwerty")
    paths = [p.meta.path for p in results]
    assert not any("threads" in p for p in paths)


# ---------------------------------------------------------------------------
# Security: path traversal rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_thread_rejects_empty_slug(tmp_path: Path) -> None:
    """Empty or all-special titles must raise ValueError (no hidden dotfiles)."""
    adapter = _make_adapter(tmp_path)
    with pytest.raises(ValueError, match="empty slug"):
        await adapter.create_thread(title="")


@pytest.mark.asyncio
async def test_create_thread_rejects_special_char_only_title(tmp_path: Path) -> None:
    """A title that slugifies to empty must also raise ValueError."""
    adapter = _make_adapter(tmp_path)
    with pytest.raises(ValueError, match="empty slug"):
        await adapter.create_thread(title="!@#$%^&*()")


@pytest.mark.asyncio
async def test_get_thread_rejects_path_traversal(tmp_path: Path) -> None:
    """get_thread must raise PathSecurityError on traversal attempts."""
    from mimir.adapters.markdown import PathSecurityError

    adapter = _make_adapter(tmp_path)
    with pytest.raises(PathSecurityError):
        await adapter.get_thread("threads/../../etc/passwd")


@pytest.mark.asyncio
async def test_update_thread_state_rejects_path_traversal(tmp_path: Path) -> None:
    """update_thread_state must raise PathSecurityError on traversal attempts."""
    from mimir.adapters.markdown import PathSecurityError

    adapter = _make_adapter(tmp_path)
    with pytest.raises(PathSecurityError):
        await adapter.update_thread_state("threads/../../evil", ThreadState.closed)


@pytest.mark.asyncio
async def test_update_thread_weight_rejects_path_traversal(tmp_path: Path) -> None:
    """update_thread_weight must raise PathSecurityError on traversal attempts."""
    from mimir.adapters.markdown import PathSecurityError

    adapter = _make_adapter(tmp_path)
    with pytest.raises(PathSecurityError):
        await adapter.update_thread_weight("threads/../../evil", 0.5)


@pytest.mark.asyncio
async def test_assign_thread_owner_rejects_path_traversal(tmp_path: Path) -> None:
    """assign_thread_owner must raise PathSecurityError on traversal attempts."""
    from mimir.adapters.markdown import PathSecurityError

    adapter = _make_adapter(tmp_path)
    with pytest.raises(PathSecurityError):
        await adapter.assign_thread_owner("threads/../../evil", "agent-1")

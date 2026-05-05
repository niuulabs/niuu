/**
 * PagesView — three-pane knowledge browser.
 *
 *  Left: file-tree sidebar (multi-mount union merge)
 * Centre: page reader (layout toggle: structured | split)
 * Right:  page meta panel (provenance, sources, backlinks)
 */

import { useState, useReducer, useEffect, useRef, useMemo, Fragment } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import { useMimirPages, useMimirPage, useMimirPageSources } from './useMimirPages';
import { TreeNode } from './components/TreeNode';
import { ZoneBlock } from './components/ZoneBlock';
import { MetaPanel } from './components/MetaPanel';
import { MountChip } from './components/MountChip';
import { RawSourcePane } from './components/RawSourcePane';
import { mergeFileTrees } from '../domain';
import { zoneEditReducer } from '../domain/zone-edit';
import type { Zone, Page, PageMeta } from '../domain/page';
import type { ZoneEditState } from '../domain/zone-edit';
import type { IMimirService } from '../ports';
import './mimir-views.css';

/** Delay (ms) before auto-resetting zone edit state after a successful save. */
const ZONE_SAVE_RESET_DELAY_MS = 3_000;

type ReaderLayout = 'structured' | 'split';

export function PagesView() {
  const { data: allPages = [] } = useMimirPages();
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [readerLayout, setReaderLayout] = useState<ReaderLayout>('structured');

  // Auto-select the first page once data loads.
  useEffect(() => {
    if (selectedPath === null && allPages.length > 0) {
      setSelectedPath(allPages[0]?.path ?? null);
    }
  }, [allPages, selectedPath]);

  const activePagePath = selectedPath ?? allPages[0]?.path ?? null;
  const { data: page } = useMimirPage(activePagePath);
  const { data: pageSources = [] } = useMimirPageSources(activePagePath);
  const service = useService<IMimirService>('mimir');

  const [editState, dispatch] = useReducer(zoneEditReducer, { status: 'idle' });
  const saveResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clean up any pending reset timer on unmount.
  useEffect(() => {
    return () => {
      if (saveResetTimerRef.current !== null) clearTimeout(saveResetTimerRef.current);
    };
  }, []);

  const tree = mergeFileTrees(allPages);

  /** O(N) set of known paths for O(1) broken-link lookup in TreeNode leaves. */
  const knownPaths = useMemo(
    () => new Set(allPages.flatMap((p) => [p.path, p.path.replace(/^\//, '')])),
    [allPages],
  );

  function handleNavigate(slug: string) {
    const target = allPages.find((p) => p.path === `/${slug}` || p.path === slug);
    if (target) setSelectedPath(target.path);
  }

  function handleEdit(zone: Zone) {
    if (!activePagePath) return;
    dispatch({ type: 'START_EDIT', path: activePagePath, zoneKind: zone.kind, zone });
  }

  /** Trigger zone edit for the first zone of the current page. */
  function handleEditFirstZone() {
    const firstZone = page?.zones?.[0];
    if (firstZone) handleEdit(firstZone);
  }

  async function handleSave(text: string) {
    if (!page || editState.status !== 'editing') return;
    const mounts = page.mounts;
    dispatch({ type: 'BEGIN_SAVE', destinationMounts: mounts });
    try {
      await service.pages.upsertPage(page.path, text, mounts[0]);
      dispatch({ type: 'SAVE_SUCCESS', savedAt: new Date().toISOString() });
      saveResetTimerRef.current = setTimeout(
        () => dispatch({ type: 'RESET' }),
        ZONE_SAVE_RESET_DELAY_MS,
      );
    } catch (err) {
      dispatch({
        type: 'SAVE_ERROR',
        message: err instanceof Error ? err.message : 'save failed',
      });
    }
  }

  function handleCancel() {
    dispatch({ type: 'CANCEL' });
  }

  /** Copy page path + title to clipboard. */
  async function handleCite() {
    if (!page) return;
    await navigator.clipboard.writeText(`${page.path} — ${page.title}`);
  }

  /** Flag the page for re-synthesis (stub — service method not yet implemented). */
  function handleFlag() {
    // no-op: flagging service method not yet available on IPageStore
  }

  /** Promote the page's confidence level (stub — service method not yet implemented). */
  function handlePromote() {
    // no-op: confidence promotion service method not yet available on IPageStore
  }

  const breadcrumbs = activePagePath ? activePagePath.split('/').filter(Boolean) : [];

  return (
    <div className="niuu-grid niuu-grid-cols-[280px_1fr_380px] niuu-h-full niuu-overflow-hidden">
      {/* ── File tree sidebar ──────────────────────────────────── */}
      <aside
        className="niuu-bg-bg-secondary niuu-border-r niuu-border-border-subtle niuu-flex niuu-flex-col niuu-overflow-hidden"
        aria-label="page tree"
      >
        <div className="niuu-flex niuu-items-start niuu-justify-between niuu-px-4 niuu-py-3 niuu-border-b niuu-border-border-subtle niuu-flex-shrink-0">
          <div className="niuu-flex niuu-flex-col niuu-gap-0.5">
            <span className="niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
              Pages
            </span>
            <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-faint">
              merged mount tree
            </span>
          </div>
          <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
            {allPages.length}
          </span>
        </div>
        <div className="niuu-overflow-y-auto niuu-flex-1">
          {Object.values(tree.children).map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={0}
              selectedPath={activePagePath}
              onSelect={setSelectedPath}
              knownPaths={knownPaths}
            />
          ))}
        </div>
      </aside>

      {/* ── Page reader ────────────────────────────────────────── */}
      <main className="niuu-overflow-hidden niuu-flex niuu-flex-col niuu-border-r niuu-border-border-subtle">
        {page ? (
          <>
            {/* Reader layout toggle */}
            <div className="niuu-flex niuu-items-center niuu-gap-1 niuu-px-6 niuu-py-2.5 niuu-border-b niuu-border-border-subtle niuu-flex-shrink-0">
              <button
                type="button"
                className={[
                  'niuu-px-3 niuu-py-1 niuu-text-xs niuu-rounded-sm niuu-border',
                  readerLayout === 'structured'
                    ? 'niuu-bg-bg-tertiary niuu-text-text-primary niuu-border-border'
                    : 'niuu-bg-transparent niuu-text-text-muted niuu-border-transparent',
                ].join(' ')}
                onClick={() => setReaderLayout('structured')}
                aria-pressed={readerLayout === 'structured'}
              >
                Structured
              </button>
              <button
                type="button"
                className={[
                  'niuu-px-3 niuu-py-1 niuu-text-xs niuu-rounded-sm niuu-border',
                  readerLayout === 'split'
                    ? 'niuu-bg-bg-tertiary niuu-text-text-primary niuu-border-border'
                    : 'niuu-bg-transparent niuu-text-text-muted niuu-border-transparent',
                ].join(' ')}
                onClick={() => setReaderLayout('split')}
                aria-pressed={readerLayout === 'split'}
              >
                Split
              </button>
            </div>

            {/* Main content area */}
            {readerLayout === 'split' ? (
              <div className="niuu-grid niuu-grid-cols-[1.2fr_1fr] niuu-flex-1 niuu-overflow-hidden">
                {/* Left: structured zones */}
                <div className="niuu-overflow-y-auto niuu-border-r niuu-border-border-subtle">
                  <PageContent
                    page={page}
                    breadcrumbs={breadcrumbs}
                    allPages={allPages}
                    editState={editState}
                    onNavigate={handleNavigate}
                    onEdit={handleEdit}
                    onSave={handleSave}
                    onCancel={handleCancel}
                    onEditFirstZone={handleEditFirstZone}
                    onFlag={handleFlag}
                    onPromote={handlePromote}
                    onCite={handleCite}
                  />
                </div>
                {/* Right: raw sources */}
                <div className="niuu-overflow-y-auto niuu-bg-bg-secondary">
                  <RawSourcePane sources={pageSources} onNavigate={handleNavigate} />
                </div>
              </div>
            ) : (
              <div className="niuu-overflow-y-auto niuu-flex-1">
                <PageContent
                  page={page}
                  breadcrumbs={breadcrumbs}
                  allPages={allPages}
                  editState={editState}
                  onNavigate={handleNavigate}
                  onEdit={handleEdit}
                  onSave={handleSave}
                  onCancel={handleCancel}
                  onEditFirstZone={handleEditFirstZone}
                  onFlag={handleFlag}
                  onPromote={handlePromote}
                  onCite={handleCite}
                />
              </div>
            )}
          </>
        ) : (
          <div className="niuu-flex niuu-items-center niuu-justify-center niuu-h-full niuu-text-sm niuu-text-text-muted">
            Select a page from the tree
          </div>
        )}
      </main>

      {/* ── Meta panel ─────────────────────────────────────────── */}
      <aside aria-label="page metadata">
        {page && (
          <MetaPanel
            page={page}
            sources={pageSources}
            allPages={allPages}
            onNavigate={handleNavigate}
          />
        )}
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Internal sub-component: the scrollable page body (shared by both layout modes)
// ---------------------------------------------------------------------------

interface PageContentProps {
  page: Page;
  breadcrumbs: string[];
  allPages: PageMeta[];
  editState: ZoneEditState;
  onNavigate: (slug: string) => void;
  onEdit: (zone: Zone) => void;
  onSave: (text: string) => Promise<void>;
  onCancel: () => void;
  onEditFirstZone: () => void;
  onFlag: () => void;
  onPromote: () => void;
  onCite: () => Promise<void>;
}

function PageContent({
  page,
  breadcrumbs,
  allPages,
  editState,
  onNavigate,
  onEdit,
  onSave,
  onCancel,
  onEditFirstZone,
  onFlag,
  onPromote,
  onCite,
}: PageContentProps) {
  return (
    <div className="niuu-p-6">
      {/* breadcrumbs */}
      <div
        className="niuu-flex niuu-items-center niuu-gap-1 niuu-font-mono niuu-text-xs niuu-text-text-muted niuu-mb-3"
        aria-label="breadcrumb"
      >
        {breadcrumbs.slice(0, -1).map((part, i) => (
          <Fragment key={i}>
            <span>{part}</span>
            <span className="niuu-text-border">/</span>
          </Fragment>
        ))}
        <span className="niuu-text-brand-300">{breadcrumbs[breadcrumbs.length - 1]}</span>
      </div>

      <h1 className="niuu-text-3xl niuu-font-semibold niuu-text-text-primary niuu-m-0 niuu-mb-2 niuu-tracking-tight">
        {page.title}
      </h1>
      <p className="niuu-text-base niuu-leading-relaxed niuu-text-text-secondary niuu-m-0 niuu-mb-4">
        {page.summary}
      </p>

      {/* action bar — matches web2 layout: actions first, then chips */}
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-mb-4 niuu-flex-wrap">
        <button
          type="button"
          className="mm-btn"
          onClick={onEditFirstZone}
          disabled={editState.status !== 'idle' || !page.zones?.length}
          aria-label="edit page"
        >
          ✎ Edit
        </button>
        <button type="button" className="mm-btn" onClick={onFlag} aria-label="flag for review">
          ⚑ Flag
        </button>
        <button
          type="button"
          className="mm-btn"
          onClick={onPromote}
          aria-label="promote confidence"
        >
          Promote confidence
        </button>
        <div className="niuu-flex-1" />
        <button
          type="button"
          className="mm-btn niuu-font-mono"
          onClick={onCite}
          aria-label="cite page"
        >
          ⌘K cite
        </button>
      </div>

      {/* chip bar */}
      <div className="mm-chip-bar">
        <span className="mm-chip">
          <span className="mm-chip-k">type</span> {page.type}
        </span>
        <span className="mm-chip">
          <span className="mm-chip-k">confidence</span> {page.confidence}
        </span>
        {page.mounts.map((m) => (
          <MountChip key={m} name={m} />
        ))}
      </div>

      {/* zones */}
      {(page.zones ?? []).map((zone, i) => (
        <ZoneBlock
          key={i}
          zone={zone}
          pagePath={page.path}
          pageMounts={page.mounts}
          allPages={allPages}
          onNavigate={onNavigate}
          editState={editState}
          onEdit={onEdit}
          onSave={onSave}
          onCancel={onCancel}
        />
      ))}

      {(!page.zones || page.zones.length === 0) && (
        <p className="niuu-text-sm niuu-text-text-muted niuu-italic">
          No zones — page will be populated on the next dream cycle.
        </p>
      )}
    </div>
  );
}

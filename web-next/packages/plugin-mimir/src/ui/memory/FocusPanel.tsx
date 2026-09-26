/**
 * FocusPanel — left inspector shown when a node is focused: breadcrumb,
 * eyebrow, title, counts, summary, Key Facts (with `ProofPill`), actions,
 * and the depth-controlled Links list.
 *
 * Links come from a pure BFS over the graph (`domain/graphNeighbors.ts`),
 * not `IPageStore.getRelated` — that port takes no mount and 404s for a
 * page kept on a mount other than the serving instance's own, so it cannot
 * walk links for an arbitrary focused node.
 */
import { relTime, LoadingState, ErrorState, SegmentedFilter } from '@niuulabs/ui';
import { getZoneByKind } from '../../domain/page';
import { neighborsWithinDepth } from '../../domain/graphNeighbors';
import { useEvidence } from './useMemory';
import { ProofPill } from './ProofPill';
import './MemoryLegend.css';
import type { Page } from '../../domain/page';
import type { MimirGraph } from '../../domain/api-types';
import type { FocusDepth } from '../scene/types';

export interface FocusPanelProps {
  /** The currently focused graph node id — opaque, mount-qualified, not a page path. */
  focusId: string;
  page: Page | null;
  isLoading: boolean;
  isError: boolean;
  graph: MimirGraph;
  depth: FocusDepth;
  disputedIds: ReadonlySet<string>;
  onDepthChange: (depth: FocusDepth) => void;
  onClearFocus: () => void;
  onFocusPage: (id: string) => void;
  onReadPage: (page: Page) => void;
  onAskAbout: (page: Page) => void;
}

export function FocusPanel({
  focusId,
  page,
  isLoading,
  isError,
  graph,
  depth,
  disputedIds,
  onDepthChange,
  onClearFocus,
  onFocusPage,
  onReadPage,
  onAskAbout,
}: FocusPanelProps) {
  const evidence = useEvidence(page?.path ?? null);

  if (isLoading) {
    return (
      <section
        className="niuu:w-96 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4"
        aria-label="Page inspector"
      >
        <LoadingState label="loading page…" />
      </section>
    );
  }

  if (isError || !page) {
    return (
      <section
        className="niuu:w-96 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4"
        aria-label="Page inspector"
      >
        <ErrorState
          message="This page could not be loaded."
          action={
            <button type="button" onClick={onClearFocus}>
              All memory
            </button>
          }
        />
      </section>
    );
  }

  const facts = getZoneByKind(page.zones ?? [], 'key-facts')?.items ?? [];
  const evidenceByFact = new Map((evidence.data ?? []).map((row) => [row.fact, row]));
  const links = neighborsWithinDepth(graph, focusId, depth);
  const linkCount = links.length;
  const mount = page.mounts[0] ?? '';

  return (
    <section
      className="niuu:w-96 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:flex niuu:flex-col niuu:gap-3 niuu:overflow-y-auto"
      aria-label="Page inspector"
    >
      <nav aria-label="breadcrumb" className="niuu:text-xs niuu:text-text-muted">
        <button
          type="button"
          onClick={onClearFocus}
          className="niuu:text-brand-300 niuu:hover:underline"
        >
          All memory
        </button>
        {' / '}
        {mount}
      </nav>

      <div>
        <p className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest niuu:m-0">
          {page.type} · {mount}
        </p>
        <h2 className="niuu:text-xl niuu:font-semibold niuu:text-text-primary niuu:m-0 niuu:mt-1">
          {page.title}
        </h2>
        <p className="niuu:text-xs niuu:text-text-muted niuu:m-0 niuu:mt-1">
          {facts.length} facts · {linkCount} links · rewritten {relTime(page.updatedAt)}
        </p>
      </div>

      <p className="niuu:text-sm niuu:text-text-secondary niuu:m-0">{page.summary}</p>

      {facts.length > 0 && (
        <ul className="niuu:flex niuu:flex-col niuu:gap-2 niuu:m-0 niuu:p-0 niuu:list-none">
          {facts.map((fact, i) => {
            const row = evidenceByFact.get(fact);
            return (
              <li
                key={i}
                className="niuu:pb-2 niuu:border-b niuu:border-border-subtle niuu:last:border-b-0"
              >
                <p className="niuu:text-sm niuu:text-text-secondary niuu:m-0">{fact}</p>
                {row && (
                  <div className="niuu:mt-1">
                    <ProofPill evidence={row} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <div className="niuu:flex niuu:gap-2">
        <button
          type="button"
          onClick={() => onReadPage(page)}
          className="niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:rounded-sm niuu:bg-brand niuu:text-bg-primary"
        >
          Read the page
        </button>
        <button
          type="button"
          onClick={() => onAskAbout(page)}
          className="niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:rounded-sm niuu:border niuu:border-border niuu:text-text-secondary"
        >
          Ask about this
        </button>
      </div>

      <div>
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:mb-1.5">
          <h3 className="niuu:text-xs niuu:text-text-muted niuu:uppercase niuu:tracking-widest niuu:m-0">
            Links
          </h3>
          <SegmentedFilter
            aria-label="Link depth"
            value={String(depth)}
            onChange={(v) => onDepthChange(Number(v) as FocusDepth)}
            options={[
              { value: '1', label: '1' },
              { value: '2', label: '2' },
              { value: '3', label: '3' },
            ]}
          />
        </div>
        {links.length === 0 && (
          <p className="niuu:text-xs niuu:text-text-muted niuu:italic niuu:m-0">No linked pages.</p>
        )}
        <ul className="niuu:flex niuu:flex-col niuu:gap-0.5 niuu:m-0 niuu:p-0 niuu:list-none">
          {links.map(({ node, relation }) => {
            const disputed = disputedIds.has(node.id);
            return (
              <li key={node.id}>
                <button
                  type="button"
                  onClick={() => onFocusPage(node.id)}
                  className="niuu:w-full niuu:flex niuu:items-center niuu:justify-between niuu:px-2 niuu:py-1 niuu:rounded-sm niuu:text-sm niuu:hover:bg-bg-tertiary"
                >
                  <span className="niuu:truncate niuu:text-text-secondary">{node.title}</span>
                  {(relation || disputed) && (
                    <span
                      className={
                        disputed
                          ? 'memory-dispute niuu:text-xs niuu:flex-shrink-0 niuu:ml-2'
                          : 'niuu:text-xs niuu:text-text-muted niuu:flex-shrink-0 niuu:ml-2'
                      }
                    >
                      {disputed ? 'disputed' : relation}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

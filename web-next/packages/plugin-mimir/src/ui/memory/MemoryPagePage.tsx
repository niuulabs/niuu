/**
 * MemoryPagePage — one page, read the way it was written.
 *
 * Left: what we believe (the Key Facts), what it links to, and the standing
 * assessment. Right: the neighbourhood and the evidence trail. The trail is
 * append-only — a revision adds a line, it never rewrites one — so the page
 * always shows how a belief got to where it is.
 */

import { useState } from 'react';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { usePluginCtx } from '@niuulabs/plugin-sdk';
import { ArrowLeft, MessageSquare, Pencil } from 'lucide-react';
import { Chip, ErrorState, LoadingState, relTime } from '@niuulabs/ui';
import { getZoneByKind, type Page } from '../../domain/page';
import { evidenceForFact } from '../../domain/evidence';
import { resolveWikilink } from '../../domain/wikilink';
import { useMimirPage, useMimirPages, useMimirPageSources } from '../useMimirPages';
import { MountChip } from '../components/MountChip';
import { PageTypeGlyph } from '../components/PageTypeGlyph';
import { WikilinkPill } from '../components/WikilinkPill';
import { NeighbourhoodGraph } from './NeighbourhoodGraph';
import { ProofPill } from './ProofPill';
import { ReviseFact } from './ReviseFact';
import { useEvidence, useRelated } from './useMemory';

const SECTION =
  'niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-5 niuu:py-4';
const SECTION_TITLE = 'niuu:text-sm niuu:font-medium niuu:text-text-primary';
const SECTION_NOTE = 'niuu:text-[11px] niuu:text-text-muted';
const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-text-primary';

/** A timeline entry written by a belief revision. */
const REVISION_PREFIX = 'belief revised';

const CONFIDENCE_TONE = {
  high: 'default',
  medium: 'muted',
  low: 'critical',
} as const;

interface ReadSearch {
  path?: string;
  mount?: string;
}

function isRevision(note: string): boolean {
  return note.toLowerCase().startsWith(REVISION_PREFIX);
}

function breadcrumb(path: string, mount?: string): string[] {
  const segments = path
    .split('/')
    .filter(Boolean)
    .map((segment) => segment.replace(/\.md$/, ''));
  return mount ? [mount, ...segments] : segments;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function MemoryPagePage() {
  const navigate = useNavigate();
  const ctx = usePluginCtx();
  const search = useSearch({ strict: false }) as ReadSearch;
  const path = search.path ?? null;
  const mount = search.mount;
  const [revisionsOnly, setRevisionsOnly] = useState(false);

  const page = useMimirPage(path, mount);
  const evidence = useEvidence(path);
  const related = useRelated(path);
  const sources = useMimirPageSources(path);
  const allPages = useMimirPages(mount ? { mountName: mount } : undefined);

  if (!path) {
    return (
      <div className="niuu:px-10 niuu:py-6" data-testid="memory-read-page">
        <ErrorState
          title="No page named"
          message="This link carries no page path. Open a page from memory or from an answer."
        />
      </div>
    );
  }

  if (page.isLoading) {
    return (
      <div className="niuu:px-10 niuu:py-6" data-testid="memory-read-page">
        <LoadingState label="Opening page…" />
      </div>
    );
  }

  if (page.isError) {
    return (
      <div className="niuu:px-10 niuu:py-6" data-testid="memory-read-page">
        <ErrorState message={errorMessage(page.error, 'Could not open the page')} />
      </div>
    );
  }

  if (!page.data) {
    return (
      <div className="niuu:px-10 niuu:py-6" data-testid="memory-read-page">
        <ErrorState title="No such page" message={`Nothing is written at ${path}.`} />
      </div>
    );
  }

  const current: Page = page.data;
  const zones = current.zones ?? [];
  const facts = getZoneByKind(zones, 'key-facts')?.items ?? [];
  const relationships = getZoneByKind(zones, 'relationships')?.items ?? [];
  const assessment = getZoneByKind(zones, 'assessment')?.text ?? '';
  const timeline = [...(getZoneByKind(zones, 'timeline')?.items ?? [])].sort((left, right) =>
    right.date.localeCompare(left.date),
  );
  const evidenceRows = evidence.data ?? [];
  const shownTimeline = revisionsOnly
    ? timeline.filter((entry) => isRevision(entry.note))
    : timeline;

  function openPage(nextPath: string) {
    void navigate({ to: '/mimir/read', search: { path: nextPath, mount } });
  }

  function edit() {
    if (mount) ctx.setTweak('activeMount', mount);
    ctx.setTweak('mimir.selectedPagePath', current.path);
    void navigate({ to: '/mimir/pages' });
  }

  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-5 niuu:px-10 niuu:py-6"
      data-testid="memory-read-page"
    >
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Link
          to="/mimir"
          className="niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary"
        >
          <ArrowLeft size={12} aria-hidden="true" />
          Memory
        </Link>
        <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-faint">
          {breadcrumb(current.path, mount).join(' / ')}
        </span>
      </div>

      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-6">
        <div className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-1.5">
          <div className="niuu:flex niuu:items-center niuu:gap-2.5">
            <PageTypeGlyph type={current.type} size={18} />
            <h1 className="niuu:text-xl niuu:font-medium niuu:text-text-primary">
              {current.title}
            </h1>
            <Chip tone="muted">{current.type}</Chip>
            <Chip tone={CONFIDENCE_TONE[current.confidence]}>{current.confidence} confidence</Chip>
          </div>
          <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:text-[11px] niuu:text-text-muted">
            {current.mounts.map((name) => (
              <MountChip key={name} name={name} />
            ))}
            <span>
              {facts.length} {facts.length === 1 ? 'fact' : 'facts'} · {current.sourceIds.length}{' '}
              sources · {timeline.length} evidence entries
            </span>
            {current.updatedAt ? <span>· last change {relTime(current.updatedAt)}</span> : null}
          </div>
        </div>
        <div className="niuu:flex niuu:shrink-0 niuu:items-center niuu:gap-2">
          <Link
            to="/mimir/ask"
            search={{ q: current.title, mount }}
            className={BUTTON}
            data-testid="memory-ask-about"
          >
            <MessageSquare size={13} aria-hidden="true" />
            Ask about this
          </Link>
          <button type="button" className={BUTTON} onClick={edit} data-testid="memory-edit-page">
            <Pencil size={13} aria-hidden="true" />
            Edit
          </button>
        </div>
      </header>

      <div className="niuu:grid niuu:grid-cols-[1.3fr_1fr] niuu:items-start niuu:gap-6">
        <div className="niuu:flex niuu:flex-col niuu:gap-5">
          {facts.length > 0 ? (
            <section className={SECTION} data-testid="memory-facts-zone">
              <div className="niuu:flex niuu:items-baseline niuu:gap-3">
                <h2 className={SECTION_TITLE}>What we believe</h2>
                <span className={SECTION_NOTE}>written by a resident, with its proof</span>
              </div>
              {facts.map((fact, index) => {
                const row = evidenceForFact(evidenceRows, fact);
                return (
                  <div
                    key={fact}
                    className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0"
                  >
                    <div className="niuu:flex niuu:items-start niuu:gap-2.5">
                      <span
                        className="niuu:mt-1.5 niuu:size-1.5 niuu:shrink-0 niuu:rounded-full niuu:bg-brand-300"
                        aria-hidden="true"
                      />
                      <span className="niuu:flex-1 niuu:text-xs niuu:leading-relaxed niuu:text-text-primary">
                        {fact}
                      </span>
                      {row ? <ProofPill evidence={row} /> : null}
                      <ReviseFact path={current.path} fact={fact} index={index} label="" />
                    </div>
                    {row?.latestSupport ? (
                      <span className="niuu:pl-4 niuu:text-[11px] niuu:text-text-faint">
                        newest proof {row.latestSupport} · {row.sourceProofCount} from raw sources
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </section>
          ) : null}

          {relationships.length > 0 ? (
            <section className={SECTION} data-testid="memory-relationships">
              <h2 className={SECTION_TITLE}>Relationships</h2>
              <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
                {relationships.map((item) => {
                  const target = resolveWikilink(item.slug, allPages.data ?? []);
                  return (
                    <span key={item.slug} className="niuu:flex niuu:items-center niuu:gap-1.5">
                      <WikilinkPill
                        slug={item.slug}
                        broken={target.broken}
                        onNavigate={() => target.page && openPage(target.page.path)}
                      />
                      {item.note ? (
                        <span className="niuu:text-[11px] niuu:text-text-muted">· {item.note}</span>
                      ) : null}
                    </span>
                  );
                })}
              </div>
            </section>
          ) : null}

          {assessment ? (
            <section className={SECTION} data-testid="memory-assessment">
              <h2 className={SECTION_TITLE}>Assessment</h2>
              <p className="niuu:m-0 niuu:text-xs niuu:leading-relaxed niuu:text-text-secondary">
                {assessment}
              </p>
            </section>
          ) : null}
        </div>

        <div className="niuu:flex niuu:flex-col niuu:gap-5">
          <section className={SECTION} data-testid="memory-related">
            <div className="niuu:flex niuu:items-baseline niuu:gap-3">
              <h2 className={SECTION_TITLE}>Around this page</h2>
              <Link to="/mimir/graph" className="niuu:ml-auto niuu:text-[11px] niuu:text-brand-300">
                open the graph ›
              </Link>
            </div>
            {(related.data ?? []).length === 0 ? (
              <p className="niuu:text-xs niuu:text-text-faint">Nothing links here yet.</p>
            ) : (
              <NeighbourhoodGraph
                title={current.title}
                related={related.data ?? []}
                onOpen={openPage}
              />
            )}
          </section>

          <section className={SECTION} data-testid="memory-evidence">
            <div className="niuu:flex niuu:items-baseline niuu:gap-3">
              <h2 className={SECTION_TITLE}>Evidence</h2>
              <span className={SECTION_NOTE}>append-only · never edited</span>
            </div>

            {timeline.length === 0 ? (
              <p className="niuu:text-xs niuu:text-text-faint">
                No evidence has been recorded for this page yet.
              </p>
            ) : null}

            {shownTimeline.map((entry) => (
              <div
                key={`${entry.date}-${entry.note}`}
                className={[
                  'niuu:flex niuu:items-start niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2 niuu:last:border-b-0',
                  isRevision(entry.note) ? 'niuu:bg-status-amber/5' : '',
                ].join(' ')}
                data-revision={isRevision(entry.note) ? 'true' : undefined}
              >
                <span className="niuu:w-[76px] niuu:shrink-0 niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
                  {entry.date}
                </span>
                <span
                  className={`niuu:mt-1.5 niuu:size-1.5 niuu:shrink-0 niuu:rounded-full ${
                    isRevision(entry.note) ? 'niuu:bg-status-amber' : 'niuu:bg-brand-300'
                  }`}
                  aria-hidden="true"
                />
                <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
                  <span className="niuu:text-xs niuu:text-text-secondary">{entry.note}</span>
                  {entry.source ? (
                    <span className="niuu:truncate niuu:font-mono niuu:text-[10px] niuu:text-text-faint">
                      Source: {entry.source}
                    </span>
                  ) : null}
                </div>
              </div>
            ))}

            {timeline.some((entry) => isRevision(entry.note)) ? (
              <button
                type="button"
                className="niuu:self-start niuu:text-[11px] niuu:text-brand-300"
                onClick={() => setRevisionsOnly(!revisionsOnly)}
                aria-pressed={revisionsOnly}
                data-testid="memory-revisions-filter"
              >
                {revisionsOnly ? 'show the whole trail' : 'what we used to believe'}
              </button>
            ) : null}

            {(sources.data ?? []).length > 0 ? (
              <div className="niuu:flex niuu:flex-col niuu:gap-1 niuu:border-t niuu:border-border-subtle niuu:pt-3">
                <span className={SECTION_NOTE}>compiled from</span>
                {(sources.data ?? []).map((source) => (
                  <span
                    key={source.id}
                    className="niuu:truncate niuu:text-[11px] niuu:text-text-secondary"
                    title={source.title}
                  >
                    {source.title}
                  </span>
                ))}
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}

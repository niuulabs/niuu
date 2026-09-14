/**
 * MemoryHomePage — Simple mode's answer to "what does Niuu remember?".
 *
 * Three things, in order of how often they are wanted: ask a question, see
 * which realm keeps what, and read what the residents wrote lately. The last
 * block lists the beliefs whose proof is going cold, because those are the
 * only ones that need a human.
 */

import { useMemo } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import type { Mount, MountRole } from '@niuulabs/domain';
import { Boxes, Database, HardDrive, Library, Users, type LucideIcon } from 'lucide-react';
import { Chip, ErrorState, LiveBadge, LoadingState, StateDot, relTime } from '@niuulabs/ui';
import { isWeakening } from '../../domain/evidence';
import {
  mergeMemoryFeed,
  pagesInFeed,
  type MemoryEvent,
  type MemoryEventKind,
} from '../../domain/memoryFeed';
import { useMimirMounts } from '../useMimirMounts';
import { useMimirRecentWrites } from '../useMimirSources';
import { useActivityLog } from '../../application/useActivityLog';
import { useDoctor } from '../../application/useDoctor';
import { MOUNT_DOT_STATE } from '../mimir.constants';
import { AskBox } from './AskBox';
import { ProofPill } from './ProofPill';
import { ReviseFact } from './ReviseFact';
import { useEvidenceForPaths } from './useMemory';

const FEED_LIMIT = 20;
/** How many recently-written pages to check for cooling proof. */
const BELIEF_PAGE_LIMIT = 8;

const LEDE =
  'Residents write what they learn here as facts with evidence. Ask a question, or browse by the realm that keeps it.';

const SECTION =
  'niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-5 niuu:py-4';
const SECTION_TITLE = 'niuu:text-sm niuu:font-medium niuu:text-text-primary';
const SECTION_NOTE = 'niuu:text-[11px] niuu:text-text-muted';
const MONO = 'niuu:font-mono niuu:text-[11px] niuu:text-text-muted';

const ROLE_LINE: Record<MountRole, string> = {
  local: 'kept on this machine',
  shared: 'shared with everyone',
  domain: 'kept for one domain',
};

const ROLE_ICON: Record<MountRole, LucideIcon> = {
  local: HardDrive,
  shared: Users,
  domain: Library,
};

const KIND_DOT: Record<MemoryEventKind, string> = {
  'fact-added': 'niuu:bg-brand-300',
  'belief-revised': 'niuu:bg-status-amber',
  'page-compiled': 'niuu:bg-brand',
  'source-ingested': 'niuu:bg-status-cyan',
  tidied: 'niuu:bg-text-muted',
  dreamed: 'niuu:bg-status-purple',
  asked: 'niuu:bg-text-faint',
};

const REALM_MOUNT_PREFIX = 'realm-';
const TIMESTAMP_HOUR_START = 11;
const TIMESTAMP_HOUR_END = 16;

/** The realm a mount keeps, when the mount is a realm mount. */
function realmSlug(mount: string): string | null {
  return mount.startsWith(REALM_MOUNT_PREFIX)
    ? mount.slice(REALM_MOUNT_PREFIX.length) || null
    : null;
}

function formatTime(iso: string): string {
  return iso.slice(TIMESTAMP_HOUR_START, TIMESTAMP_HOUR_END);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// Mount card — one realm's memory
// ---------------------------------------------------------------------------

function MountCard({ mount }: { mount: Mount }) {
  const slug = realmSlug(mount.name);
  const Icon = slug ? Boxes : ROLE_ICON[mount.role];

  return (
    <article
      className="niuu:flex niuu:flex-col niuu:gap-2.5 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-4 niuu:py-3.5"
      data-testid={`memory-mount-${mount.name}`}
    >
      <div className="niuu:flex niuu:items-start niuu:gap-2.5">
        <span className="niuu:flex niuu:size-7 niuu:items-center niuu:justify-center niuu:rounded-lg niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand-300">
          <Icon size={15} aria-hidden="true" />
        </span>
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
          {slug ? (
            <Link
              to={`/realms/${slug}`}
              className="niuu:truncate niuu:text-sm niuu:font-medium niuu:text-brand-300"
            >
              {slug}
            </Link>
          ) : (
            <span className="niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
              {mount.name}
            </span>
          )}
          <span className="niuu:truncate niuu:text-[11px] niuu:text-text-muted">
            {mount.desc || ROLE_LINE[mount.role]}
          </span>
        </div>
        <StateDot state={MOUNT_DOT_STATE[mount.status]} title={mount.status} />
      </div>

      <div className={MONO}>
        <span className="niuu:text-text-primary">{mount.pages}</span> pages ·{' '}
        <span className="niuu:text-text-primary">{mount.sources}</span> sources
      </div>
      <div className="niuu:text-[11px] niuu:text-text-faint">
        {mount.lastWrite ? `last written ${relTime(mount.lastWrite)}` : 'nothing written yet'}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Feed row — one thing a resident did
// ---------------------------------------------------------------------------

function FeedRow({ event }: { event: MemoryEvent }) {
  return (
    <div className="niuu:flex niuu:items-start niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0">
      <span className={`${MONO} niuu:w-11 niuu:shrink-0 niuu:pt-0.5`}>
        {formatTime(event.timestamp)}
      </span>
      <span
        className={`niuu:mt-1.5 niuu:size-1.5 niuu:shrink-0 niuu:rounded-full ${KIND_DOT[event.kind]}`}
        aria-hidden="true"
      />
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:text-xs niuu:text-text-secondary">{event.message}</span>
        <span className="niuu:truncate niuu:text-[11px] niuu:text-text-faint">
          {event.label} · {event.who} · {event.mount}
          {event.page ? ' · ' : ''}
          {event.page ? (
            <Link
              to="/mimir/read"
              search={{ path: event.page, mount: event.mount }}
              className="niuu:font-mono niuu:text-brand-300"
            >
              {event.page}
            </Link>
          ) : null}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function MemoryHomePage() {
  const navigate = useNavigate();
  const mounts = useMimirMounts();
  const writes = useMimirRecentWrites(FEED_LIMIT);
  const activity = useActivityLog(FEED_LIMIT);
  const doctor = useDoctor();

  const feed = useMemo(
    () => mergeMemoryFeed(writes.data ?? [], activity.data ?? [], FEED_LIMIT),
    [writes.data, activity.data],
  );
  const feedPages = useMemo(() => pagesInFeed(feed, BELIEF_PAGE_LIMIT), [feed]);
  const { byPath } = useEvidenceForPaths(feedPages);

  const beliefs = useMemo(
    () =>
      feedPages.flatMap((path) =>
        (byPath.get(path) ?? [])
          .filter(isWeakening)
          .map((evidence) => ({ path, evidence, mount: mountForPage(feed, path) })),
      ),
    [feedPages, byPath, feed],
  );

  const totals = (mounts.data ?? []).reduce(
    (acc, mount) => ({ pages: acc.pages + mount.pages, sources: acc.sources + mount.sources }),
    { pages: 0, sources: 0 },
  );

  function ask(question: string) {
    void navigate({ to: '/mimir/ask', search: { q: question } });
  }

  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-6 niuu:px-10 niuu:py-6"
      data-testid="memory-home"
    >
      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-8">
        <div className="niuu:flex niuu:max-w-2xl niuu:flex-col niuu:gap-1.5">
          <h1 className="niuu:text-xl niuu:font-medium niuu:text-text-primary">
            What does Niuu remember?
          </h1>
          <p className="niuu:text-xs niuu:leading-relaxed niuu:text-text-muted">{LEDE}</p>
        </div>
        <div className="niuu:flex niuu:shrink-0 niuu:flex-wrap niuu:justify-end niuu:gap-1.5">
          <Chip tone="muted">
            <Database size={11} aria-hidden="true" /> {totals.pages.toLocaleString()} pages
          </Chip>
          <Chip tone="muted">{totals.sources.toLocaleString()} sources</Chip>
          {doctor.report ? (
            <Chip tone={doctor.report.worst === 'pass' ? 'default' : 'critical'}>
              health {doctor.report.score}
            </Chip>
          ) : null}
          <Chip tone="brand">hybrid search</Chip>
        </div>
      </header>

      <AskBox placeholder="Ask memory a question…" onAsk={ask} />

      <div className="niuu:grid niuu:grid-cols-[1.35fr_1fr] niuu:gap-6 niuu:items-start">
        <div className="niuu:flex niuu:flex-col niuu:gap-6">
          <section className={SECTION} aria-label="memory by realm">
            <div className="niuu:flex niuu:items-baseline niuu:gap-3">
              <h2 className={SECTION_TITLE}>Memory by realm</h2>
              <span className={SECTION_NOTE}>one store per realm, plus the shared one</span>
            </div>

            {mounts.isLoading ? <LoadingState label="Loading memory stores…" /> : null}
            {mounts.isError ? (
              <ErrorState message={errorMessage(mounts.error, 'Could not load memory stores')} />
            ) : null}
            {!mounts.isLoading && !mounts.isError && (mounts.data ?? []).length === 0 ? (
              <p className="niuu:text-xs niuu:text-text-faint">No memory stores are mounted.</p>
            ) : null}

            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3">
              {(mounts.data ?? []).map((mount) => (
                <MountCard key={mount.name} mount={mount} />
              ))}
            </div>
          </section>

          <section className={SECTION} data-testid="memory-beliefs">
            <div className="niuu:flex niuu:items-baseline niuu:gap-3">
              <h2 className={SECTION_TITLE}>Beliefs worth a look</h2>
              <span className={SECTION_NOTE}>
                facts on recently-written pages whose proof is going cold
              </span>
            </div>

            {beliefs.length === 0 ? (
              <p className="niuu:text-xs niuu:text-text-faint">
                Nothing is going cold on the pages written lately.
              </p>
            ) : null}

            {beliefs.map((belief, index) => (
              <div
                key={`${belief.path}-${belief.evidence.fact}`}
                className="niuu:flex niuu:flex-col niuu:gap-2 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0"
              >
                <div className="niuu:flex niuu:items-start niuu:gap-3">
                  <span className="niuu:flex-1 niuu:text-xs niuu:text-text-primary">
                    {belief.evidence.fact}
                  </span>
                  <ProofPill evidence={belief.evidence} />
                </div>
                <div className="niuu:flex niuu:items-center niuu:gap-3">
                  <Link
                    to="/mimir/read"
                    search={{ path: belief.path, mount: belief.mount }}
                    className="niuu:font-mono niuu:text-[11px] niuu:text-brand-300"
                  >
                    {belief.path}
                  </Link>
                  <ReviseFact path={belief.path} fact={belief.evidence.fact} index={index} />
                </div>
              </div>
            ))}
          </section>
        </div>

        <section className={SECTION} data-testid="memory-feed">
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <h2 className={SECTION_TITLE}>What it learned lately</h2>
            <LiveBadge label="LIVE" />
          </div>

          {writes.isError ? (
            <ErrorState message={errorMessage(writes.error, 'Could not load recent writes')} />
          ) : null}
          {!writes.isError && feed.length === 0 ? (
            <p className="niuu:text-xs niuu:text-text-faint">Nothing written yet.</p>
          ) : null}

          <div role="log" aria-label="what it learned lately">
            {feed.map((event) => (
              <FeedRow key={event.id} event={event} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

/** The mount the feed attributes a page to, so page links keep their store. */
function mountForPage(feed: MemoryEvent[], path: string): string | undefined {
  return feed.find((event) => event.page === path)?.mount;
}

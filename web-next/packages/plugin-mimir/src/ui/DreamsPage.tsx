import { useState } from 'react';
import { StateDot, Chip } from '@niuulabs/ui';
import { useDreams } from '../application/useDreams';
import { useActivityLog } from '../application/useActivityLog';
import type { DreamCycle, ActivityEvent, ActivityEventKind } from '../domain/lint';
import { formatDuration, formatTimestamp } from './format';

// ── Constants ────────────────────────────────────────────────────────────────

const STATUS_ROW = 'niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary';

const LOG_ROW =
  'niuu-grid niuu-grid-cols-[90px_60px_70px_1fr] niuu-gap-3 niuu-py-[3px] ' +
  'niuu-border-0 niuu-border-b niuu-border-solid ' +
  'niuu-border-[color-mix(in_srgb,var(--color-border-subtle)_40%,transparent)] ' +
  'niuu-items-center hover:niuu-bg-bg-tertiary';

const KIND_FILTERS: Array<{ value: ActivityEventKind | 'all'; label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'write', label: 'write' },
  { value: 'ingest', label: 'ingest' },
  { value: 'lint', label: 'lint' },
  { value: 'dream', label: 'dream' },
];

const KIND_COLOR: Record<ActivityEventKind, string> = {
  write: 'niuu-text-brand-300',
  ingest: 'niuu-text-brand-500',
  lint: 'niuu-text-brand-200',
  dream: 'niuu-text-brand',
  query: 'niuu-text-text-muted',
};

const FILTER_BTN_BASE =
  'niuu-bg-transparent niuu-border niuu-border-solid niuu-rounded-sm niuu-font-mono niuu-text-[9px] ' +
  'niuu-uppercase niuu-tracking-wider niuu-py-[3px] niuu-px-2 niuu-cursor-pointer niuu-transition-colors';

const FILTER_BTN_ACTIVE =
  `${FILTER_BTN_BASE} niuu-border-brand niuu-text-brand ` +
  'niuu-bg-[color-mix(in_srgb,var(--color-brand)_10%,transparent)]';

const FILTER_BTN_IDLE =
  `${FILTER_BTN_BASE} niuu-border-border-subtle niuu-text-text-muted ` +
  'hover:niuu-border-border hover:niuu-text-text-secondary';

// ── Dream cycle row ──────────────────────────────────────────────────────────

interface DreamRowProps {
  cycle: DreamCycle;
}

function DreamRow({ cycle }: DreamRowProps) {
  const totalActivity = cycle.pagesUpdated + cycle.entitiesCreated + cycle.lintFixes;

  return (
    <li
      className="niuu-p-4 niuu-border niuu-border-solid niuu-border-border-subtle niuu-rounded-md niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-gap-3"
      data-testid="dream-cycle"
    >
      <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-flex-wrap">
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-secondary">
          {formatTimestamp(cycle.timestamp, 'medium')}
        </span>
        <Chip tone="muted">{cycle.ravn}</Chip>
        <span className="niuu-ml-auto niuu-font-mono niuu-text-xs niuu-text-text-muted">
          {formatDuration(cycle.durationMs)}
        </span>
      </div>

      <div className="niuu-flex niuu-gap-1 niuu-flex-wrap">
        {cycle.mounts.map((m) => (
          <Chip key={m} tone="muted">
            {m}
          </Chip>
        ))}
      </div>

      <div className="niuu-flex niuu-gap-4 niuu-flex-wrap niuu-text-sm">
        <span
          className={cycle.pagesUpdated > 0 ? 'niuu-text-text-secondary' : 'niuu-text-text-muted'}
          data-testid="dream-pages"
        >
          <strong className="niuu-text-text-primary niuu-font-semibold">
            {cycle.pagesUpdated}
          </strong>{' '}
          pages updated
        </span>
        <span
          className={
            cycle.entitiesCreated > 0 ? 'niuu-text-text-secondary' : 'niuu-text-text-muted'
          }
          data-testid="dream-entities"
        >
          <strong className="niuu-text-text-primary niuu-font-semibold">
            {cycle.entitiesCreated}
          </strong>{' '}
          entities created
        </span>
        <span
          className={cycle.lintFixes > 0 ? 'niuu-text-text-secondary' : 'niuu-text-text-muted'}
          data-testid="dream-fixes"
        >
          <strong className="niuu-text-text-primary niuu-font-semibold">{cycle.lintFixes}</strong>{' '}
          lint fixes
        </span>
        {totalActivity === 0 && (
          <span className="niuu-text-text-muted niuu-italic">no changes</span>
        )}
      </div>
    </li>
  );
}

// ── Activity log row ─────────────────────────────────────────────────────────

interface ActivityRowProps {
  event: ActivityEvent;
}

function ActivityRow({ event }: ActivityRowProps) {
  return (
    <div className={LOG_ROW} data-testid="activity-row">
      <span className="niuu-text-text-faint niuu-truncate">
        {formatTimestamp(event.timestamp, 'short')}
      </span>
      <span
        className={`niuu-text-[9px] niuu-uppercase niuu-tracking-wider niuu-font-medium ${KIND_COLOR[event.kind]}`}
        data-testid="activity-kind"
      >
        {event.kind}
      </span>
      <span className="niuu-text-brand-300 niuu-truncate">{event.mount}</span>
      <span className="niuu-truncate">
        <span className="niuu-text-text-primary">{event.ravn}</span>
        <span className="niuu-text-text-muted niuu-mx-1">·</span>
        <span className="niuu-text-text-secondary">{event.message}</span>
      </span>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function DreamsPage() {
  const { data: cycles, isLoading: cyclesLoading, isError: cyclesError, error } = useDreams();
  const {
    data: events,
    isLoading: eventsLoading,
    isError: eventsError,
    error: eventsErr,
  } = useActivityLog();

  const [kindFilter, setKindFilter] = useState<ActivityEventKind | 'all'>('all');

  const filteredEvents =
    kindFilter === 'all' ? events : events?.filter((e) => e.kind === kindFilter);

  return (
    <div className="niuu-p-6 niuu-max-w-[960px]">
      <h2 className="niuu-text-xl niuu-font-semibold niuu-m-0 niuu-mb-2">Dreams</h2>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-m-0 niuu-mb-6">
        Dream-cycle history — idle-time synthesis passes that update pages, create entities, and
        apply lint fixes.
      </p>

      {cyclesLoading && (
        <div className={STATUS_ROW}>
          <StateDot state="processing" pulse />
          <span>loading dream cycles…</span>
        </div>
      )}

      {cyclesError && (
        <div className={STATUS_ROW}>
          <StateDot state="failed" />
          <span>{error instanceof Error ? error.message : 'dream cycles load failed'}</span>
        </div>
      )}

      {!cyclesLoading && !cyclesError && cycles?.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted">No dream cycles recorded yet.</p>
      )}

      {cycles && cycles.length > 0 && (
        <ul
          className="niuu-list-none niuu-p-0 niuu-m-0 niuu-grid niuu-gap-3 niuu-mb-8"
          aria-label="Dream cycle history"
        >
          {cycles.map((cycle) => (
            <DreamRow key={cycle.id} cycle={cycle} />
          ))}
        </ul>
      )}

      {/* Activity log section */}
      <section
        className="niuu-border-t niuu-border-solid niuu-border-border niuu-pt-6 niuu-px-4"
        aria-label="Activity log"
      >
        <div className="niuu-flex niuu-items-baseline niuu-gap-3 niuu-mb-3">
          <h3 className="niuu-text-base niuu-font-semibold niuu-m-0">Activity log</h3>
          {events && (
            <span className="niuu-text-xs niuu-text-text-muted">
              append-only · {events.length} entries · newest first
            </span>
          )}
        </div>

        {/* Kind filter buttons */}
        <div
          className="niuu-flex niuu-gap-1 niuu-mb-3 niuu-flex-wrap"
          role="group"
          aria-label="Filter by kind"
        >
          {KIND_FILTERS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setKindFilter(value)}
              aria-pressed={kindFilter === value}
              className={kindFilter === value ? FILTER_BTN_ACTIVE : FILTER_BTN_IDLE}
              data-testid={`kind-filter-${value}`}
            >
              {label}
            </button>
          ))}
        </div>

        {eventsLoading && (
          <div className={STATUS_ROW}>
            <StateDot state="processing" pulse />
            <span>loading activity log…</span>
          </div>
        )}

        {eventsError && (
          <div className={STATUS_ROW}>
            <StateDot state="failed" />
            <span>
              {eventsErr instanceof Error ? eventsErr.message : 'activity log load failed'}
            </span>
          </div>
        )}

        {!eventsLoading && !eventsError && filteredEvents?.length === 0 && (
          <p className="niuu-text-sm niuu-text-text-muted" data-testid="activity-empty">
            No activity events{kindFilter !== 'all' ? ` for kind "${kindFilter}"` : ''}.
          </p>
        )}

        {filteredEvents && filteredEvents.length > 0 && (
          <div
            className="niuu-font-mono niuu-text-[11px] niuu-text-text-secondary niuu-leading-[1.7]"
            data-testid="activity-log"
          >
            {/* Header row */}
            <div className="niuu-grid niuu-grid-cols-[90px_60px_70px_1fr] niuu-gap-3 niuu-py-[2px] niuu-text-text-muted niuu-uppercase niuu-tracking-[0.07em] niuu-text-[9px] niuu-font-medium">
              <span>time</span>
              <span>kind</span>
              <span>mount</span>
              <span>ravn · message</span>
            </div>
            {filteredEvents.map((event) => (
              <ActivityRow key={event.id} event={event} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

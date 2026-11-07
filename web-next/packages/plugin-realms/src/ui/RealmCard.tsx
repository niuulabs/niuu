import { Link } from '@tanstack/react-router';
import { Chip } from '@niuulabs/ui';
import type { RealmSummary } from '../domain/governance';
import type { RealmBinding } from '../domain/realm';
import { TemplateIcon, templateLabel } from './icons';

export interface RealmCardProps {
  realm: RealmSummary;
  binding?: RealmBinding | null;
  boardName?: string | null;
  waitingTickets?: number;
  runningSessions: number;
}

function Stat({ value, label, attention }: { value: number; label: string; attention?: boolean }) {
  return (
    <div className="niuu:flex niuu:min-w-14 niuu:flex-col">
      <span
        className={`niuu:font-mono niuu:text-lg niuu:font-semibold niuu:leading-tight ${attention ? 'niuu:text-status-amber' : 'niuu:text-text-primary'}`}
      >
        {value}
      </span>
      <span className="niuu:text-[11px] niuu:text-text-muted">{label}</span>
    </div>
  );
}

/** One realm on the home screen: what it is bound to, what is moving, what needs you. */
export function RealmCard({
  realm,
  binding = null,
  boardName = null,
  waitingTickets = 0,
  runningSessions,
}: RealmCardProps) {
  return (
    <article
      className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3.5"
      data-testid={`realm-card-${realm.slug}`}
    >
      <div className="niuu:flex niuu:items-start niuu:gap-2.5">
        <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
          <TemplateIcon templateId={binding?.template} size={14} />
        </span>
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
          <Link
            to="/realms/$slug"
            params={{ slug: realm.slug }}
            className="niuu:truncate niuu:text-[15px] niuu:font-medium niuu:text-text-primary"
          >
            {realm.name}
          </Link>
          <span className="niuu:truncate niuu:text-[11px] niuu:text-text-muted">
            {templateLabel(binding?.template)} · {binding?.repo || realm.slug}
          </span>
        </div>
      </div>
      <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
        {binding?.trackerBoard ? (
          <Chip tone="muted">board {boardName ?? binding.trackerBoard}</Chip>
        ) : null}
        {binding?.bugBoard ? <Chip tone="muted">bugs {binding.bugBoard}</Chip> : null}
        {!binding ? (
          <span className="niuu:text-[11px] niuu:text-text-faint">nothing bound yet</span>
        ) : null}
      </div>
      <div className="niuu:flex niuu:gap-4 niuu:border-t niuu:border-border-subtle niuu:pt-2.5">
        <Stat value={runningSessions} label="in sessions" />
        <Stat value={waitingTickets} label="needs you" attention={waitingTickets > 0} />
      </div>
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className="niuu:text-[11px] niuu:text-text-faint">{realm.autonomy_profile}</span>
        <Link
          to="/realms/$slug"
          params={{ slug: realm.slug }}
          search={(waitingTickets > 0 ? { tab: 'board' } : {}) as never}
          className={
            waitingTickets > 0
              ? 'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-brand-300'
              : 'niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary'
          }
        >
          {waitingTickets > 0 ? `Review ${waitingTickets}` : 'Open ›'}
        </Link>
      </div>
    </article>
  );
}

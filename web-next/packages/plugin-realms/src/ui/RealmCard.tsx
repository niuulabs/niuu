import { Link } from '@tanstack/react-router';
import type { Ravn } from '@niuulabs/plugin-ravn';
import type { RealmSummary, ReviewItem, ValkyrieResident } from '@niuulabs/plugin-valkyrie';
import { wakefulnessCopy } from '@niuulabs/plugin-valkyrie';
import { Chip, StateDot, type DotState } from '@niuulabs/ui';

export interface RealmCardProps {
  realm: RealmSummary;
  resident: ValkyrieResident | null;
  ravn: Ravn | null;
  pendingReviews: ReviewItem[];
  runningSessions: number;
}

const WAKEFULNESS_DOT: Record<string, DotState> = {
  wakeful: 'healthy',
  watching: 'observing',
  dreaming: 'processing',
  sleeping: 'idle',
};

function Stat({ value, label, attention }: { value: number; label: string; attention?: boolean }) {
  return (
    <div className="niuu:flex niuu:min-w-16 niuu:flex-col">
      <span
        className={`niuu:font-mono niuu:text-lg niuu:font-semibold ${attention ? 'niuu:text-state-warn' : 'niuu:text-text-primary'}`}
      >
        {value}
      </span>
      <span className="niuu:text-[11px] niuu:text-text-muted">{label}</span>
    </div>
  );
}

/** One realm on the home screen: who keeps it, what is moving, what needs you. */
export function RealmCard({ realm, resident, ravn, pendingReviews, runningSessions }: RealmCardProps) {
  const wakefulness = resident?.wakefulness ?? null;
  const dot: DotState = wakefulness ? (WAKEFULNESS_DOT[wakefulness] ?? 'unknown') : ravn ? 'healthy' : 'unknown';
  const stateLabel = wakefulness
    ? wakefulnessCopy(wakefulness).label
    : ravn
      ? ravn.status
      : 'no resident yet';

  return (
    <article
      className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4"
      data-testid={`realm-card-${realm.slug}`}
    >
      <div className="niuu:flex niuu:items-start niuu:gap-3">
        <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:font-mono niuu:text-xs niuu:font-bold niuu:text-brand">
          {realm.name.charAt(0).toUpperCase()}
        </span>
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
          <Link
            to="/realms/$slug"
            params={{ slug: realm.slug }}
            className="niuu:truncate niuu:text-[15px] niuu:font-medium niuu:text-text-primary"
          >
            {realm.name}
          </Link>
          <span className="niuu:truncate niuu:font-mono niuu:text-[11px] niuu:text-text-muted">
            {realm.slug}
            {realm.instance_id ? ` · ${realm.instance_id}` : ''}
          </span>
        </div>
        <Chip tone={wakefulness === 'wakeful' ? 'brand' : 'muted'}>
          <StateDot state={dot} pulse={wakefulness === 'wakeful'} size={6} />
          {stateLabel}
        </Chip>
      </div>
      <div className="niuu:flex niuu:gap-5 niuu:border-t niuu:border-border-subtle niuu:pt-3">
        <Stat value={runningSessions} label="in sessions" />
        <Stat value={pendingReviews.length} label="needs you" attention={pendingReviews.length > 0} />
        {resident ? <Stat value={resident.toolCount} label="tools" /> : null}
      </div>
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className="niuu:text-[11px] niuu:text-text-faint">
          {resident?.lastActionAt ? `last acted ${resident.lastActionAt.slice(0, 16).replace('T', ' ')}` : realm.autonomy_profile}
        </span>
        <Link
          to="/realms/$slug"
          params={{ slug: realm.slug }}
          className="niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-brand-300"
        >
          {pendingReviews.length > 0 ? `Review ${pendingReviews.length}` : 'Open'}
        </Link>
      </div>
    </article>
  );
}

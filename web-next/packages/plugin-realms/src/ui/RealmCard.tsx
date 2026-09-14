import { Link } from '@tanstack/react-router';
import type { Ravn } from '@niuulabs/plugin-ravn';
import type {
  EnvironmentSummary,
  RealmSummary,
  ReviewItem,
  ValkyrieResident,
} from '@niuulabs/plugin-valkyrie';
import { wakefulnessCopy } from '@niuulabs/plugin-valkyrie';
import { Chip, StateDot, type DotState } from '@niuulabs/ui';
import type { RealmBinding } from '../domain/realm';
import { TemplateIcon, agoLabel, templateLabel } from './icons';

export interface RealmCardProps {
  realm: RealmSummary;
  resident: ValkyrieResident | null;
  ravn: Ravn | null;
  environment?: EnvironmentSummary | null;
  binding?: RealmBinding | null;
  pendingReviews: ReviewItem[];
  runningSessions: number;
}

/** Red is for failures; "watch" and "unknown" are not failures. */
const FAILING_HEALTH = new Set(['degraded', 'critical', 'unhealthy', 'failed', 'down']);

const WAKEFULNESS_DOT: Record<string, DotState> = {
  wakeful: 'healthy',
  watching: 'observing',
  dreaming: 'processing',
  sleeping: 'unknown',
};

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

/** One realm on the home screen: who keeps it, what is moving, what needs you. */
export function RealmCard({
  realm,
  resident,
  ravn,
  environment = null,
  binding = null,
  pendingReviews,
  runningSessions,
}: RealmCardProps) {
  const wakefulness = resident?.wakefulness ?? null;
  const dot: DotState = wakefulness
    ? (WAKEFULNESS_DOT[wakefulness] ?? 'unknown')
    : ravn
      ? 'healthy'
      : 'unknown';
  const stateLabel = wakefulness
    ? wakefulnessCopy(wakefulness).label
    : ravn
      ? ravn.status
      : 'no resident yet';
  const unresolved = environment?.unresolvedSignalCount ?? 0;
  const acted = agoLabel(resident?.lastActionAt);

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
        <Chip tone={wakefulness === 'wakeful' ? 'brand' : 'muted'}>
          <StateDot state={dot} pulse={wakefulness === 'wakeful'} size={6} />
          {stateLabel}
        </Chip>
      </div>
      <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
        {binding?.trackerBoard ? <Chip tone="muted">board {binding.trackerBoard}</Chip> : null}
        {binding?.bugBoard ? <Chip tone="muted">bugs {binding.bugBoard}</Chip> : null}
        {environment ? (
          <Chip tone={FAILING_HEALTH.has(environment.health) ? 'critical' : 'muted'}>
            {unresolved > 0 ? `${unresolved} open signals` : environment.health}
          </Chip>
        ) : null}
        {!binding && !environment ? (
          <span className="niuu:text-[11px] niuu:text-text-faint">nothing bound yet</span>
        ) : null}
      </div>
      <div className="niuu:flex niuu:gap-4 niuu:border-t niuu:border-border-subtle niuu:pt-2.5">
        <Stat value={runningSessions} label="in sessions" />
        <Stat
          value={pendingReviews.length}
          label="needs you"
          attention={pendingReviews.length > 0}
        />
        {resident ? <Stat value={resident.toolCount} label="tools" /> : null}
      </div>
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className="niuu:text-[11px] niuu:text-text-faint">
          {acted ? `last acted ${acted}` : realm.autonomy_profile}
        </span>
        <Link
          to="/realms/$slug"
          params={{ slug: realm.slug }}
          className={
            pendingReviews.length > 0
              ? 'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:text-brand-300'
              : 'niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary'
          }
        >
          {pendingReviews.length > 0 ? `Review ${pendingReviews.length}` : 'Open ›'}
        </Link>
      </div>
    </article>
  );
}

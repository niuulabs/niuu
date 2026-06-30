import { useMemo } from 'react';
import type { ReviewStatus, ValkyrieEventTelemetry } from '../domain';
import { useValkyrieDashboard } from '../application/useValkyrieDashboard';
import { useReviewList } from '../application/useReviews';
import { KIND_LABELS, STATUS_LABELS, statusClasses, timeAgo } from './reviewFormat';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';

const SETTLED: ReviewStatus[] = ['approved', 'rejected', 'applied', 'apply_failed', 'expired'];

function eventClasses(kind: ValkyrieEventTelemetry['kind']): string {
  if (kind === 'action') return 'niuu:text-state-warn';
  if (kind === 'judgment') return 'niuu:text-brand';
  if (kind === 'signal') return 'niuu:text-state-ok';
  if (kind === 'log') return 'niuu:text-text-muted';
  return 'niuu:text-text-secondary';
}

function TelemetryRow({ event }: { event: ValkyrieEventTelemetry }) {
  return (
    <li data-testid="activity-row" className={`${PANEL} niuu:p-3`}>
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-2">
          <span className={`niuu:text-xs ${eventClasses(event.kind)}`}>{event.kind}</span>
          <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
            {event.summary || event.eventType}
          </span>
          <span
            className={`niuu:shrink-0 niuu:rounded-md niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs ${MUTED}`}
          >
            {event.eventType}
          </span>
        </div>
        <span className={`niuu:shrink-0 niuu:text-xs ${MUTED}`}>
          {timeAgo(event.observedAt)} ago
        </span>
      </div>
      <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
        {event.environmentId}
        {event.valkyrieId ? ` · ${event.valkyrieId}` : ''}
        {event.source ? ` · ${event.source}` : ''}
      </p>
    </li>
  );
}

export function ActivityPage() {
  const dashboard = useValkyrieDashboard();
  const reviews = useReviewList();

  const telemetryEvents = useMemo(
    () =>
      [...(dashboard.data?.telemetry?.recentEvents ?? [])].sort((a, b) =>
        b.observedAt.localeCompare(a.observedAt),
      ),
    [dashboard.data?.telemetry?.recentEvents],
  );
  const settled = useMemo(
    () =>
      (reviews.data ?? [])
        .filter((item) => SETTLED.includes(item.status))
        .sort((a, b) =>
          (b.decidedAt || b.resolvedAt || b.requestedAt).localeCompare(
            a.decidedAt || a.resolvedAt || a.requestedAt,
          ),
        ),
    [reviews.data],
  );

  if (dashboard.isLoading && reviews.isLoading) {
    return (
      <div data-testid="activity-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
        Loading activity…
      </div>
    );
  }
  const blockingError =
    dashboard.error ??
    (telemetryEvents.length === 0 && settled.length === 0 ? reviews.error : null);
  if (blockingError) {
    return (
      <div
        data-testid="activity-error"
        role="alert"
        className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
      >
        {blockingError instanceof Error ? blockingError.message : 'Unable to load activity'}
      </div>
    );
  }

  return (
    <div
      data-testid="activity-page"
      className="niuu:flex niuu:h-full niuu:min-h-0 niuu:flex-col niuu:gap-3 niuu:bg-bg-primary niuu:p-3"
    >
      <header className="niuu:flex niuu:items-center niuu:justify-between">
        <h1 className="niuu:text-base niuu:text-text-primary">Activity</h1>
        <span className={`niuu:text-xs ${MUTED}`}>
          {dashboard.data?.telemetry?.lastObservedAt
            ? `${timeAgo(dashboard.data.telemetry.lastObservedAt)} ago`
            : ''}
        </span>
      </header>
      {telemetryEvents.length === 0 && settled.length === 0 ? (
        <div data-testid="activity-empty" className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}>
          No telemetry or decisions yet.
        </div>
      ) : (
        <ol className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-2 niuu:overflow-auto">
          {telemetryEvents.map((event) => (
            <TelemetryRow key={event.id} event={event} />
          ))}
          {settled.map((item) => (
            <li key={item.itemId} data-testid="activity-row" className={`${PANEL} niuu:p-3`}>
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
                <div className="niuu:flex niuu:items-center niuu:gap-2">
                  <span className={`niuu:text-xs ${statusClasses(item.status)}`}>
                    {STATUS_LABELS[item.status]}
                  </span>
                  <span className="niuu:text-sm niuu:text-text-primary">{item.title}</span>
                  <span
                    className={`niuu:rounded-md niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs ${MUTED}`}
                  >
                    {KIND_LABELS[item.kind]}
                  </span>
                </div>
                <span className={`niuu:text-xs ${MUTED}`}>
                  {timeAgo(item.decidedAt || item.resolvedAt || item.requestedAt)} ago
                </span>
              </div>
              <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
                {item.environmentId} · decided by {item.decidedBy || 'operator'}
                {item.decisionReason ? ` — ${item.decisionReason}` : ''}
                {item.applyDetail ? ` · ${item.applyDetail}` : ''}
              </p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

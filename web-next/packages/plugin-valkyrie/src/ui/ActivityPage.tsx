import { useMemo, useState } from 'react';
import type { ReviewKind, ReviewStatus } from '../domain';
import { useReviewList } from '../application/useReviews';
import { KIND_LABELS, STATUS_LABELS, statusClasses, timeAgo } from './reviewFormat';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';

const SETTLED: ReviewStatus[] = ['approved', 'rejected', 'applied', 'apply_failed', 'expired'];

export function ActivityPage() {
  const [kindFilter, setKindFilter] = useState<ReviewKind | ''>('');
  const { data, isLoading, error } = useReviewList({ kind: kindFilter });

  const settled = useMemo(
    () =>
      (data ?? [])
        .filter((item) => SETTLED.includes(item.status))
        .sort((a, b) =>
          (b.decidedAt || b.resolvedAt || b.requestedAt).localeCompare(
            a.decidedAt || a.resolvedAt || a.requestedAt,
          ),
        ),
    [data],
  );

  if (isLoading) {
    return (
      <div data-testid="activity-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
        Loading activity…
      </div>
    );
  }
  if (error) {
    return (
      <div
        data-testid="activity-error"
        role="alert"
        className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
      >
        {error instanceof Error ? error.message : 'Unable to load activity'}
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
        <select
          aria-label="Filter activity by kind"
          value={kindFilter}
          onChange={(event) => setKindFilter(event.target.value as ReviewKind | '')}
          className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1.5 niuu:text-xs niuu:text-text-primary"
        >
          <option value="">all kinds</option>
          <option value="evolution_build">builds</option>
          <option value="skill_promotion">promotions</option>
          <option value="flock_learning">learnings</option>
          <option value="court_escalation">escalations</option>
          <option value="autonomy_change">autonomy</option>
        </select>
      </header>
      {settled.length === 0 ? (
        <div data-testid="activity-empty" className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}>
          No decisions yet. Every approval, rejection, and operator command lands here — one
          auditable ledger across the whole fleet.
        </div>
      ) : (
        <ol className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-2 niuu:overflow-auto">
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

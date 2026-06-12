import { Activity, Inbox } from 'lucide-react';
import { useReviewSummary } from '../application/useReviews';
import { useValkyrieDashboard } from '../application/useValkyrieDashboard';

export function ValkyrieTopbar() {
  const { data: dashboard } = useValkyrieDashboard();
  const { data: summary } = useReviewSummary();
  const active =
    dashboard?.valkyries.filter((valkyrie) => valkyrie.status !== 'offline').length ?? 0;
  const pending = summary?.pendingTotal ?? 0;
  return (
    <div data-testid="valkyrie-topbar" className="niuu:flex niuu:items-center niuu:gap-2">
      <span
        data-testid="topbar-pending"
        className={`niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:px-2 niuu:py-1 niuu:text-xs ${
          pending > 0
            ? 'niuu:bg-state-warn-bg niuu:text-state-warn'
            : 'niuu:bg-bg-secondary niuu:text-text-muted'
        }`}
      >
        <Inbox size={14} aria-hidden="true" />
        {pending}
      </span>
      <span className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
        <Activity size={14} aria-hidden="true" />
        {active}
      </span>
    </div>
  );
}

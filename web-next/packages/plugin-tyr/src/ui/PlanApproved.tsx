import { Rune } from '@niuulabs/ui';
import type { Saga } from '../domain/saga';

interface PlanApprovedProps {
  saga: Saga;
  onNewPlan?(): void;
}

/**
 * Step 5 of the Plan wizard — confirmation that the saga has been created.
 */
export function PlanApproved({ saga, onNewPlan }: PlanApprovedProps) {
  return (
    <div
      className="niuu-flex niuu-flex-col niuu-gap-6 niuu-items-center niuu-py-8 niuu-text-center"
      aria-live="polite"
      data-testid="plan-approved"
    >
      <Rune glyph="ᚦ" size={48} />

      <div className="niuu-flex niuu-flex-col niuu-gap-2">
        <h2 className="niuu-text-xl niuu-font-semibold niuu-text-text-primary">Saga launched!</h2>
        <p className="niuu-text-sm niuu-text-text-secondary">
          <span className="niuu-font-medium niuu-text-brand-400">{saga.name}</span> has been created
          and is ready for the dispatcher.
        </p>
      </div>

      <dl className="niuu-flex niuu-flex-col niuu-gap-1 niuu-text-left niuu-w-full niuu-max-w-sm niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-4 niuu-py-3">
        <div className="niuu-flex niuu-justify-between niuu-text-sm">
          <dt className="niuu-text-text-muted">Saga</dt>
          <dd className="niuu-text-text-primary niuu-font-medium">{saga.name}</dd>
        </div>
        <div className="niuu-flex niuu-justify-between niuu-text-sm">
          <dt className="niuu-text-text-muted">Branch</dt>
          <dd className="niuu-font-mono niuu-text-text-secondary niuu-text-xs">
            {saga.featureBranch}
          </dd>
        </div>
        <div className="niuu-flex niuu-justify-between niuu-text-sm">
          <dt className="niuu-text-text-muted">Phases</dt>
          <dd className="niuu-text-text-secondary">{saga.phaseSummary.total}</dd>
        </div>
        <div className="niuu-flex niuu-justify-between niuu-text-sm">
          <dt className="niuu-text-text-muted">Confidence</dt>
          <dd className="niuu-text-text-secondary">{saga.confidence}%</dd>
        </div>
      </dl>

      <div className="niuu-flex niuu-gap-3">
        <a
          href="/tyr"
          className="niuu-py-1 niuu-px-3 niuu-bg-brand niuu-text-bg-primary niuu-border niuu-border-brand niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs"
        >
          Open in Sagas →
        </a>
        {onNewPlan && (
          <button
            type="button"
            onClick={onNewPlan}
            className="niuu-rounded-md niuu-px-4 niuu-py-2 niuu-text-sm niuu-font-medium niuu-text-text-secondary niuu-border niuu-border-border hover:niuu-bg-bg-elevated niuu-transition-colors"
          >
            New plan
          </button>
        )}
      </div>
    </div>
  );
}

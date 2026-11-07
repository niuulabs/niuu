import type { JoinStep, StepStatus } from '../../domain/computeHosts';

const DOT_CLASS: Record<StepStatus, string> = {
  done: 'niuu:border-state-ok niuu:bg-state-ok',
  now: 'niuu:border-brand niuu:shadow-[0_0_0_4px_var(--color-bg-tertiary)]',
  todo: 'niuu:border-text-faint',
  failed: 'niuu:border-critical niuu:bg-critical',
};

const STATUS_LABEL: Record<StepStatus, string> = {
  done: 'done',
  now: 'in progress',
  todo: 'not started',
  failed: 'failed',
};

/** Steps from joined to ready, each with its state. */
export function Timeline({
  steps,
}: {
  steps: readonly Pick<JoinStep, 'id' | 'title' | 'detail' | 'status'>[];
}) {
  return (
    <ol className="niuu:m-0 niuu:list-none niuu:p-0" aria-label="Progress">
      {steps.map((step) => (
        <li
          key={step.id}
          data-status={step.status}
          className="niuu:grid niuu:grid-cols-[1.4rem_1fr] niuu:gap-2 niuu:py-1.5"
        >
          <span
            aria-hidden="true"
            className={`niuu:mt-1 niuu:h-3.5 niuu:w-3.5 niuu:rounded-full niuu:border-2 ${DOT_CLASS[step.status]}`}
          />
          <div>
            <div
              className={`niuu:text-sm niuu:font-medium ${
                step.status === 'todo' ? 'niuu:text-text-muted' : 'niuu:text-text-primary'
              } ${step.status === 'failed' ? 'niuu:text-critical-fg' : ''}`}
            >
              {step.title}
              <span className="niuu:sr-only"> ({STATUS_LABEL[step.status]})</span>
            </div>
            {step.detail ? (
              <div
                className={`niuu:text-xs ${
                  step.status === 'failed' ? 'niuu:text-critical-fg' : 'niuu:text-text-muted'
                }`}
              >
                {step.detail}
              </div>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

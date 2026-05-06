import type { PlanStep } from '../domain/plan';
import { PLAN_STEP_LABELS, stepIndex } from '../domain/plan';

interface StepDotsProps {
  steps: readonly PlanStep[];
  current: PlanStep;
}

/**
 * Horizontal step-progress indicator used across the Plan wizard header.
 * Each dot represents one step; the current and completed steps are visually
 * distinguished from pending ones.
 */
export function StepDots({ steps, current }: StepDotsProps) {
  const currentIndex = stepIndex(current);

  return (
    <nav
      aria-label="Plan wizard steps"
      className="niuu-flex niuu-items-center niuu-gap-2 niuu-mb-6"
    >
      {steps.map((step, idx) => {
        const isCompleted = idx < currentIndex;
        const isActive = idx === currentIndex;
        const label = PLAN_STEP_LABELS[step];

        return (
          <div
            key={step}
            className="niuu-flex niuu-items-center niuu-gap-2"
            aria-current={isActive ? 'step' : undefined}
          >
            <div className="niuu-flex niuu-flex-col niuu-items-center niuu-gap-1">
              <span
                className={[
                  'niuu-inline-flex niuu-items-center niuu-justify-center',
                  'niuu-w-2 niuu-h-2 niuu-rounded-full niuu-transition-all',
                  isCompleted
                    ? 'niuu-bg-brand-400'
                    : isActive
                      ? 'niuu-bg-brand-500 niuu-ring-2 niuu-ring-brand-500/30'
                      : 'niuu-bg-bg-elevated',
                ]
                  .filter(Boolean)
                  .join(' ')}
                role="img"
                aria-label={`${label}: ${isCompleted ? 'complete' : isActive ? 'current' : 'pending'}`}
              />
              <span
                className={[
                  'niuu-text-xs niuu-font-medium niuu-transition-colors',
                  isActive
                    ? 'niuu-text-text-primary'
                    : isCompleted
                      ? 'niuu-text-text-secondary'
                      : 'niuu-text-text-muted',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                {label}
              </span>
            </div>

            {idx < steps.length - 1 && (
              <span
                className={[
                  'niuu-flex-1 niuu-h-px niuu-min-w-6 niuu-mb-4',
                  idx < currentIndex ? 'niuu-bg-brand-400' : 'niuu-bg-border',
                ].join(' ')}
                aria-hidden="true"
              />
            )}
          </div>
        );
      })}
    </nav>
  );
}

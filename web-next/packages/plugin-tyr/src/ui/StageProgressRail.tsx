import { Fragment } from 'react';
import type { Phase, PhaseStatus } from '../domain/saga';

function dotStatusLabel(status: PhaseStatus): string {
  if (status === 'complete') return 'complete';
  if (status === 'active') return 'active';
  if (status === 'gated') return 'gated';
  return 'pending';
}

function dotClassName(status: PhaseStatus): string {
  const base =
    'niuu-w-9 niuu-h-9 niuu-rounded-full niuu-flex niuu-items-center niuu-justify-center niuu-text-[15px] niuu-font-semibold niuu-border niuu-shrink-0';
  if (status === 'complete')
    return `${base} niuu-bg-[#455564] niuu-border-[#cad4df] niuu-text-text-primary`;
  if (status === 'active')
    return `${base} niuu-bg-brand niuu-border-brand niuu-text-bg-primary niuu-shadow-[0_0_18px_rgba(125,211,252,0.35)]`;
  if (status === 'gated')
    return `${base} niuu-bg-accent-amber/20 niuu-border-accent-amber niuu-text-accent-amber`;
  return `${base} niuu-bg-bg-elevated niuu-border-border niuu-text-text-muted`;
}

function barClassName(status: PhaseStatus): string {
  const base = 'niuu-flex-1 niuu-h-[2px] niuu-mx-2';
  if (status === 'complete') return `${base} niuu-bg-[#8aa0b8]`;
  return `${base} niuu-bg-border`;
}

interface StageProgressRailProps {
  phases: Phase[];
}

export function StageProgressRail({ phases }: StageProgressRailProps) {
  const completed = phases.filter((p) => p.status === 'complete').length;

  return (
    <section
      aria-label="Stage progress"
      className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-overflow-hidden"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-px-5 niuu-py-4 niuu-border-b niuu-border-border-subtle">
        <h3 className="niuu-m-0 niuu-text-[17px] niuu-font-semibold niuu-text-text-primary">
          Stage progress
        </h3>
        <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-muted">
          {completed} / {phases.length}
        </span>
      </div>

      <div className="niuu-p-5">
        {phases.length === 0 ? (
          <p className="niuu-m-0 niuu-text-xs niuu-text-text-muted">No stages defined.</p>
        ) : (
          <>
            <div className="niuu-flex niuu-items-center" role="list" aria-label="Stage dots">
              {phases.map((phase, i) => (
                <Fragment key={phase.id}>
                  <div
                    role="listitem"
                    aria-label={`Stage ${phase.number}: ${phase.name}, ${dotStatusLabel(phase.status)}`}
                    data-status={phase.status}
                    className={dotClassName(phase.status)}
                  >
                    {phase.number}
                  </div>
                  {i < phases.length - 1 && (
                    <div className={barClassName(phase.status)} aria-hidden="true" />
                  )}
                </Fragment>
              ))}
            </div>
            <div className="niuu-flex niuu-mt-4" aria-label="Stage labels">
              {phases.map((phase) => (
                <span
                  key={phase.id}
                  className={[
                    'niuu-flex-1 niuu-text-[13px] niuu-text-center niuu-truncate',
                    phase.status === 'active'
                      ? 'niuu-text-brand niuu-font-medium'
                      : 'niuu-text-text-muted',
                  ].join(' ')}
                >
                  {phase.name}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

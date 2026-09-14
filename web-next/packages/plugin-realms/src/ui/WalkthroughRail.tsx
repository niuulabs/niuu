import { useWalkthrough, type Walkthrough } from '../application/useWalkthrough';

/**
 * Guidance rail with progress: the numbered-card pattern from Ting's plan guidance,
 * with the steps ticked off as the person gets through them.
 */
export function WalkthroughRail({
  walkthrough,
  action,
}: {
  walkthrough: Walkthrough;
  action?: React.ReactNode;
}) {
  const { done, hidden, complete, currentStepId, setHidden } = useWalkthrough(walkthrough);
  const total = walkthrough.steps.length;
  const doneCount = done.length;
  if (hidden) {
    // A thin edge tab keeps the way back visible on every page that has the rail.
    return (
      <button
        type="button"
        onClick={() => setHidden(false)}
        className="niuu:flex niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:border-l niuu:border-border-subtle niuu:bg-bg-secondary niuu:text-text-muted niuu:hover:text-text-primary"
        title="Show the walkthrough"
        aria-label="Show the walkthrough"
        data-testid="walkthrough-show"
      >
        <span
          className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.25em]"
          style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
        >
          walkthrough · {doneCount}/{total}
        </span>
      </button>
    );
  }

  return (
    <aside
      className="niuu:flex niuu:w-[300px] niuu:shrink-0 niuu:flex-col niuu:gap-4 niuu:border-l niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-5"
      aria-label="Walkthrough"
      data-testid="walkthrough-rail"
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-1.5">
        <div className="niuu:flex niuu:items-center niuu:justify-between">
          <span className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.3em] niuu:text-brand-300">
            walkthrough
          </span>
          <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-faint">
            {doneCount} / {total}
          </span>
        </div>
        <span className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          {walkthrough.title}
        </span>
        <div className="niuu:h-[3px] niuu:overflow-hidden niuu:rounded-full niuu:bg-bg-tertiary">
          <div
            className="niuu:h-full niuu:bg-brand"
            style={{ width: `${(doneCount / total) * 100}%` }}
          />
        </div>
      </div>
      <ol className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:p-0">
        {walkthrough.steps.map((step, index) => {
          const isDone = done.includes(step.id);
          const isCurrent = step.id === currentStepId;
          return (
            <li key={step.id} className="niuu:flex niuu:gap-2.5 niuu:py-2">
              <span
                className={`niuu:flex niuu:h-[18px] niuu:w-[18px] niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:font-mono niuu:text-[10px] ${
                  isDone
                    ? 'niuu:bg-brand niuu:text-bg-primary'
                    : isCurrent
                      ? 'niuu:border-2 niuu:border-brand niuu:text-brand'
                      : 'niuu:border niuu:border-border-subtle niuu:text-text-faint'
                }`}
              >
                {isDone ? '✓' : index + 1}
              </span>
              <div className="niuu:flex niuu:flex-col niuu:gap-0.5">
                <span
                  className={`niuu:text-[13px] ${
                    isDone
                      ? 'niuu:text-text-secondary niuu:line-through'
                      : isCurrent
                        ? 'niuu:font-medium niuu:text-text-primary'
                        : 'niuu:text-text-faint'
                  }`}
                >
                  {step.title}
                </span>
                {step.detail && isCurrent ? (
                  <span className="niuu:text-xs niuu:text-text-muted">{step.detail}</span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      <div className="niuu:flex niuu:flex-col niuu:gap-1.5 niuu:rounded-lg niuu:border niuu:border-brand/25 niuu:bg-brand/5 niuu:p-3">
        <span className="niuu:text-[11px] niuu:text-brand-300">Why this matters</span>
        <span className="niuu:text-xs niuu:text-text-secondary">{walkthrough.tip}</span>
      </div>
      <div className="niuu:flex-1" />
      {complete ? (
        <span className="niuu:text-xs niuu:text-state-ok">Done. Nicely kept.</span>
      ) : (
        action
      )}
      <button
        type="button"
        onClick={() => setHidden(true)}
        className="niuu:self-start niuu:text-[11px] niuu:text-text-muted"
      >
        Hide walkthrough
      </button>
    </aside>
  );
}

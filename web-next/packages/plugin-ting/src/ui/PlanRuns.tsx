import { StateDot } from '@niuulabs/ui';

interface PlanRunsProps {
  error: string | null;
  onBack(): void;
}

/**
 * Step 3 of the Plan wizard — animated waiting state while the planning ravn
 * decomposes the goal into phases and runs.
 */
export function PlanRuns({ error, onBack }: PlanRunsProps) {
  if (error) {
    return (
      <div
        role="alert"
        aria-live="polite"
        className="niuu:flex niuu:flex-col niuu:gap-4 niuu:items-start"
      >
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <StateDot state="failed" />
          <h2 className="niuu:text-lg niuu:font-semibold niuu:text-text-primary">
            Decomposition failed
          </h2>
        </div>
        <p className="niuu:text-sm niuu:text-critical">{error}</p>
        <button
          type="button"
          onClick={onBack}
          className="niuu:rounded-md niuu:px-4 niuu:py-2 niuu:text-sm niuu:font-medium niuu:text-text-secondary niuu:border niuu:border-border niuu:hover:bg-bg-elevated niuu:transition-colors"
        >
          ← Try again
        </button>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Decomposing plan"
      className="niuu:flex niuu:flex-col niuu:gap-6 niuu:items-center niuu:py-10"
    >
      <div className="niuu:flex niuu:flex-col niuu:items-center niuu:gap-3">
        <StateDot state="processing" pulse />
        <h2 className="niuu:text-lg niuu:font-semibold niuu:text-text-primary">
          Ravns are mapping the work…
        </h2>
        <p className="niuu:text-sm niuu:text-text-secondary niuu:text-center niuu:max-w-xs">
          The planning ravn is decomposing your goal into phases and runs. This can take a few
          minutes or longer; you can leave this page open while it works.
        </p>
      </div>

      <div className="niuu:flex niuu:gap-2" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="niuu:w-2 niuu:h-2 niuu:rounded-full niuu:bg-brand-400 niuu:animate-pulse"
            style={{ animationDelay: `${i * 200}ms` }}
          />
        ))}
      </div>

      <div
        aria-live="polite"
        className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-center"
        aria-label="Raven activity"
      >
        <p className="niuu:text-xs niuu:text-text-muted niuu:font-mono">
          ᚱ · decomposer — analyzing brief
        </p>
        <p className="niuu:text-xs niuu:text-text-muted niuu:font-mono">
          ᚱ · investigator — probing repo for affected modules
        </p>
        <p className="niuu:text-xs niuu:text-text-muted niuu:font-mono">
          ᚱ · mimir-indexer — pulling in prior-art sagas
        </p>
      </div>
    </div>
  );
}

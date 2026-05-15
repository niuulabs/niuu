import type { Phase } from '../domain/saga';

interface ConfidenceDriftCardProps {
  confidence: number;
  phases: Phase[];
}

function formatRelativeTime(timestamp: string | null): string {
  if (!timestamp) return '—';
  const ageMs = Date.now() - Date.parse(timestamp);
  const ageMinutes = Math.max(0, Math.floor(ageMs / 60_000));
  if (ageMinutes < 60) return `${ageMinutes}m ago`;
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return `${ageHours}h ago`;
  const ageDays = Math.floor(ageHours / 24);
  return `${ageDays}d ago`;
}

export function ConfidenceDriftCard({ confidence, phases }: ConfidenceDriftCardProps) {
  const runs = phases.flatMap((phase) => phase.runs);
  const averageRunConfidence = runs.length
    ? Math.round(runs.reduce((sum, run) => sum + run.confidence, 0) / runs.length)
    : null;
  const lowConfidenceRuns = runs.filter((run) => run.confidence < 50).length;
  const reviewPressure = runs.filter(
    (run) => run.status === 'review' || run.status === 'escalated',
  ).length;
  const retries = runs.reduce((sum, run) => sum + run.retryCount, 0);
  const latestUpdate =
    runs
      .map((run) => run.updatedAt)
      .sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? null;

  return (
    <section
      aria-label="Confidence signals"
      className="niuu-rounded-xl niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-overflow-hidden"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-px-5 niuu-py-4 niuu-border-b niuu-border-border-subtle">
        <h3 className="niuu-m-0 niuu-text-[17px] niuu-font-semibold niuu-text-text-primary">
          Confidence signals
        </h3>
        <span className="niuu-font-mono niuu-text-[12px] niuu-text-text-muted">live run state</span>
      </div>

      <div className="niuu-p-5 niuu-space-y-4">
        <p className="niuu-m-0 niuu-text-[13px] niuu-leading-6 niuu-text-text-secondary">
          This is the current confidence picture for the saga right now. It is based on live run
          status, current run confidence, retries, and review pressure rather than a simulated
          history curve.
        </p>

        <div className="niuu-grid niuu-gap-3 md:niuu-grid-cols-2 xl:niuu-grid-cols-4">
          <div className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-4">
            <div className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
              Saga confidence
            </div>
            <div className="niuu-mt-2 niuu-font-mono niuu-text-2xl niuu-text-text-primary">
              {Math.round(confidence)}%
            </div>
          </div>

          <div className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-4">
            <div className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
              Average run confidence
            </div>
            <div className="niuu-mt-2 niuu-font-mono niuu-text-2xl niuu-text-text-primary">
              {averageRunConfidence === null ? '—' : `${averageRunConfidence}%`}
            </div>
          </div>

          <div className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-4">
            <div className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
              Review pressure
            </div>
            <div className="niuu-mt-2 niuu-font-mono niuu-text-2xl niuu-text-text-primary">
              {reviewPressure}
            </div>
          </div>

          <div className="niuu-rounded-lg niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-4">
            <div className="niuu-text-[11px] niuu-font-mono niuu-uppercase niuu-tracking-[0.08em] niuu-text-text-muted">
              Latest run update
            </div>
            <div className="niuu-mt-2 niuu-font-mono niuu-text-2xl niuu-text-text-primary">
              {formatRelativeTime(latestUpdate)}
            </div>
          </div>
        </div>

        <div
          className="niuu-flex niuu-flex-wrap niuu-gap-4 niuu-font-mono niuu-text-[12px] niuu-text-text-muted"
          aria-label="Confidence metrics"
        >
          <span>
            runs <strong>{runs.length}</strong>
          </span>
          <span>
            low confidence <strong>{lowConfidenceRuns}</strong>
          </span>
          <span>
            retries <strong>{retries}</strong>
          </span>
          <span>
            phases <strong>{phases.length}</strong>
          </span>
        </div>
      </div>
    </section>
  );
}

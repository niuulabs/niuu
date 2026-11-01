import { AlertCircle } from 'lucide-react';
import type { SourceCoverage } from '../domain/work';

export function WorkCoverageNotices({
  coverage,
  onRetry,
}: {
  coverage: SourceCoverage[];
  onRetry?: () => void;
}) {
  const incomplete = coverage.filter((entry) => entry.status !== 'complete');
  if (incomplete.length === 0) return null;
  return (
    <section className="ting-work-coverage" aria-label="Source coverage">
      {incomplete.map((entry) => (
        <div key={`${entry.source}:${entry.connectionId ?? ''}`} role="status">
          <AlertCircle aria-hidden="true" />
          <span>
            <strong>{entry.source}</strong>{' '}
            {entry.error?.message ??
              (entry.status === 'partial'
                ? 'returned partial results'
                : 'is currently unavailable')}
          </span>
          {onRetry ? (
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      ))}
    </section>
  );
}

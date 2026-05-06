import { cn } from '../../utils/cn';
import './ConfidenceBadge.css';

type ConfidenceTier = 'hi' | 'md' | 'lo';

function getTier(pct: number): ConfidenceTier {
  if (pct >= 80) return 'hi';
  if (pct >= 50) return 'md';
  return 'lo';
}

export interface ConfidenceBadgeProps {
  value: number | null;
  className?: string;
}

export function ConfidenceBadge({ value, className }: ConfidenceBadgeProps) {
  if (value == null || value === 0) {
    return (
      <span className={cn('niuu-conf-badge', 'niuu-conf-badge--null', className)}>
        <span className="niuu-conf-badge__track" aria-hidden="true" />
        <span className="niuu-conf-badge__num">—</span>
      </span>
    );
  }

  const pct = Math.round(value * 100);
  const tier = getTier(pct);

  return (
    <span
      className={cn('niuu-conf-badge', `niuu-conf-badge--${tier}`, className)}
      role="meter"
      aria-label={`confidence: ${pct}%`}
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span className="niuu-conf-badge__track" aria-hidden="true">
        <span className="niuu-conf-badge__fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="niuu-conf-badge__num">{pct}</span>
    </span>
  );
}

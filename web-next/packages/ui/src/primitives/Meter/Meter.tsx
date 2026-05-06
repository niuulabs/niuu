import { cn } from '../../utils/cn';

export interface MeterProps {
  used: number | null | undefined;
  limit: number | null | undefined;
  unit?: string;
  label?: string;
  /** Threshold for "hot" (red). Default 0.85 */
  critical?: number;
  className?: string;
}

/**
 * Utilization bar — 6px bar with 3 color tiers:
 * cool (<60%), warm (60-85%), hot (>85%).
 */
export function Meter({ used, limit, unit = '', label, critical = 0.85, className }: MeterProps) {
  if (used == null || limit == null || limit === 0) {
    return (
      <div className={cn('niuu-flex niuu-flex-col niuu-gap-0.5', className)} data-testid="meter">
        {label && (
          <div className="niuu-flex niuu-justify-between niuu-text-xs">
            <span className="niuu-text-text-muted">{label}</span>
            <span className="niuu-font-mono niuu-text-text-faint">—</span>
          </div>
        )}
        <div
          className="niuu-h-1.5 niuu-rounded-full niuu-bg-bg-elevated"
          role="meter"
          aria-valuenow={0}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label ?? 'meter'}
        >
          <div className="niuu-h-full niuu-rounded-full" style={{ width: '0%' }} />
        </div>
      </div>
    );
  }

  const pct = Math.min(1, used / limit);
  const level = pct >= critical ? 'hot' : pct >= 0.6 ? 'warm' : 'cool';
  const colorClass =
    level === 'hot'
      ? 'niuu-bg-critical'
      : level === 'warm'
        ? 'niuu-bg-state-warn'
        : 'niuu-bg-brand';
  const pctNum = Math.round(pct * 100);

  return (
    <div
      className={cn('niuu-flex niuu-flex-col niuu-gap-0.5', className)}
      data-testid="meter"
      data-level={level}
    >
      {label && (
        <div className="niuu-flex niuu-justify-between niuu-text-xs">
          <span className="niuu-text-text-muted">{label}</span>
          <span className="niuu-font-mono niuu-text-text-faint">
            {used}
            {unit}/{limit}
            {unit}
          </span>
        </div>
      )}
      <div
        className="niuu-h-1.5 niuu-rounded-full niuu-bg-bg-elevated"
        role="meter"
        aria-valuenow={pctNum}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ? `${label} ${pctNum}%` : `${pctNum}%`}
      >
        <div
          className={cn('niuu-h-full niuu-rounded-full niuu-transition-all', colorClass)}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
    </div>
  );
}

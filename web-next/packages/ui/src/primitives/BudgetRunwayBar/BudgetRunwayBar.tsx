import { cn } from '../../utils/cn';
import './BudgetRunwayBar.css';

export interface BudgetRunwayBarProps {
  /** Amount already spent today (USD). */
  spent: number;
  /** Projected total spend by end of day: spent + extrapolated remaining (USD). */
  projected: number;
  /** Daily cap (USD). This is the bar's 100% length. */
  cap: number;
  /**
   * Fraction of the day that has elapsed (0–1).
   * A tick at this position marks "now" on the time axis.
   */
  elapsedFrac: number;
  className?: string;
}

export function BudgetRunwayBar({
  spent,
  projected,
  cap,
  elapsedFrac,
  className,
}: BudgetRunwayBarProps) {
  const spentPct = cap > 0 ? Math.min(100, Math.max(0, (spent / cap) * 100)) : 0;
  const projPct = cap > 0 ? Math.min(100, Math.max(0, ((projected - spent) / cap) * 100)) : 0;
  const over = projected > cap;
  const elapsedPct = Math.min(100, Math.max(0, elapsedFrac * 100));

  return (
    <div
      className={cn('niuu-budget-runway', className)}
      role="meter"
      aria-label="budget runway"
      aria-valuenow={Math.round(spentPct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="niuu-budget-runway__track">
        <div className="niuu-budget-runway__spent" style={{ width: `${spentPct}%` }} />
        <div
          className={cn('niuu-budget-runway__proj', over && 'niuu-budget-runway__proj--over')}
          style={{ left: `${spentPct}%`, width: `${projPct}%` }}
        />
        <div
          className="niuu-budget-runway__now-mark"
          style={{ left: `${elapsedPct}%` }}
          title="now (time elapsed)"
        />
      </div>
    </div>
  );
}

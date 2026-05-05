import { cn } from '../../utils/cn';
import './BudgetBar.css';

const DEFAULT_WARN_AT = 80;

type BudgetTone = 'ok' | 'warn' | 'crit';

function resolveTone(pct: number, warnAt: number): BudgetTone {
  if (pct >= 100) return 'crit';
  if (pct >= warnAt) return 'warn';
  return 'ok';
}

export interface BudgetBarProps {
  /** Amount spent so far (USD). */
  spent: number;
  /** Total budget cap (USD). */
  cap: number;
  /** Percentage threshold above which the bar turns amber. Default: 80. */
  warnAt?: number;
  /** Whether to render the $spent / $cap label. */
  showLabel?: boolean;
  /** Bar height variant. */
  size?: 'sm' | 'md';
  className?: string;
}

export function BudgetBar({
  spent,
  cap,
  warnAt = DEFAULT_WARN_AT,
  showLabel = false,
  size = 'md',
  className,
}: BudgetBarProps) {
  const pct = cap > 0 ? (spent / cap) * 100 : 0;
  const tone = resolveTone(pct, warnAt);
  const fillWidth = `${Math.min(100, Math.max(0, pct))}%`;

  return (
    <div
      className={cn(
        'niuu-budget-bar',
        `niuu-budget-bar--${size}`,
        `niuu-budget-bar--${tone}`,
        className,
      )}
      role="meter"
      aria-label="budget"
      aria-valuenow={Math.min(100, Math.round(pct))}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="niuu-budget-bar__track">
        <div className="niuu-budget-bar__fill" style={{ width: fillWidth }} />
        {warnAt > 0 && warnAt < 100 && (
          <div className="niuu-budget-bar__warn-mark" style={{ left: `${warnAt}%` }} />
        )}
      </div>
      {showLabel && (
        <div className="niuu-budget-bar__label">
          <span className="niuu-budget-bar__spent">${spent.toFixed(2)}</span>
          <span className="niuu-budget-bar__divider" aria-hidden="true">
            /
          </span>
          <span className="niuu-budget-bar__cap">${cap.toFixed(2)}</span>
        </div>
      )}
    </div>
  );
}

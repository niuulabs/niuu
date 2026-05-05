/**
 * LintBadge — compact severity summary for a lint report.
 *
 * Plugin-local for now; promote to @niuulabs/ui when a second plugin needs it.
 */
import './LintBadge.css';

export interface LintBadgeSummary {
  error: number;
  warn: number;
  info: number;
}

export interface LintBadgeProps {
  summary: LintBadgeSummary;
  /** Display size. @default 'md' */
  size?: 'sm' | 'md';
  className?: string;
}

/**
 * Renders a row of error / warn / info counts, colour-coded by severity.
 * Returns null when all counts are zero.
 */
export function LintBadge({ summary, size = 'md', className }: LintBadgeProps) {
  const { error, warn, info } = summary;
  const hasIssues = error + warn + info > 0;

  if (!hasIssues) {
    return (
      <span
        className={[
          'lint-badge',
          'lint-badge--clean',
          size === 'sm' ? 'lint-badge--sm' : '',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        aria-label="no lint issues"
        data-testid="lint-badge"
      >
        ✓ clean
      </span>
    );
  }

  return (
    <span
      className={['lint-badge', size === 'sm' ? 'lint-badge--sm' : '', className]
        .filter(Boolean)
        .join(' ')}
      aria-label={`${error} errors, ${warn} warnings, ${info} info`}
      data-testid="lint-badge"
    >
      {error > 0 && (
        <span className="lint-badge__count lint-badge__count--error" data-testid="lint-badge-error">
          {error} {error === 1 ? 'error' : 'errors'}
        </span>
      )}
      {warn > 0 && (
        <span className="lint-badge__count lint-badge__count--warn" data-testid="lint-badge-warn">
          {warn} {warn === 1 ? 'warning' : 'warnings'}
        </span>
      )}
      {info > 0 && (
        <span className="lint-badge__count lint-badge__count--info" data-testid="lint-badge-info">
          {info} info
        </span>
      )}
    </span>
  );
}

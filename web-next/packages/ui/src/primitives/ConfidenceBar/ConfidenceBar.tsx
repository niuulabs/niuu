import { cn } from '../../utils/cn';
import './ConfidenceBar.css';

export type ConfidenceLevel = 'high' | 'medium' | 'low';

const LEVEL_VALUE: Record<ConfidenceLevel, number> = { high: 100, medium: 60, low: 25 };

export interface ConfidenceBarProps {
  level: ConfidenceLevel;
  /** Hide the text label — show only the bar. */
  hideLabel?: boolean;
  className?: string;
}

export function ConfidenceBar({ level, hideLabel, className }: ConfidenceBarProps) {
  return (
    <span
      className={cn('niuu-conf-bar', `niuu-conf-bar--${level}`, className)}
      role="meter"
      aria-label={`confidence: ${level}`}
      aria-valuenow={LEVEL_VALUE[level]}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span className="niuu-conf-bar__track" aria-hidden="true">
        <span className="niuu-conf-bar__fill" />
      </span>
      {!hideLabel && <span className="niuu-conf-bar__label">{level}</span>}
    </span>
  );
}

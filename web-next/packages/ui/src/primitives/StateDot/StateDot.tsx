import { cn } from '../../utils/cn';
import './StateDot.css';

export type DotState =
  | 'healthy'
  | 'running'
  | 'observing'
  | 'merged'
  | 'attention'
  | 'review'
  | 'queued'
  | 'processing'
  | 'deciding'
  | 'failed'
  | 'degraded'
  | 'unknown'
  | 'idle'
  | 'archived';

export interface StateDotProps {
  state: DotState;
  pulse?: boolean;
  size?: number;
  title?: string;
  className?: string;
}

export function StateDot({ state, pulse = false, size = 8, title, className }: StateDotProps) {
  return (
    <span
      className={cn(
        'niuu-state-dot',
        `niuu-state-dot--${state}`,
        pulse && 'niuu-state-dot--pulse',
        className,
      )}
      style={{ width: size, height: size }}
      title={title ?? state}
      aria-label={title ?? state}
      role="status"
    />
  );
}

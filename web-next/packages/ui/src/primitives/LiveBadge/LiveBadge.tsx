import { cn } from '../../utils/cn';
import './LiveBadge.css';

export interface LiveBadgeProps {
  label?: string;
  className?: string;
}

export function LiveBadge({ label = 'LIVE', className }: LiveBadgeProps) {
  return (
    <span className={cn('niuu-live-badge', className)} role="status" aria-label={label}>
      <span className="niuu-live-badge__dot" />
      {label}
    </span>
  );
}

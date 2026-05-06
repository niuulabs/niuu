import { cn } from '../../utils/cn';
import './states.css';

export interface LoadingStateProps {
  label?: string;
  className?: string;
}

export function LoadingState({ label = 'Loading…', className }: LoadingStateProps) {
  return (
    <div
      className={cn('niuu-state niuu-state--loading', className)}
      role="status"
      aria-label={label}
    >
      <span className="niuu-state__spinner" aria-hidden="true" />
      <p className="niuu-state__title">{label}</p>
    </div>
  );
}

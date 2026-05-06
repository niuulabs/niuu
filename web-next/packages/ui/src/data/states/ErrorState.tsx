import { type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './states.css';

export interface ErrorStateProps {
  icon?: ReactNode;
  title?: string;
  message: string;
  action?: ReactNode;
  className?: string;
}

export function ErrorState({
  icon,
  title = 'Something went wrong',
  message,
  action,
  className,
}: ErrorStateProps) {
  return (
    <div className={cn('niuu-state niuu-state--error', className)} role="alert">
      {icon && <div className="niuu-state__icon">{icon}</div>}
      <p className="niuu-state__title">{title}</p>
      <p className="niuu-state__desc">{message}</p>
      {action && <div className="niuu-state__action">{action}</div>}
    </div>
  );
}

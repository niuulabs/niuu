import { type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './states.css';

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('niuu-state niuu-state--empty', className)} role="status">
      {icon && <div className="niuu-state__icon">{icon}</div>}
      <p className="niuu-state__title">{title}</p>
      {description && <p className="niuu-state__desc">{description}</p>}
      {action && <div className="niuu-state__action">{action}</div>}
    </div>
  );
}

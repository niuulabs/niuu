import { cn } from '@/modules/shared/utils/classnames';
import styles from './FlockBadge.module.css';

interface FlockBadgeProps {
  participantCount?: number;
  className?: string;
}

export function FlockBadge({ participantCount, className }: FlockBadgeProps) {
  return (
    <span className={cn(styles.badge, className)}>
      ⬡ flock{participantCount != null ? ` ×${participantCount}` : ''}
    </span>
  );
}

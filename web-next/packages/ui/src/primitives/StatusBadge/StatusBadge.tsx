import { cn } from '../../utils/cn';
import './StatusBadge.css';

export type BadgeStatus =
  | 'running'
  | 'active'
  | 'complete'
  | 'merged'
  | 'review'
  | 'queued'
  | 'escalated'
  | 'blocked'
  | 'pending'
  | 'failed'
  | 'archived'
  | 'gated';

type BadgeTone = 'run' | 'ok' | 'warn' | 'crit' | 'mute' | 'gate';

const TONE_MAP: Record<BadgeStatus, BadgeTone> = {
  running: 'run',
  active: 'run',
  complete: 'ok',
  merged: 'ok',
  review: 'warn',
  queued: 'warn',
  escalated: 'warn',
  blocked: 'warn',
  pending: 'mute',
  archived: 'mute',
  failed: 'crit',
  gated: 'gate',
};

export interface StatusBadgeProps {
  status: BadgeStatus;
  pulse?: boolean;
  className?: string;
}

export function StatusBadge({ status, pulse = false, className }: StatusBadgeProps) {
  const tone = TONE_MAP[status] ?? 'mute';
  return (
    <span
      className={cn(
        'niuu-status-badge',
        `niuu-status-badge--${tone}`,
        pulse && 'niuu-status-badge--pulse',
        className,
      )}
      role="status"
      aria-label={status}
    >
      <span className="niuu-status-badge__dot" aria-hidden="true" />
      {status}
    </span>
  );
}

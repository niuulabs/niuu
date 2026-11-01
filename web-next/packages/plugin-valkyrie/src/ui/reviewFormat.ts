import type { ReviewItem, ReviewKind, ReviewRiskClass, ReviewStatus } from '../domain';

export const KIND_LABELS: Record<ReviewKind, string> = {
  evolution_build: 'build',
  skill_promotion: 'promotion',
  flock_learning: 'learning',
  court_escalation: 'escalation',
  autonomy_change: 'autonomy',
  morning_brief: 'brief',
};

export const STATUS_LABELS: Record<ReviewStatus, string> = {
  pending: 'pending',
  approved: 'approved',
  rejected: 'rejected',
  expired: 'expired',
  applied: 'applied',
  apply_failed: 'apply failed',
};

export function riskClasses(risk: ReviewRiskClass): string {
  if (risk === 'critical' || risk === 'high') return 'niuu:text-critical';
  if (risk === 'medium') return 'niuu:text-state-warn';
  return 'niuu:text-text-muted';
}

export function statusClasses(status: ReviewStatus): string {
  if (status === 'pending') return 'niuu:text-state-warn';
  if (status === 'rejected' || status === 'apply_failed') return 'niuu:text-critical';
  if (status === 'expired') return 'niuu:text-text-muted';
  return 'niuu:text-state-ok';
}

export function timeAgo(value: string, now: Date = new Date()): string {
  if (!value) return '—';
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return value;
  const seconds = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function sortByUrgency(items: ReviewItem[]): ReviewItem[] {
  return [...items].sort((a, b) => {
    if (a.urgency !== b.urgency) return b.urgency - a.urgency;
    return b.requestedAt.localeCompare(a.requestedAt);
  });
}

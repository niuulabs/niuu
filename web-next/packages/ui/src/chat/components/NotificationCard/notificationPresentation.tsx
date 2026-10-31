import {
  BellRing,
  CircleAlert,
  Flag,
  Info,
  MessageSquareText,
  Signpost,
  type LucideIcon,
} from 'lucide-react';
import type { ForgeNotificationKind, ForgeNotificationSeverity } from '@niuulabs/domain';

/**
 * Shared presentation for Forge notifications — the inline chat card and the
 * Forge notifications feed use the same icons, labels and severity tones.
 * Tones are token-backed Tailwind classes only (red stays reserved for critical).
 */

const KIND_ICONS: Record<ForgeNotificationKind, LucideIcon> = {
  milestone: Flag,
  decision: Signpost,
  attention: BellRing,
  reply_ready: MessageSquareText,
  error: CircleAlert,
  info: Info,
};

export const NOTIFICATION_KIND_LABELS: Record<ForgeNotificationKind, string> = {
  milestone: 'Milestone',
  decision: 'Decision',
  attention: 'Needs attention',
  reply_ready: 'Reply ready',
  error: 'Error',
  info: 'Info',
};

export const NOTIFICATION_SEVERITY_LABELS: Record<ForgeNotificationSeverity, string> = {
  info: 'Info',
  success: 'Success',
  warning: 'Warning',
  critical: 'Critical',
};

export interface NotificationTone {
  /** Foreground accent for the icon and kind label. */
  text: string;
  /**
   * Background for a severity accent bar. A separate element, not a border
   * colour, so a host's own border utilities can never override it.
   */
  bar: string;
  /** Soft background for chips. */
  soft: string;
}

const TONES: Record<ForgeNotificationSeverity, NotificationTone> = {
  info: {
    text: 'niuu:text-brand',
    bar: 'niuu:bg-brand',
    soft: 'niuu:bg-brand/15 niuu:text-brand',
  },
  success: {
    text: 'niuu:text-state-ok',
    bar: 'niuu:bg-state-ok',
    soft: 'niuu:bg-state-ok-bg niuu:text-state-ok',
  },
  warning: {
    text: 'niuu:text-state-warn',
    bar: 'niuu:bg-state-warn',
    soft: 'niuu:bg-state-warn-bg niuu:text-state-warn',
  },
  critical: {
    text: 'niuu:text-critical',
    bar: 'niuu:bg-critical',
    soft: 'niuu:bg-critical-bg niuu:text-critical',
  },
};

export function notificationTone(severity: ForgeNotificationSeverity): NotificationTone {
  return TONES[severity] ?? TONES.info;
}

export interface NotificationKindIconProps {
  kind: ForgeNotificationKind;
  className?: string;
}

export function NotificationKindIcon({ kind, className }: NotificationKindIconProps) {
  const Icon = KIND_ICONS[kind] ?? Info;
  return <Icon className={className} aria-hidden="true" data-kind-icon={kind} />;
}

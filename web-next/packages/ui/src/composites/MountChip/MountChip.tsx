import { cn } from '../../utils/cn';
import type { MountRole } from '@niuulabs/domain';
import './MountChip.css';

/**
 * Ravn mount-binding roles — describes how a raven uses a mount.
 * Ordered by access level: primary (full r/w), archive (append), ro (read-only).
 */
export type MountBindingRole = 'primary' | 'archive' | 'ro';

/**
 * Mímir mount-kind roles — re-exported from @niuulabs/domain.
 * local (operator-private), shared (realm-wide), domain (prefix-scoped).
 */
export type MountKindRole = MountRole;

export type MountChipRole = MountBindingRole | MountKindRole;

/** Unicode glyphs for Mímir mount-kind roles. */
export const MOUNT_KIND_GLYPH: Record<MountKindRole, string> = {
  local: '◉',
  shared: '◎',
  domain: '◈',
};

const BINDING_ROLES = new Set<MountChipRole>(['primary', 'archive', 'ro']);

function isMountKindRole(role: MountChipRole): role is MountKindRole {
  return !BINDING_ROLES.has(role);
}

export interface MountChipProps {
  name: string;
  role: MountChipRole;
  /** Write-routing priority (lower wins). Shown in tooltip when provided. */
  priority?: number;
  className?: string;
}

/**
 * Mount chip — shows a mount's name and its role.
 *
 * Two usage contexts:
 * - Ravn: `role` is `primary | archive | ro` — the raven's binding mode.
 * - Mímir: `role` is `local | shared | domain` — the mount's deployment kind.
 */
export function MountChip({ name, role, priority, className }: MountChipProps) {
  const tooltipSuffix = priority != null ? ` · p${priority}` : '';
  const tooltip = `${name} (${role}${tooltipSuffix})`;

  if (isMountKindRole(role)) {
    return (
      <span
        className={cn('niuu-mount-chip', `niuu-mount-chip--${role}`, className)}
        title={tooltip}
      >
        <span className="niuu-mount-chip__glyph" aria-hidden>
          {MOUNT_KIND_GLYPH[role]}
        </span>
        <span className="niuu-mount-chip__name">{name}</span>
        <span className="niuu-mount-chip__role">{role}</span>
      </span>
    );
  }

  return (
    <span className={cn('niuu-mount-chip', `niuu-mount-chip--${role}`, className)} title={tooltip}>
      <span className="niuu-mount-chip__dot" aria-hidden />
      <span className="niuu-mount-chip__name">{name}</span>
      <span className="niuu-mount-chip__role">{role}</span>
    </span>
  );
}

/**
 * MountChip — inline chip showing mount name and role.
 *
 * Used throughout Mímir to stamp provenance on pages and sources.
 * Plugin-local for now; promote to @niuulabs/ui when a second plugin needs it.
 */

import type { Mount, MountRole } from '@niuulabs/domain';

import { accessScopeLabel } from '../../domain/access-scope';

interface MountChipProps {
  name: string;
  role?: MountRole;
  accessScope?: Mount['accessScope'];
  /** Override the chip's click handler (e.g. to focus the mount in Overview). */
  onClick?: () => void;
}

export function MountChip({ name, role, accessScope, onClick }: MountChipProps) {
  const label = accessScope ? accessScopeLabel(accessScope) : role;
  const cls = `mm-mount-chip mm-mount-chip--${role ?? 'local'}`;

  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} aria-label={`mount: ${name}`}>
        <span className="mm-mount-chip__name">{name}</span>
        {label && <span className="mm-mount-chip__role">{label}</span>}
      </button>
    );
  }

  return (
    <span className={cls} aria-label={`mount: ${name}`}>
      <span className="mm-mount-chip__name">{name}</span>
      {label && <span className="mm-mount-chip__role">{label}</span>}
    </span>
  );
}

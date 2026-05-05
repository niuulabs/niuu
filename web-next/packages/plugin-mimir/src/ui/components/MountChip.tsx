/**
 * MountChip — inline chip showing mount name and role.
 *
 * Used throughout Mímir to stamp provenance on pages and sources.
 * Plugin-local for now; promote to @niuulabs/ui when a second plugin needs it.
 */

import type { MountRole } from '@niuulabs/domain';

interface MountChipProps {
  name: string;
  role?: MountRole;
  /** Override the chip's click handler (e.g. to focus the mount in Overview). */
  onClick?: () => void;
}

export function MountChip({ name, role, onClick }: MountChipProps) {
  const cls = `mm-mount-chip mm-mount-chip--${role ?? 'local'}`;

  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} aria-label={`mount: ${name}`}>
        <span className="mm-mount-chip__name">{name}</span>
        {role && <span className="mm-mount-chip__role">{role}</span>}
      </button>
    );
  }

  return (
    <span className={cls} aria-label={`mount: ${name}`}>
      <span className="mm-mount-chip__name">{name}</span>
      {role && <span className="mm-mount-chip__role">{role}</span>}
    </span>
  );
}

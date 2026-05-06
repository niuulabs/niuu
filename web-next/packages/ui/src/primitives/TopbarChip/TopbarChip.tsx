/**
 * TopbarChip — shared stat chip for the shell topbar-right area.
 *
 * Used by plugin topbar components (TyrTopbar, RavnTopbar, etc.) to
 * render consistent KPI indicators with an icon and label.
 */

export interface TopbarChipProps {
  kind: 'ok' | 'err' | 'dim';
  icon: string;
  label: string;
  /** Custom data-testid. Falls back to `topbar-chip-${kind}`. */
  testId?: string;
}

export function TopbarChip({ kind, icon, label, testId }: TopbarChipProps) {
  return (
    <span
      className={`niuu-inline-flex niuu-items-center niuu-gap-1 niuu-px-2 niuu-py-0.5 niuu-rounded-full niuu-text-xs niuu-font-mono niuu-topbar-chip niuu-topbar-chip--${kind}`}
      data-testid={testId ?? `topbar-chip-${kind}`}
    >
      <span aria-hidden="true">{icon}</span>
      {label}
    </span>
  );
}

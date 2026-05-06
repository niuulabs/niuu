import type { ConnectionType } from '../../domain/session';

export interface ConnectionTypeBadgeProps {
  connectionType: ConnectionType;
  className?: string;
}

const LABELS: Record<ConnectionType, string> = {
  cli: 'CLI',
  ide: 'IDE',
  api: 'API',
};

/** Small rounded badge showing how a session is accessed: CLI, IDE, or API. */
export function ConnectionTypeBadge({ connectionType, className }: ConnectionTypeBadgeProps) {
  return (
    <span
      className={`niuu-inline-flex niuu-items-center niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-secondary${className ? ` ${className}` : ''}`}
      data-testid="connection-type-badge"
    >
      {LABELS[connectionType]}
    </span>
  );
}

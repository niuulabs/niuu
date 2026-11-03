export interface CliBadgeProps {
  cli: string;
  compact?: boolean;
  className?: string;
}

const CLI_META: Record<string, { label: string; mark: string }> = {
  claude: { label: 'Claude Code', mark: 'Cl' },
  codex: { label: 'Codex', mark: 'Cx' },
  gemini: { label: 'Gemini', mark: 'Ge' },
  aider: { label: 'Aider', mark: 'Ai' },
};

/** CLI tool identity badge — the label, or a short mark when compact */
export function CliBadge({ cli, compact = false, className }: CliBadgeProps) {
  const meta = CLI_META[cli];
  if (!meta) return null;

  return (
    <span
      className={`niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-1.5 niuu:py-0.5 niuu:text-xs ${className ?? ''}`}
      data-testid="cli-badge"
      title={meta.label}
    >
      {compact ? (
        <span className="niuu:font-mono niuu:text-text-secondary">{meta.mark}</span>
      ) : (
        <span className="niuu:text-text-secondary">{meta.label}</span>
      )}
    </span>
  );
}

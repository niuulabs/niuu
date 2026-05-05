export interface CliBadgeProps {
  cli: string;
  compact?: boolean;
  className?: string;
}

const CLI_META: Record<string, { label: string; rune: string }> = {
  claude: { label: 'Claude Code', rune: 'ᛗ' },
  codex: { label: 'Codex', rune: 'ᚲ' },
  gemini: { label: 'Gemini', rune: 'ᛇ' },
  aider: { label: 'Aider', rune: 'ᚨ' },
};

/** CLI tool identity badge — rune + label */
export function CliBadge({ cli, compact = false, className }: CliBadgeProps) {
  const meta = CLI_META[cli];
  if (!meta) return null;

  return (
    <span
      className={`niuu-inline-flex niuu-items-center niuu-gap-1 niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-tertiary niuu-px-1.5 niuu-py-0.5 niuu-text-xs ${className ?? ''}`}
      data-testid="cli-badge"
      title={meta.label}
    >
      <span className="niuu-font-mono niuu-text-text-secondary">{meta.rune}</span>
      {!compact && <span className="niuu-text-text-secondary">{meta.label}</span>}
    </span>
  );
}

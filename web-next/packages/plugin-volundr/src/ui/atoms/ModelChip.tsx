export interface ModelData {
  alias: string;
  tier?: string;
}

export interface ModelChipProps {
  model: ModelData | string | null | undefined;
  className?: string;
}

const TIER_CLASSES: Record<string, string> = {
  frontier: 'niuu-bg-brand',
  reasoning: 'niuu-bg-state-warn',
  execution: 'niuu-bg-state-ok',
  balanced: 'niuu-bg-text-muted',
};

/** Model alias with tier color indicator */
export function ModelChip({ model, className }: ModelChipProps) {
  if (!model) {
    return (
      <span className="niuu-font-mono niuu-text-text-faint" data-testid="model-chip">
        —
      </span>
    );
  }

  const alias = typeof model === 'string' ? model : model.alias;
  const tier = typeof model === 'string' ? undefined : model.tier;

  return (
    <span
      className={`niuu-inline-flex niuu-items-center niuu-gap-1 niuu-text-xs ${className ?? ''}`}
      data-testid="model-chip"
      title={alias}
    >
      {tier && (
        <span
          className={`niuu-inline-block niuu-h-2 niuu-w-2 niuu-rounded-full ${TIER_CLASSES[tier] ?? 'niuu-bg-text-faint'}`}
          aria-hidden
        />
      )}
      <span className="niuu-font-mono niuu-text-text-secondary">{alias}</span>
    </span>
  );
}

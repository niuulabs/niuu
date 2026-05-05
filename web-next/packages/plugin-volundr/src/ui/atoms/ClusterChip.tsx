export interface ClusterData {
  name: string;
  kind: string;
}

export interface ClusterChipProps {
  cluster: ClusterData | null | undefined;
  className?: string;
}

const KIND_CLASSES: Record<string, string> = {
  primary: 'niuu-text-brand',
  gpu: 'niuu-text-state-warn',
  edge: 'niuu-text-text-muted',
  local: 'niuu-text-text-secondary',
  observ: 'niuu-text-text-muted',
  media: 'niuu-text-text-muted',
};

/** Cluster name + kind badge */
export function ClusterChip({ cluster, className }: ClusterChipProps) {
  if (!cluster) {
    return (
      <span className="niuu-font-mono niuu-text-text-faint" data-testid="cluster-chip">
        —
      </span>
    );
  }

  return (
    <span
      className={`niuu-inline-flex niuu-items-center niuu-gap-1 niuu-text-xs ${className ?? ''}`}
      data-testid="cluster-chip"
    >
      <span className="niuu-font-medium niuu-text-text-secondary">{cluster.name}</span>
      <span className={`niuu-font-mono niuu-text-text-faint ${KIND_CLASSES[cluster.kind] ?? ''}`}>
        {cluster.kind}
      </span>
    </span>
  );
}

import type { Topology, TopologyNode } from '../../domain';
import './Minimap.css';

const MINIMAP_W = 160;
const MINIMAP_H = 120;
const NODE_R = 4;
const PADDING = 10;

const STATUS_COLOR: Record<string, string> = {
  healthy: 'var(--color-accent-emerald)',
  degraded: 'var(--color-accent-amber)',
  failed: 'var(--color-accent-red)',
  idle: 'var(--color-text-muted)',
  observing: 'var(--color-accent-cyan)',
  unknown: 'var(--color-text-muted)',
};

export interface MinimapProps {
  topology: Topology | null;
  selectedNodeId?: string | null;
}

export function Minimap({ topology, selectedNodeId = null }: MinimapProps) {
  if (!topology || topology.nodes.length === 0) {
    return (
      <div
        className="obs-minimap obs-minimap--empty"
        aria-label="Minimap — no topology"
        data-testid="minimap-panel"
      >
        <span className="obs-minimap__empty-label">no topology</span>
      </div>
    );
  }

  const positions = computeCircularLayout(topology.nodes, MINIMAP_W, MINIMAP_H, PADDING);

  return (
    <div className="obs-minimap" aria-label="Minimap" data-testid="minimap-panel">
      <div className="obs-minimap__header">
        <span>Minimap</span>
        <span className="obs-minimap__count">{topology.nodes.length} entities</span>
      </div>
      <svg
        width={MINIMAP_W}
        height={MINIMAP_H}
        className="obs-minimap__svg"
        role="img"
        aria-label={`Topology minimap: ${topology.nodes.length} nodes, ${topology.edges.length} edges`}
      >
        {topology.edges.map((edge) => {
          const src = positions.get(edge.sourceId);
          const tgt = positions.get(edge.targetId);
          if (!src || !tgt) return null;
          return (
            <line
              key={edge.id}
              x1={src[0]}
              y1={src[1]}
              x2={tgt[0]}
              y2={tgt[1]}
              stroke="var(--color-border)"
              strokeWidth={0.75}
              opacity={0.5}
            />
          );
        })}
        {topology.nodes.map((node) => {
          const pos = positions.get(node.id);
          if (!pos) return null;
          const isSelected = node.id === selectedNodeId;
          return (
            <circle
              key={node.id}
              cx={pos[0]}
              cy={pos[1]}
              r={isSelected ? NODE_R + 2 : NODE_R}
              fill={STATUS_COLOR[node.status] ?? 'var(--color-text-muted)'}
              stroke={isSelected ? 'var(--color-text-primary)' : 'none'}
              strokeWidth={1.5}
              data-node-id={node.id}
              aria-label={node.label}
            />
          );
        })}
      </svg>
    </div>
  );
}

function computeCircularLayout(
  nodes: TopologyNode[],
  width: number,
  height: number,
  padding: number,
): Map<string, [number, number]> {
  const positions = new Map<string, [number, number]>();
  const count = nodes.length;

  if (count === 1) {
    positions.set(nodes[0]!.id, [width / 2, height / 2]);
    return positions;
  }

  const cx = width / 2;
  const cy = height / 2;
  const rx = (width - 2 * padding) / 2;
  const ry = (height - 2 * padding) / 2;

  nodes.forEach((node, i) => {
    const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
    positions.set(node.id, [cx + rx * Math.cos(angle), cy + ry * Math.sin(angle)]);
  });

  return positions;
}

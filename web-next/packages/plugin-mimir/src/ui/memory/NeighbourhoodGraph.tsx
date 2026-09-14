/**
 * NeighbourhoodGraph — the page in the middle, what it links to around it.
 *
 * Deliberately small: eight neighbours at most, edge labels only where the
 * link is typed. The full picture is the graph view.
 */

import { ringLayout, type Neighbourhood } from '../../domain/neighbourhood';
import type { RelatedPage } from '../../domain/evidence';

const WIDTH = 300;
const HEIGHT = 220;
const RADIUS = 78;
const MAX_NODES = 8;
const CENTRE_RADIUS = 7;
const NODE_RADIUS = 5;

export interface NeighbourhoodGraphProps {
  title: string;
  related: RelatedPage[];
  onOpen: (path: string) => void;
}

export function NeighbourhoodGraph({ title, related, onOpen }: NeighbourhoodGraphProps) {
  const layout: Neighbourhood = ringLayout(related, {
    limit: MAX_NODES,
    width: WIDTH,
    height: HEIGHT,
    radius: RADIUS,
  });

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="niuu:w-full"
      role="group"
      aria-label={`pages around ${title}`}
    >
      {layout.nodes.map((node) => (
        <g key={node.path}>
          <line
            x1={layout.centre.x}
            y1={layout.centre.y}
            x2={node.x}
            y2={node.y}
            stroke="var(--color-border)"
            strokeWidth={1}
            strokeDasharray={node.direction === 'in' ? '3 3' : undefined}
          />
          {node.rel ? (
            <text
              x={(layout.centre.x + node.x) / 2}
              y={(layout.centre.y + node.y) / 2 - 3}
              textAnchor="middle"
              fontSize={8}
              fill="var(--color-text-faint)"
            >
              {node.rel}
            </text>
          ) : null}
        </g>
      ))}

      {layout.nodes.map((node) => (
        <g
          key={`node-${node.path}`}
          role="button"
          tabIndex={0}
          aria-label={`open ${node.label}`}
          className="niuu:cursor-pointer"
          onClick={() => onOpen(node.path)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') onOpen(node.path);
          }}
        >
          <circle cx={node.x} cy={node.y} r={NODE_RADIUS} fill="var(--brand-300)" />
          <text
            x={node.x}
            y={node.y + 15}
            textAnchor="middle"
            fontSize={9}
            fill="var(--color-text-secondary)"
          >
            {node.label}
          </text>
        </g>
      ))}

      <circle
        cx={layout.centre.x}
        cy={layout.centre.y}
        r={CENTRE_RADIUS}
        fill="var(--color-bg-primary)"
        stroke="var(--color-brand)"
        strokeWidth={2}
      />

      {layout.overflow > 0 ? (
        <text
          x={WIDTH - 4}
          y={HEIGHT - 4}
          textAnchor="end"
          fontSize={9}
          fill="var(--color-text-faint)"
        >
          +{layout.overflow} more
        </text>
      ) : null}
    </svg>
  );
}

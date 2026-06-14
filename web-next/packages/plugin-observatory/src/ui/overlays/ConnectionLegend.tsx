import type { CSSProperties } from 'react';
import type { EdgeRelationType, Registry, Topology } from '../../domain';
import { humanizeObservatoryText } from '../displayLabels';
import { nodeColour, nodeIconGlyph, rgba } from '../TopologyCanvas/renderer';
import './ConnectionLegend.css';

type EdgeLegendKey = Exclude<EdgeRelationType, 'contains'> | 'run';

interface EdgeLegendEntry {
  relation: EdgeLegendKey;
  label: string;
}

interface NodeLegendEntry {
  typeId: string;
  label: string;
  count?: number;
}

interface ConnectionLegendProps {
  topology?: Topology | null;
  registry?: Registry | null;
}

const EDGE_LEGEND: EdgeLegendEntry[] = [
  { relation: 'manages', label: 'manages' },
  { relation: 'writes', label: 'writes' },
  { relation: 'reads', label: 'reads' },
  { relation: 'routes_to', label: 'routes' },
  { relation: 'observes', label: 'observes' },
  { relation: 'signals_to', label: 'signals' },
  { relation: 'uses', label: 'uses' },
  { relation: 'exposes', label: 'exposes' },
  { relation: 'member_of', label: 'membership' },
  { relation: 'run', label: 'run flow' },
];

const FALLBACK_NODE_TYPES = [
  'realm',
  'cluster',
  'namespace',
  'mimir',
  'warden',
  'ravn_long',
  'ravn_run',
  'ting',
  'bifrost',
  'volundr',
  'skuld',
  'service',
];

export function ConnectionLegend({ topology = null, registry = null }: ConnectionLegendProps) {
  const nodeEntries = nodeLegendEntries(topology, registry);

  return (
    <aside className="obs-conn-legend" aria-label="Topology legend">
      <section className="obs-conn-legend__section">
        <h2 className="obs-conn-legend__heading">Connections</h2>
        <ul className="obs-conn-legend__list" aria-label="Connection types">
          {EDGE_LEGEND.map(({ relation, label }) => (
            <li
              key={relation}
              className="obs-conn-legend__item"
              data-relation={relation}
              data-testid={`legend-edge-${relation}`}
            >
              <svg className="obs-conn-legend__line-svg" width={42} height={14} aria-hidden="true">
                <EdgeLine relation={relation} />
              </svg>
              <span className="obs-conn-legend__label">{label}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="obs-conn-legend__section">
        <h2 className="obs-conn-legend__heading">Nodes</h2>
        <ul className="obs-conn-legend__list" aria-label="Node types">
          {nodeEntries.map(({ typeId, label, count }) => {
            const col = nodeColour(typeId);
            return (
              <li
                key={typeId}
                className="obs-conn-legend__item"
                data-node-type={typeId}
                data-testid={`legend-node-${typeId}`}
              >
                <span
                  className="obs-conn-legend__node-swatch"
                  style={
                    {
                      '--obs-node-color': rgba(col, 0.9),
                      '--obs-node-bg': rgba(col, 0.14),
                    } as CSSProperties
                  }
                  aria-hidden="true"
                >
                  {nodeIconGlyph(typeId)}
                </span>
                <span className="obs-conn-legend__label">{label}</span>
                {count != null && count > 1 && (
                  <span className="obs-conn-legend__count">{count}</span>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </aside>
  );
}

function nodeLegendEntries(
  topology: Topology | null,
  registry: Registry | null,
): NodeLegendEntry[] {
  const registryLabels = new Map(registry?.types.map((type) => [type.id, type.label]) ?? []);
  const counts = new Map<string, number>();
  const orderedTypes: string[] = [];

  for (const node of topology?.nodes ?? []) {
    counts.set(node.typeId, (counts.get(node.typeId) ?? 0) + 1);
    if (!orderedTypes.includes(node.typeId)) orderedTypes.push(node.typeId);
  }

  const typeIds = orderedTypes.length > 0 ? orderedTypes : FALLBACK_NODE_TYPES;
  return typeIds.map((typeId) => ({
    typeId,
    label: registryLabels.get(typeId) ?? humanizeObservatoryText(typeId),
    count: counts.get(typeId),
  }));
}

function EdgeLine({ relation }: { relation: EdgeLegendKey }) {
  const y = 7;
  const base = { x1: 2, y1: y, x2: 40, y2: y };

  if (relation === 'run') {
    return (
      <g>
        <circle cx={9} cy={y} r={3} className="obs-conn-legend__edge--run-node" />
        <circle cx={33} cy={y} r={3} className="obs-conn-legend__edge--run-node" />
        <line x1={12} y1={y} x2={30} y2={y} className="obs-conn-legend__edge--run-line" />
      </g>
    );
  }

  if (isAnimatedRelation(relation)) {
    return (
      <line {...base} className={`obs-conn-legend__edge--${relation}`}>
        <animate
          attributeName="stroke-dashoffset"
          from="0"
          to={relation === 'signals_to' ? '-10' : '-12'}
          dur={relation === 'signals_to' ? '0.55s' : '0.9s'}
          repeatCount="indefinite"
        />
      </line>
    );
  }

  return <line {...base} className={`obs-conn-legend__edge--${relation}`} />;
}

function isAnimatedRelation(relation: EdgeLegendKey): boolean {
  return (
    relation === 'writes' ||
    relation === 'routes_to' ||
    relation === 'observes' ||
    relation === 'signals_to'
  );
}

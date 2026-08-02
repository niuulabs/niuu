import type { ReactNode } from 'react';
import type { Registry, Topology, TopologyNode } from '../../domain';
import { humanizeObservatoryText } from '../displayLabels';
import './Inspector.css';

export interface InspectorProps {
  node: TopologyNode | null;
  topology: Topology | null;
  registry: Registry | null;
  onNodeSelect?: (node: TopologyNode) => void;
  /** Composed in by the page — anything needing a service, such as the A2A card. */
  footer?: ReactNode;
}

/** Placement as the mockup writes it: `cluster · realm`, lowercased. */
function placementOf(node: TopologyNode): string {
  return [node.cluster, node.realm]
    .filter((part): part is string => typeof part === 'string' && part.length > 0)
    .map((part) => part.toLowerCase())
    .join(' · ');
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="obs-insp__block">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

/** Definition rows. Absent values render as an em dash, never as blank. */
function KeyValues({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="obs-insp__kv">
      {rows.map(([key, value]) => (
        <div key={key} className="obs-insp__kvrow">
          <dt>{key}</dt>
          <dd>{value || '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Peer / relation list: name over placement, kind on the right. */
function NodeList({
  nodes,
  emptyText,
  onNodeSelect,
  testId,
}: {
  nodes: TopologyNode[];
  emptyText: string;
  onNodeSelect?: (node: TopologyNode) => void;
  testId: string;
}) {
  if (nodes.length === 0) {
    return <p className="obs-insp__empty">{emptyText}</p>;
  }
  return (
    <ul className="obs-insp__wl" data-testid={testId}>
      {nodes.map((node) => (
        <li key={node.id}>
          <button
            type="button"
            onClick={() => onNodeSelect?.(node)}
            data-testid={`insp-peer-${node.id}`}
          >
            <span>
              {humanizeObservatoryText(node.label)}
              <br />
              <span className="obs-insp__ns">{placementOf(node) || node.typeId}</span>
            </span>
          </button>
          <span className="obs-insp__rep">{node.typeId}</span>
        </li>
      ))}
    </ul>
  );
}

/** Detail rows built from what the adapters actually attached to the node. */
function detailRows(node: TopologyNode): [string, string][] {
  const extra = node as unknown as Record<string, unknown>;
  const text = (key: string): string => {
    const value = extra[key];
    if (typeof value === 'string') return value;
    if (typeof value === 'number') return value.toLocaleString();
    return '';
  };
  const rows: [string, string][] = [
    ['type', node.typeId],
    ['status', node.status],
  ];
  for (const [label, key] of [
    ['workload', 'workload'],
    ['namespace', 'namespace'],
    ['host', 'hostId'],
    ['engine', 'engine'],
    ['persona', 'persona'],
    ['model', 'model'],
    ['deployment', 'deployment'],
    ['provider', 'provider'],
    ['location', 'location'],
    ['pages', 'pages'],
    ['cores', 'cores'],
    ['ram', 'ram'],
    ['gpu', 'gpu'],
  ] as const) {
    const value = text(key);
    if (value) rows.push([label, value]);
  }
  rows.push(['cluster', node.cluster ?? '']);
  rows.push(['realm', node.realm || 'outside the realms']);
  return rows;
}

/**
 * Inspector — the right-hand column.
 *
 * Mirrors `docs/mockups/observatory/index.html`: an accent-tinted head with
 * kind / name / placement and state chips, then "What it is", "Detail", and
 * either the node's peers of the same kind or what it is connected to.
 *
 * Rows are built from fields the adapters actually attached, so a node that
 * carries no GPU or persona simply has no such row — rather than a row
 * asserting a blank.
 */
export function Inspector({ node, topology, registry, onNodeSelect, footer }: InspectorProps) {
  if (!node) {
    return (
      <div className="obs-insp obs-insp--empty" data-testid="inspector-empty">
        <p>Select an entity to inspect it.</p>
      </div>
    );
  }

  const entityType = registry?.types.find((type) => type.id === node.typeId);
  const peers = (topology?.nodes ?? []).filter(
    (candidate) => candidate.typeId === node.typeId && candidate.id !== node.id,
  );
  const connectedIds = new Set(
    (topology?.edges ?? [])
      .filter((edge) => edge.sourceId === node.id || edge.targetId === node.id)
      .map((edge) => (edge.sourceId === node.id ? edge.targetId : edge.sourceId)),
  );
  const connected = (topology?.nodes ?? []).filter((candidate) => connectedIds.has(candidate.id));

  return (
    <div className="obs-insp" data-testid="inspector">
      <header className="obs-insp__head">
        <div className="obs-insp__kind" data-testid="inspector-kind">
          {entityType?.label ?? node.typeId}
        </div>
        <h2>{humanizeObservatoryText(node.label)}</h2>
        <div className="obs-insp__sub">{placementOf(node) || '—'}</div>
        <div className="obs-insp__chips">
          <span className={`obs-insp__state obs-insp__state--${node.status}`}>{node.status}</span>
          {peers.length > 0 ? (
            <span className="obs-insp__state obs-insp__state--idle">1 of {peers.length + 1}</span>
          ) : null}
        </div>
      </header>

      {entityType?.description ? (
        <Block title="What it is">
          <p className="obs-insp__note">{entityType.description}</p>
        </Block>
      ) : null}

      <Block title="Detail">
        <KeyValues rows={detailRows(node)} />
      </Block>

      <Block title="Connected to">
        <NodeList
          nodes={connected}
          emptyText="Nothing yet."
          onNodeSelect={onNodeSelect}
          testId="inspector-connected"
        />
      </Block>

      {peers.length > 0 ? (
        <Block title={`The other ${peers.length}`}>
          <NodeList
            nodes={peers}
            emptyText="None."
            onNodeSelect={onNodeSelect}
            testId="inspector-peers"
          />
        </Block>
      ) : null}

      {footer ? <div className="obs-insp__footer">{footer}</div> : null}
    </div>
  );
}

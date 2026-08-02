import { useState, type ReactNode } from 'react';
import type { Registry, Topology, TopologyNode } from '../../domain';
import { deriveAgentMeshes } from '../../domain/agentMesh';
import { humanizeObservatoryText } from '../displayLabels';
import './Inspector.css';

export interface InspectorProps {
  node: TopologyNode | null;
  topology: Topology | null;
  registry: Registry | null;
  onNodeSelect?: (node: TopologyNode) => void;
  /**
   * Composed in by the page — anything needing a service, such as the A2A
   * card. Receives the selected tab so the card can render itself or its raw
   * JSON, as the mockup's segmented control does.
   */
  footer?: (mode: Exclude<CardMode, 'resident'>) => ReactNode;
}

/** Inspector tabs, after the mockup: the entity, its card, or the raw card. */
export type CardMode = 'resident' | 'card' | 'json';

const CARD_MODES: ReadonlyArray<[CardMode, string]> = [
  ['resident', 'Resident'],
  ['card', 'A2A card'],
  ['json', 'JSON'],
];

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
  relation,
}: {
  nodes: TopologyNode[];
  emptyText: string;
  onNodeSelect?: (node: TopologyNode) => void;
  testId: string;
  /** Right-hand tag. Defaults to the node's type. */
  relation?: (node: TopologyNode) => string;
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
          <span className="obs-insp__rep">{relation ? relation(node) : node.typeId}</span>
        </li>
      ))}
    </ul>
  );
}

function field(node: TopologyNode, key: string): string {
  const value = (node as unknown as Record<string, unknown>)[key];
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return value.toLocaleString();
  return '';
}

/**
 * The line above the name: kind, what it runs on, and where.
 *
 * One line answers "what am I looking at and where does it live", which was
 * previously three separate rows in a table further down the panel.
 */
export function eyebrowOf(node: TopologyNode, typeLabel?: string): string[] {
  const engine = field(node, 'engine') || field(node, 'runtime');
  return [
    typeLabel ?? node.typeId,
    engine ? `engine ${engine}` : '',
    node.cluster ?? '',
    node.realm ?? '',
  ].filter(Boolean);
}

/** Under the name: the model it thinks with, or what it is made of. */
export function headlineOf(node: TopologyNode): string {
  const uptime = field(node, 'uptime');
  return [
    field(node, 'model'),
    field(node, 'specialty'),
    field(node, 'hw'),
    uptime && `up ${uptime}`,
  ]
    .filter(Boolean)
    .join(' · ');
}

/** Chips beside the status: the counts worth carrying into the header. */
const HEADER_COUNTS: ReadonlyArray<[label: string, key: string]> = [
  ['learned tools', 'learnedTools'],
  ['queued', 'queue'],
  ['a2a', 'a2aTasks'],
];

export function headerChips(node: TopologyNode): string[] {
  const chips: string[] = [];
  for (const [label, key] of HEADER_COUNTS) {
    const value = (node as unknown as Record<string, unknown>)[key];
    if (typeof value === 'number' && value > 0) chips.push(`${value} ${label}`);
  }
  return chips;
}

/** What it is doing right now, when it reports it. */
export function activityOf(node: TopologyNode): string {
  return field(node, 'activity') || field(node, 'doing');
}

/**
 * How this thing is run — a short, fixed set.
 *
 * Deliberately not every field: the panel leads with a reading, and a reading
 * is five lines, not twenty.
 */
const RUNTIME_KEYS: ReadonlyArray<[label: string, key: string]> = [
  ['engine', 'engine'],
  ['model', 'model'],
  ['profile', 'profile'],
  ['deployment', 'deployment'],
  ['registered', 'registered'],
  ['persona', 'persona'],
  ['specialty', 'specialty'],
  ['state', 'state'],
  ['queue', 'queue'],
  ['a2a tasks', 'a2aTasks'],
  ['learned tools', 'learnedTools'],
  ['host', 'hostId'],
  ['workload', 'workload'],
  ['tokens', 'tokens'],
];

export function runtimeRows(node: TopologyNode): [string, string][] {
  const rows: [string, string][] = [];
  for (const [label, key] of RUNTIME_KEYS) {
    const value = field(node, key);
    if (value) rows.push([label, value]);
  }
  return rows;
}

/**
 * Fields the inspector shows somewhere else — in the head, the placement line,
 * or a dedicated block — so a Detail row would only repeat them.
 */
const SHOWN_ELSEWHERE: ReadonlySet<string> = new Set([
  'id',
  'typeId',
  'label',
  'parentId',
  'status',
  'cluster',
  'realm',
  'layoutHints',
  'children',
  'sub',
  // Said by the header or the Runtime block already.
  'engine',
  'runtime',
  'model',
  'specialty',
  'hw',
  'activity',
  'doing',
  'profile',
  'deployment',
  'persona',
  'hostId',
  'workload',
  'tokens',
]);

/**
 * Keys worth leading with, in the order an operator reads them: what the thing
 * is running as, then what it is made of, then what it costs.
 *
 * This is an ordering hint, not an allowlist. Anything else the adapters
 * attached follows in alphabetical order, so a field added by a discovery
 * adapter appears here without a change to this file — the same reason the
 * canvas takes its glyphs from the registry rather than a table.
 */
const DETAIL_ORDER: readonly string[] = [
  'workload',
  'warden',
  'service',
  'namespace',
  'chart',
  'deployment',
  'hostId',
  'host',
  'engine',
  'persona',
  'specialty',
  'model',
  'provider',
  'location',
  'store',
  'path',
  'pages',
  'categories',
  'mounts',
  'role',
  'cores',
  'ram',
  'gpu',
  'os',
  'reason',
];

/** Detail rows built from what the adapters actually attached to the node. */
function detailRows(node: TopologyNode): [string, string][] {
  const extra = node as unknown as Record<string, unknown>;

  const scalar = (value: unknown): string => {
    if (typeof value === 'string') return value;
    if (typeof value === 'number') return value.toLocaleString();
    if (typeof value === 'boolean') return value ? 'yes' : 'no';
    if (Array.isArray(value) && value.every((item) => typeof item === 'string')) {
      return value.join(' · ');
    }
    return '';
  };

  const present = Object.keys(extra).filter(
    (key) => !SHOWN_ELSEWHERE.has(key) && scalar(extra[key]) !== '',
  );
  const rank = (key: string) => {
    const index = DETAIL_ORDER.indexOf(key);
    return index === -1 ? DETAIL_ORDER.length : index;
  };
  present.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

  return present.map((key): [string, string] => [
    humanizeObservatoryText(key).toLowerCase(),
    scalar(extra[key]),
  ]);
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
  const [mode, setMode] = useState<CardMode>('resident');

  if (!node) {
    return (
      <div className="obs-insp obs-insp--empty" data-testid="inspector-empty">
        <p>Select an entity to inspect it.</p>
      </div>
    );
  }

  const entityType = registry?.types.find((type) => type.id === node.typeId);
  const connectedIds = new Set(
    (topology?.edges ?? [])
      .filter((edge) => edge.sourceId === node.id || edge.targetId === node.id)
      .map((edge) => (edge.sourceId === node.id ? edge.targetId : edge.sourceId)),
  );
  const connected = (topology?.nodes ?? []).filter((candidate) => connectedIds.has(candidate.id));

  // The mesh this node peers in, and who else is in it. Peers "of the same
  // type" was the wrong grouping — two residents sharing a type share nothing;
  // two sharing a mesh share their findings.
  const mesh = deriveAgentMeshes(topology).find((candidate) =>
    candidate.memberIds.includes(node.id),
  );
  const meshMembers = (topology?.nodes ?? []).filter(
    (candidate) => candidate.id !== node.id && mesh?.memberIds.includes(candidate.id),
  );

  return (
    <div className="obs-insp" data-testid="inspector">
      <header className="obs-insp__head">
        {/*
          The eyebrow places the entity in one line — kind, what it runs on,
          and where — so the reader never has to go down to a table to answer
          "what am I looking at and where does it live".
        */}
        <div className="obs-insp__kind" data-testid="inspector-kind">
          {eyebrowOf(node, entityType?.label).join(' · ')}
        </div>
        <h2>{humanizeObservatoryText(node.label)}</h2>
        {headlineOf(node) ? <div className="obs-insp__sub">{headlineOf(node)}</div> : null}
        <div className="obs-insp__chips">
          <span className={`obs-insp__state obs-insp__state--${node.status}`}>{node.status}</span>
          {headerChips(node).map((chip) => (
            <span key={chip} className="obs-insp__state obs-insp__state--idle">
              {chip}
            </span>
          ))}
          {mesh ? (
            <span className="obs-insp__state obs-insp__state--idle">
              {mesh.memberIds.length} in mesh
            </span>
          ) : null}
        </div>
      </header>

      {footer ? (
        <div className="obs-insp__seg" role="tablist" aria-label="Inspector view">
          {CARD_MODES.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              className="obs-insp__segbtn"
              aria-selected={mode === value}
              data-testid={`insp-tab-${value}`}
              onClick={() => setMode(value)}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {mode !== 'resident' && footer ? (
        <div className="obs-insp__footer">{footer(mode)}</div>
      ) : null}

      {mode === 'resident' ? (
        <>
          {activityOf(node) ? (
            <Block title="Doing now">
              <p className="obs-insp__note" data-testid="inspector-activity">
                {activityOf(node)}
              </p>
            </Block>
          ) : null}

          {/* The handful of fields that say how this thing is run. */}
          {runtimeRows(node).length > 0 ? (
            <Block title="Runtime">
              <KeyValues rows={runtimeRows(node)} />
            </Block>
          ) : null}

          {mesh ? (
            <Block title={`Mesh · ${mesh.id}`}>
              <p className="obs-insp__note">
                {mesh.memberIds.length} residents peer directly, so a finding by one becomes
                evidence for all.
              </p>
              <NodeList
                nodes={meshMembers}
                emptyText="No other members placed."
                onNodeSelect={onNodeSelect}
                testId="inspector-mesh"
                relation={() => 'same mesh'}
              />
            </Block>
          ) : null}

          <Block title="Connected to">
            <NodeList
              nodes={connected}
              emptyText="Nothing yet."
              onNodeSelect={onNodeSelect}
              testId="inspector-connected"
            />
          </Block>

          {/*
            Everything else the adapters attached. It comes last because it is
            a reference, not a reading — leading with it made the panel a table
            with a name on top.
          */}
          {detailRows(node).length > 0 ? (
            <Block title="Detail">
              <KeyValues rows={detailRows(node)} />
            </Block>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

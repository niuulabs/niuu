/**
 * Observatory domain — pure value objects describing the live topology and
 * entity-type registry. No framework imports.
 *
 * Shared cross-plugin types (`EntityShape`, `EntityCategory`, `TypeRegistry`)
 * are re-exported verbatim from `@niuulabs/domain`. Observatory-specific
 * rendering + editor fields are layered on top of the canonical `EntityType`
 * via extension — no local copies.
 */

// ── Base types re-exported from @niuulabs/domain ──────────────────────────────
// Import the domain bases we extend locally.
import type { EntityType as BaseEntityType, TypeRegistry } from '@niuulabs/domain';

export type {
  AgentKind,
  AgentInterface,
  AgentProvenance,
  AgentDirectoryEntry,
  AgentDirectoryWarning,
  AgentDirectorySourceHealth,
  AgentDirectorySourceStatus,
  AgentDirectoryPage,
  AgentDirectoryFilters,
} from './agentDirectory';
export { findAgentTopologyNode } from './agentDirectory';
export type { AgentMesh } from './agentMesh';
export { deriveAgentMeshes, findMeshForNode, isMeshMember } from './agentMesh';
export type { EdgeLayer } from './edgeLayer';
export {
  EDGE_LAYERS,
  EDGE_LAYER_LABELS,
  edgeLayer,
  visibleEdges,
  countEdgesByLayer,
} from './edgeLayer';

// Re-export shared primitives so consumers can import from a single package.
export type { EntityShape, EntityCategory, TypeRegistry } from '@niuulabs/domain';

// ── Observatory-specific field definition ─────────────────────────────────────

/** A configurable field descriptor on an EntityType used by the registry editor. */
export interface EntityTypeField {
  key: string;
  label: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'tags';
  required?: boolean;
  options?: string[];
}

/**
 * Observatory-extended entity type.
 * Inherits `id`, `label`, `category` (typed enum), `rune`, `shape`, `color`,
 * `description`, `parentTypes`, `canContain` from `@niuulabs/domain`
 * `EntityType` and adds observatory rendering + registry-editor fields.
 */
export interface EntityType extends BaseEntityType {
  icon: string;
  size: number;
  border: 'solid' | 'dashed';
  fields: EntityTypeField[];
}

/**
 * Versioned registry of observatory-extended entity types.
 * Structurally compatible with `TypeRegistry` from `@niuulabs/domain` but
 * narrows `types` to the richer `EntityType` extension.
 */
export interface Registry extends Omit<TypeRegistry, 'types'> {
  types: EntityType[];
}

// ── Topology graph ────────────────────────────────────────────────────────────

/** The 5 connection styles in the topology edge taxonomy. */
export type EdgeKind = 'solid' | 'dashed-anim' | 'dashed-long' | 'soft' | 'run';

/** Semantic relationship represented by a visible topology edge. */
export type EdgeRelationType =
  | 'contains'
  | 'manages'
  | 'uses'
  | 'reads'
  | 'writes'
  | 'routes_to'
  | 'exposes'
  | 'observes'
  | 'signals_to'
  | 'member_of';

/** Strength/source of evidence for a relationship edge. */
export type EdgeConfidence = 'declared' | 'observed' | 'inferred';

/** Higher-level layout strategy hint attached to a node or snapshot. */
export type LayoutMode = 'manual' | 'orbit' | 'pack' | 'force' | 'hybrid';

/** Scope at which a layout hint should be interpreted. */
export type LayoutScope = 'world' | 'realm' | 'cluster' | 'group' | 'node';

/** Optional fixed or weighted anchor point. */
export interface LayoutAnchor {
  x: number;
  y: number;
  pinned?: boolean;
  weight?: number;
}

/** Optional layout guidance emitted by Valkyrie, services, or operators. */
export interface LayoutHints {
  mode?: LayoutMode;
  scope?: LayoutScope;
  anchor?: LayoutAnchor;
  order?: number;
  ring?: number;
  radius?: number;
  packGroup?: string;
  clusterRole?: string;
  axisLock?: Array<'x' | 'y'>;
  note?: string;
}

/** Runtime health status of a topology node. */
export type NodeStatus = 'healthy' | 'degraded' | 'failed' | 'idle' | 'observing' | 'unknown';

/** Activity state of an agent/entity. */
export type NodeActivity =
  'idle' | 'thinking' | 'tooling' | 'waiting' | 'delegating' | 'writing' | 'reading';

/**
 * An instance of an EntityType in the live topology graph.
 * Base fields are always present; kind-specific fields are optional and
 * populated according to the node's `typeId`.
 */
export interface TopologyNode {
  id: string;
  typeId: string;
  label: string;
  parentId: string | null;
  status: NodeStatus;

  // ── Universal optional fields ────────────────────────────────────────────
  activity?: NodeActivity;
  zone?: string;
  cluster?: string | null;
  hostId?: string | null;
  flockId?: string | null;
  sourceId?: string;
  sourceKind?: string;
  layoutHints?: LayoutHints;

  // ── ting ──────────────────────────────────────────────────────────────────
  mode?: string;
  activeSagas?: number;
  pendingRuns?: number;

  // ── bifrost ───────────────────────────────────────────────────────────────
  providers?: string[];
  reqPerMin?: number;
  cacheHitRate?: number;

  // ── volundr ───────────────────────────────────────────────────────────────
  activeSessions?: number;
  maxSessions?: number;

  // ── mimir ─────────────────────────────────────────────────────────────────
  pages?: number;
  writes?: number;
  mountCount?: number;
  mounts?: string[];

  // ── ravn_long ─────────────────────────────────────────────────────────────
  persona?: string;
  specialty?: string;
  tokens?: number;

  // ── host ──────────────────────────────────────────────────────────────────
  hw?: string;
  os?: string;
  cores?: number;
  ram?: string;
  gpu?: string | null;

  // ── model ─────────────────────────────────────────────────────────────────
  provider?: string;
  location?: string;

  // ── realm ─────────────────────────────────────────────────────────────────
  vlan?: number;
  dns?: string;
  purpose?: string;

  // ── run ──────────────────────────────────────────────────────────────────
  state?: string;

  // ── ravn_run / coord ─────────────────────────────────────────────────────
  role?: string;
  confidence?: number;

  // ── valkyrie ──────────────────────────────────────────────────────────────
  autonomy?: string;

  // ── service ───────────────────────────────────────────────────────────────
  svcType?: string;

  // ── printer ───────────────────────────────────────────────────────────────
  model?: string;

  // ── vaettir ───────────────────────────────────────────────────────────────
  sensors?: string;
}

/** Typed aliases for the four named domain entities. */
export type Realm = TopologyNode & { typeId: 'realm' };
export type Cluster = TopologyNode & { typeId: 'cluster' };
export type Host = TopologyNode & { typeId: 'host' };
export type Run = TopologyNode & { typeId: 'run' };

/** A directional connection between two topology nodes. */
export interface TopologyEdge {
  id: string;
  sourceId: string;
  targetId: string;
  kind: EdgeKind;
  label?: string;
  relationType?: EdgeRelationType;
  confidence?: EdgeConfidence;
  evidence?: Record<string, string>;
}

/** How a source's fragment reached the aggregator. */
export type SourceTransport = 'pull' | 'push' | 'local' | 'static';

/**
 * Reachability of one contributing source.
 *
 * `stale` is distinct from `failed`: the source was reachable and simply has
 * not reported within its TTL — a different operator situation from one that
 * cannot be reached at all.
 */
export type SourceStatus = 'healthy' | 'degraded' | 'stale' | 'failed';

/** Per-source health reported alongside the merged graph. */
export interface TopologySourceHealth {
  sourceId: string;
  sourceKind?: string;
  sourceName?: string;
  transport?: SourceTransport;
  status: SourceStatus;
  clusterId?: string;
  realmId?: string;
  revision?: string;
  nodeCount?: number;
  lastSeen?: string;
  message?: string;
}

/** A degradation that did not stop the snapshot being served. */
export interface TopologyWarning {
  sourceId?: string;
  code?: string;
  message?: string;
}

/**
 * Point-in-time snapshot of the live topology graph.
 *
 * When the snapshot comes from the Guild aggregate it also reports how
 * complete it is. That matters: without `sources` and `partial`, an estate
 * where half the clusters are unreachable looks exactly like a small estate.
 */
export interface Topology {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  timestamp: string;
  layoutHints?: LayoutHints;
  revision?: string;
  sources?: TopologySourceHealth[];
  warnings?: TopologyWarning[];
  partial?: boolean;
}

// ── Event log ─────────────────────────────────────────────────────────────────

/** Source subsystem that generated an observatory event (matches web2 type column). */
export type ObservatoryEventType = 'RUN' | 'RAVN' | 'TING' | 'MIMIR' | 'BIFROST';

/**
 * A single entry in the observatory event log.
 * Shaped to match the web2 prototype event format:
 *   4-column grid — time, type, subject, body.
 */
export interface ObservatoryEvent {
  id: string;
  /** HH:MM:SS — display time extracted from ISO timestamp at emit time. */
  time: string;
  type: ObservatoryEventType;
  subject: string;
  body: string;
  /**
   * Severity as the producer reported it. Discovery adapters emit `warning`
   * when they cannot reach a service, which is the difference between
   * "nothing is deployed" and "we cannot see" — worth keeping visible.
   */
  level?: 'info' | 'warning';
}

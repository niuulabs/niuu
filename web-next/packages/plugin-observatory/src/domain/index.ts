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
export type EdgeKind = 'solid' | 'dashed-anim' | 'dashed-long' | 'soft' | 'raid';

/** Runtime health status of a topology node. */
export type NodeStatus = 'healthy' | 'degraded' | 'failed' | 'idle' | 'observing' | 'unknown';

/** Activity state of an agent/entity. */
export type NodeActivity =
  | 'idle'
  | 'thinking'
  | 'tooling'
  | 'waiting'
  | 'delegating'
  | 'writing'
  | 'reading';

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

  // ── tyr ──────────────────────────────────────────────────────────────────
  mode?: string;
  activeSagas?: number;
  pendingRaids?: number;

  // ── bifrost ───────────────────────────────────────────────────────────────
  providers?: string[];
  reqPerMin?: number;
  cacheHitRate?: number;

  // ── volundr ───────────────────────────────────────────────────────────────
  activeSessions?: number;
  maxSessions?: number;

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

  // ── raid ──────────────────────────────────────────────────────────────────
  state?: string;

  // ── ravn_raid / coord ─────────────────────────────────────────────────────
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
export type Raid = TopologyNode & { typeId: 'raid' };

/** A directional connection between two topology nodes. */
export interface TopologyEdge {
  id: string;
  sourceId: string;
  targetId: string;
  kind: EdgeKind;
}

/** Point-in-time snapshot of the live topology graph. */
export interface Topology {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  timestamp: string;
}

// ── Event log ─────────────────────────────────────────────────────────────────

/** Source subsystem that generated an observatory event (matches web2 type column). */
export type ObservatoryEventType = 'RAID' | 'RAVN' | 'TYR' | 'MIMIR' | 'BIFROST';

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
}

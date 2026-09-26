/**
 * How the memory view groups page kinds for colour and the legend.
 *
 * A graph node's `kind` is the page's entity type when it has one, else its
 * page type, else `thread` or `page` (see the backend's `project_pages`). The
 * legend shows groups rather than raw kinds so six entity sub-types read as
 * one colour.
 */

export type KindGroup =
  | 'topic'
  | 'entity'
  | 'decision'
  | 'directive'
  | 'preference'
  | 'goal'
  | 'observation'
  | 'thread'
  | 'page';

export interface KindGroupInfo {
  id: KindGroup;
  /** Plural legend label. */
  label: string;
  /** Singular label for a single page ("topic · Platform"). */
  singular: string;
}

/** Legend order. */
export const KIND_GROUPS: readonly KindGroupInfo[] = [
  { id: 'topic', label: 'Topics', singular: 'topic' },
  { id: 'entity', label: 'Entities', singular: 'entity' },
  { id: 'decision', label: 'Decisions', singular: 'decision' },
  { id: 'directive', label: 'Directives', singular: 'directive' },
  { id: 'preference', label: 'Preferences', singular: 'preference' },
  { id: 'goal', label: 'Goals', singular: 'goal' },
  { id: 'observation', label: 'Observations', singular: 'observation' },
  { id: 'thread', label: 'Threads', singular: 'thread' },
  { id: 'page', label: 'Untyped pages', singular: 'page' },
];

const ENTITY_KINDS = new Set([
  'entity',
  'person',
  'project',
  'concept',
  'technology',
  'organization',
  'strategy',
]);

const DIRECT_GROUPS = new Set<KindGroup>([
  'topic',
  'decision',
  'directive',
  'preference',
  'goal',
  'observation',
  'thread',
]);

/** The legend group a graph node's `kind` belongs to. Unknown kinds are untyped pages. */
export function kindGroup(kind: string | undefined): KindGroup {
  if (!kind) return 'page';
  const normalised = kind.toLowerCase();
  if (ENTITY_KINDS.has(normalised)) return 'entity';
  if (DIRECT_GROUPS.has(normalised as KindGroup)) return normalised as KindGroup;
  return 'page';
}

/** The singular label for a node's kind ("technology" stays "technology"). */
export function kindLabel(kind: string | undefined): string {
  return kind ? kind.toLowerCase() : 'page';
}

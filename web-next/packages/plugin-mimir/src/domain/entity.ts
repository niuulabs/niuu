/**
 * Mímir entity domain — typed entity pages.
 *
 * Entities are a sub-type of pages that represent real-world objects: people,
 * organisations, concepts, projects, etc. Each entity has a kind that
 * determines which glyph and grouping the UI uses.
 */

export type EntityKind = 'person' | 'org' | 'concept' | 'project' | 'component' | 'technology';

export const ENTITY_KINDS: EntityKind[] = [
  'person',
  'org',
  'concept',
  'project',
  'component',
  'technology',
];

export interface EntityMeta {
  /** Page path — same as the underlying Page.path. */
  path: string;
  title: string;
  entityKind: EntityKind;
  summary: string;
  /** Number of explicit relationship links from this entity's Relationships zone. */
  relationshipCount: number;
}

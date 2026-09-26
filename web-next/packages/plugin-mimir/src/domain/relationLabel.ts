/**
 * How a typed graph edge reads to a person: `depends_on` → "depends on".
 *
 * Structural links — a plain wikilink, a generic link, "cites the same
 * source", "mentions this entity" — carry no verb worth showing, so they have
 * no label at all rather than a misleading one.
 */

const UNTYPED_RELATIONS = new Set(['wikilink', 'link', 'shared_source', 'related_entity']);

/** Relations that record a disagreement between two pages. */
const CONTRADICTION_RELATIONS = new Set(['contradicts', 'disagrees_with', 'conflicts_with']);

/** The label for an edge type, or null for an untyped or missing one. */
export function relationLabel(type: string | null | undefined): string | null {
  if (!type || UNTYPED_RELATIONS.has(type)) return null;
  return type.replace(/_/g, ' ');
}

/** True for the relations that mean two pages disagree. */
export function isContradictionRelation(type: string | null | undefined): boolean {
  return Boolean(type && CONTRADICTION_RELATIONS.has(type));
}

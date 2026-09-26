/**
 * Humanise a graph edge's `type` into the label shown on a lit edge in the
 * scene (and reused by the surrounding panel UI for the same edges).
 *
 * Untyped/structural relations — a plain wikilink, a generic link, "these
 * two pages cite the same source", or "this mentions that entity" — carry
 * no interesting verb, so they render as null: no label rather than a
 * misleading one.
 */

/** Edge types that carry no human-readable relation — the scene draws them unlabelled. */
const UNTYPED_KINDS = new Set(['wikilink', 'link', 'shared_source', 'related_entity']);

/** Contradiction relations — dashed amber in the scene, still labelled. */
const CONTRADICTION_LABELS: Record<string, string> = {
  contradicts: 'contradicts',
  disagrees_with: 'disagrees with',
  conflicts_with: 'conflicts with',
};

const KNOWN_LABELS: Record<string, string> = {
  depends_on: 'depends on',
  part_of: 'part of',
  explains: 'explains',
  fixed_by: 'fixed by',
  routes_for: 'routes for',
  ...CONTRADICTION_LABELS,
};

/** True for the contradiction family — used by the scene to dash+amber an edge. */
export function isContradictionRelation(type: string | undefined | null): boolean {
  return Boolean(type && type in CONTRADICTION_LABELS);
}

/**
 * Humanise an edge type for display. Returns null for untyped/structural
 * relations and for a missing type, so callers can decide not to draw a
 * label at all rather than drawing an empty one.
 */
export function relationLabel(type: string | undefined | null): string | null {
  if (!type) return null;
  if (UNTYPED_KINDS.has(type)) return null;
  const known = KNOWN_LABELS[type];
  if (known) return known;
  // Fallback for any relation not in the known table: snake_case -> words.
  return type.replace(/_/g, ' ');
}

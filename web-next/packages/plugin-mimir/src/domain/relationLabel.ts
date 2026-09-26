/** Humanises `RelatedPage.rel` for the Focus panel's "Links" list, e.g. `depends_on` → "depends on". */

const RELATION_LABELS: Record<string, string> = {
  depends_on: 'depends on',
  part_of: 'part of',
  explains: 'explains',
  fixed_by: 'fixed by',
  shapes: 'shapes',
  routes_for: 'routes for',
  same_failure: 'same failure',
  caused_by: 'caused by',
  blocks: 'blocks',
  supersedes: 'supersedes',
};

/** Humanise a typed relation. Returns null for a plain (untyped) link. */
export function relationLabel(rel: string | null): string | null {
  if (!rel) return null;
  return RELATION_LABELS[rel] ?? rel.replace(/_/g, ' ');
}

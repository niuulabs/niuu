/** Category labels stay data-driven; color assignments stay stable across corpora. */
const PALETTE = [
  'lavender',
  'blue',
  'mint',
  'sand',
  'periwinkle',
  'sage',
  'aqua',
  'lilac',
] as const;
const CATEGORY_COLORS: Record<string, (typeof PALETTE)[number]> = {
  research: 'lavender',
  concepts: 'blue',
  notes: 'mint',
  projects: 'sand',
  skills: 'periwinkle',
  decisions: 'sage',
};

export function categoryColor(category: string): string {
  const name = category.trim().toLowerCase();
  let hash = 2166136261;
  for (const character of name) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  const color = Object.hasOwn(CATEGORY_COLORS, name)
    ? CATEGORY_COLORS[name]
    : PALETTE[(hash >>> 0) % PALETTE.length];
  return `var(--knowledge-${color})`;
}

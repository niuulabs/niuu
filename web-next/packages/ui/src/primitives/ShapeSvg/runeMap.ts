/**
 * Canonical rune glyph map for the Niuu design system.
 *
 * Sources:
 *   - SERVICE_RUNES: DS_RUNES from the Flokk Observatory prototype (brand-identity glyphs)
 *   - ENTITY_RUNES: rune fields from DEFAULT_REGISTRY entity-type definitions
 *
 * Forbidden runes (appropriated hate symbols) are explicitly excluded:
 *   ᛟ (Othala)  ᛊ (Sowilo)  ᛏ (Tiwaz)  ᛉ (Algiz)  ᚺ/ᚻ (Hagalaz variants)
 */

/** Service / persona-identity glyphs keyed by system name */
export const SERVICE_RUNES = {
  volundr: 'ᚲ',
  tyr: 'ᛃ',
  ravn: 'ᚱ',
  mimir: 'ᛗ',
  bifrost: 'ᚨ',
  sleipnir: 'ᛖ',
  buri: 'ᛜ',
  hlidskjalf: 'ᛞ',
  flokk: 'ᚠ',
  skuld: 'ᚾ',
  valkyrie: 'ᛒ',
} as const satisfies Record<string, string>;

/** Entity-kind runes keyed by registry type id */
export const ENTITY_RUNES = {
  realm: 'ᛞ',
  cluster: 'ᚲ',
  host: 'ᚦ',
  ravn_long: 'ᚱ',
  ravn_raid: 'ᚲ',
  skuld: 'ᛜ',
  valkyrie: 'ᛒ',
  tyr: 'ᛃ',
  bifrost: 'ᚨ',
  volundr: 'ᚲ',
  mimir: 'ᛗ',
  mimir_sub: 'ᛗ',
  service: 'ᛦ',
  model: 'ᛖ',
  printer: 'ᛈ',
  vaettir: 'ᚹ',
  beacon: 'ᚠ',
  raid: 'ᚷ',
} as const satisfies Record<string, string>;

/**
 * Combined canonical rune map.
 * Entity-kind runes are the base; service-identity runes are merged on top.
 * Use SERVICE_RUNES directly when you need the brand-tagged service identity.
 */
export const RUNE_MAP = {
  ...ENTITY_RUNES,
  ...SERVICE_RUNES,
} as const;

export type RuneKey = keyof typeof RUNE_MAP;

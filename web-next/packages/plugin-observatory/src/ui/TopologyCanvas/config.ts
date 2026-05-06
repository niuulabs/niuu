/**
 * Canvas layout and rendering constants.
 * All numeric values that affect visual layout live here — never inline.
 */

export const CANVAS = {
  /** World-space dimensions. */
  WORLD_W: 4200,
  WORLD_H: 3600,

  /** Zoom limits (task spec: 0.3×–3×). */
  ZOOM_MIN: 0.3,
  ZOOM_MAX: 3.0,

  /** Multiplicative zoom step per scroll tick. */
  ZOOM_STEP: 1.15,

  /** Camera starts here so Mímir is centred on screen. */
  INITIAL_ZOOM: 0.5,

  /** World units panned per arrow-key press. */
  PAN_KEY_STEP: 80,

  /** Minimap dimensions in screen pixels. */
  MINIMAP_W: 220,
  MINIMAP_H: 165,
} as const;

export const LAYOUT = {
  /** Distance from origin to the centre of each realm circle. */
  REALM_RING_RADIUS: 700,

  /** Visual radius drawn for realm circles. */
  REALM_INNER_RADIUS: 240,

  /** Distance from a realm centre to a cluster centre inside it. */
  CLUSTER_RING_DIST: 120,

  /** Visual radius drawn for cluster circles. */
  CLUSTER_INNER_RADIUS: 90,

  /** Distance from a realm centre to a host rounded-rect. */
  HOST_RING_DIST: 200,

  /** Orbit radius for sub-Mímir nodes around the primary Mímir. */
  SUB_MIMIR_RING: 160,

  /** Radial scatter applied when placing generic nodes near a parent. */
  NODE_SCATTER_DIST: 60,

  /** Pixel radius of the Mímir glyph circle. */
  MIMIR_RADIUS: 42,
} as const;

/** Per-typeId hit radius for click / hover detection (world units). */
export const HIT_RADIUS: Record<string, number> = {
  mimir: 42,
  tyr: 18,
  bifrost: 16,
  volundr: 16,
  valkyrie: 12,
  ravn_long: 12,
  ravn_raid: 9,
  skuld: 9,
  host: 24,
  service: 7,
  model: 7,
  printer: 9,
  vaettir: 9,
  beacon: 6,
  raid: 50,
};

/** Per-typeId visual size (radius / half-side) for rendering. */
export const NODE_SIZE: Record<string, number> = {
  mimir: 42,
  tyr: 11,
  bifrost: 10,
  volundr: 13,
  valkyrie: 10,
  ravn_long: 9,
  ravn_raid: 6,
  skuld: 7,
  host: 8,
  service: 4,
  model: 5,
  printer: 7,
  vaettir: 7,
  beacon: 4,
  mimir_sub: 18,
};

/**
 * Orbiting runes displayed around the Mímir glyph.
 * Hate-symbol exclusion list: Othala ᛟ, Sowilo ᛊ, Tiwaz ᛏ, Algiz ᛉ,
 * Hagalaz ᚺ/ᚻ — see ADL hate symbol database.
 */
export const MIMIR_RUNES = [
  'ᚠ',
  'ᚢ',
  'ᚦ',
  'ᚨ',
  'ᚱ',
  'ᚲ',
  'ᚷ',
  'ᚹ',
  'ᚾ',
  'ᛁ',
  'ᛃ',
  'ᛈ',
  'ᛒ',
  'ᛖ',
  'ᛗ',
  'ᛚ',
  'ᛜ',
  'ᛞ',
] as const;

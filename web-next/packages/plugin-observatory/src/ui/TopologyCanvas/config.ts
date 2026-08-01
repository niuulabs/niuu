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
  REALM_RING_RADIUS: 920,

  /** Extra outward spacing when many realms need multiple bands. */
  REALM_RING_STEP: 340,

  /** Visual radius drawn for realm circles. */
  REALM_INNER_RADIUS: 320,

  /** Base orbit for clusters inside a realm. */
  REALM_CLUSTER_ORBIT: 180,

  /** Extra orbit added for additional cluster bands inside a realm. */
  REALM_CLUSTER_STEP: 170,

  /** Base orbit for direct realm devices/services. */
  REALM_DEVICE_ORBIT: 126,

  /** Extra orbit added for additional realm device bands. */
  REALM_DEVICE_STEP: 120,

  /** Base orbit for hosts inside a realm. */
  REALM_HOST_ORBIT: 290,

  /** Extra orbit added for additional host bands. */
  REALM_HOST_STEP: 136,

  /** Visual radius drawn for cluster circles. */
  CLUSTER_INNER_RADIUS: 138,

  /** Minimum visual radius drawn for namespace containers. */
  NAMESPACE_INNER_RADIUS: 86,

  /** Base orbit for child nodes inside a cluster. */
  CLUSTER_CHILD_ORBIT: 132,

  /** Extra orbit added for additional cluster child bands. */
  CLUSTER_CHILD_STEP: 84,

  /** Stable orbit for the named core services inside a cluster. */
  CLUSTER_CORE_ORBIT: 148,

  /** Base orbit for wardens and ravens within a cluster. */
  CLUSTER_RAVEN_ORBIT: 248,

  /** Extra orbit added for additional raven bands. */
  CLUSTER_RAVEN_STEP: 70,

  /** Base orbit for runs within a cluster. */
  CLUSTER_RUN_ORBIT: 274,

  /** Extra orbit added for additional run bands. */
  CLUSTER_RUN_STEP: 82,

  /** Base orbit for uncategorized cluster children. */
  CLUSTER_GENERIC_ORBIT: 212,

  /** Extra orbit added for additional generic cluster child bands. */
  CLUSTER_GENERIC_STEP: 72,

  /** Base orbit for child nodes around a host. */
  HOST_CHILD_ORBIT: 86,

  /** Extra orbit added for additional host child bands. */
  HOST_CHILD_STEP: 56,

  /** Base orbit for child nodes around a run. */
  RUN_CHILD_ORBIT: 72,

  /** Extra orbit added for additional run child bands. */
  RUN_CHILD_STEP: 52,

  /** Radial scatter applied when placing generic nodes near a parent. */
  NODE_SCATTER_DIST: 96,

  /** Extra scatter added for additional generic-node bands. */
  NODE_SCATTER_STEP: 64,

  /** Pixel radius of the Mímir glyph circle. */
  MIMIR_RADIUS: 42,
} as const;

/**
 * Level-of-detail thresholds.
 *
 * Labels are drawn at a constant *screen* size (see `worldFontSize`), so at low
 * zoom a label occupies far more world space than the gap between the nodes it
 * sits beside. Children on a cluster orbit are ~150–220 world units apart while
 * a label is ~70–110px wide, so below these zoom levels neighbouring labels
 * overlap by construction. Each tier is the zoom at which its labels start to
 * fit; anything hovered or selected ignores the tier entirely so nothing
 * becomes unreachable.
 */
export const LOD = {
  /** Stat line under a realm/cluster name. */
  CONTAINER_DETAIL: 0.3,
  /** Residents, Mímir instances and workflow sessions. */
  PRIMARY: 0.45,
  /** Services, hosts, models and run agents. */
  SECONDARY: 0.8,
  /** The secondary line beneath any node label. */
  NODE_DETAIL: 1.15,
} as const;

/** Label sizes in screen pixels — held constant regardless of camera zoom. */
export const LABEL_PX = {
  REALM: 13,
  CLUSTER: 12,
  CONTAINER_DETAIL: 9,
  PRIMARY: 11,
  SECONDARY: 10,
  NODE_DETAIL: 9,
} as const;

/** Per-typeId hit radius for click / hover detection (world units). */
export const HIT_RADIUS: Record<string, number> = {
  mimir: 42,
  ting: 18,
  bifrost: 16,
  volundr: 16,
  valkyrie: 12,
  ravn_long: 12,
  warden: 12,
  ravn_run: 9,
  skuld: 9,
  trigger: 9,
  gate: 11,
  cond: 11,
  stage: 14,
  end: 9,
  resource: 10,
  host: 24,
  namespace: 28,
  service: 7,
  model: 7,
  printer: 9,
  vaettir: 9,
  beacon: 6,
  run: 50,
};

/** Per-typeId visual size (radius / half-side) for rendering. */
export const NODE_SIZE: Record<string, number> = {
  mimir: 42,
  ting: 11,
  bifrost: 10,
  volundr: 13,
  valkyrie: 10,
  ravn_long: 9,
  warden: 10,
  ravn_run: 6,
  skuld: 7,
  trigger: 7,
  gate: 8,
  cond: 8,
  stage: 9,
  end: 7,
  resource: 8,
  host: 8,
  namespace: 8,
  service: 4,
  model: 5,
  printer: 7,
  vaettir: 7,
  beacon: 4,
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
  '✦',
  'ᛈ',
  'ᛒ',
  'ᛖ',
  'ᛗ',
  'ᛚ',
  'ᛜ',
  'ᛞ',
] as const;

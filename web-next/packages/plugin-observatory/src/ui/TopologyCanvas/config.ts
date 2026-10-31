/**
 * Canvas layout and rendering constants.
 * All numeric values that affect visual layout live here — never inline.
 */

export const CANVAS = {
  /**
   * The field the estate is drawn on: a radial wash, lit at the centre.
   *
   * Shared with the 3D stage rather than restated there. The two views are one
   * estate seen two ways, and nothing gives that away faster than the ground
   * changing colour when you switch — so there is one pair of values and both
   * views read it.
   */
  BACKDROP_CENTRE: '#0a0c12',
  BACKDROP_EDGE: '#050509',

  /** World-space dimensions. */
  WORLD_W: 4200,
  WORLD_H: 3600,

  /**
   * Zoom limits.
   *
   * The floor was 0.3×, which predates a topology of this size: eight clusters
   * across five realms do not fit on screen at 0.3×, so "fit to bounds" clamped
   * to the minimum and the whole graph could never be seen at once. The floor
   * has to be low enough that fitting the full world is reachable.
   */
  ZOOM_MIN: 0.12,
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

  /**
   * Padding between a realm's outermost content and its drawn rectangle.
   * Shared with the renderer's `realmBounds`, so the space the layout
   * reserves and the shape drawn into it cannot disagree.
   */
  REALM_HULL_PADDING: 78,

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
  /**
   * A cloud's lobes bulge outward from its nominal radius but leave the
   * corners empty, so its contents need more room than a circle of the same
   * radius would give them.
   */
  CLOUD_LOBE_HEADROOM: 1.35,
  CLOUD_MIN_RADIUS: 96,
  /** Where the vendor name sits, as a fraction of the cloud's radius. */
  CLOUD_LABEL_OFFSET: 0.92,

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

/**
 * Agent-mesh highlight.
 *
 * A mesh spans clusters by design, so its members have no shape in common —
 * a hull drawn around them enclosed everything that happened to sit between
 * them and read as a container the mesh does not have. Pulsing the members
 * themselves says "these, wherever they are" without claiming any region.
 */
export const MESH_PULSE = {
  /**
   * Standoff and travel are in SCREEN pixels, converted to world units per
   * frame. In world units the ring shrank with everything else, so at the
   * zoom where a mesh spanning four clusters is actually visible the pulse
   * was a couple of pixels wide and could not be seen at all.
   */
  STANDOFF_PX: 9,
  /** How far the ring travels outward over one cycle, in screen pixels. */
  TRAVEL_PX: 26,
  /** Milliseconds per pulse. Slow enough to read as breathing, not flashing. */
  PERIOD_MS: 1700,
  /** Ring alpha at the start of a cycle, fading to zero at the end. */
  PEAK_ALPHA: 1,
  /** A second ring runs half a cycle behind the first. */
  PHASE_OFFSET: 0.5,
  /** Dimming applied to the trailing ring. */
  TRAILING_DIM: 0.75,
  /** Steady halo under the rings so members stay marked mid-fade. */
  HALO_ALPHA: 0.5,
  /** A filled wash inside the halo, so a member reads as lit at any zoom. */
  GLOW_ALPHA: 0.18,
  LINE_WIDTH: 2.6,
  HALO_LINE_WIDTH: 3.4,
} as const;

/**
 * The hue that means "this thing is unwell".
 *
 * Defined once because it is read both by the status table and by the agent
 * glyph, and the two drifting apart would give one concept two colours. It is
 * deliberately not the mesh hue, which a selected mesh pulses in, so a
 * degraded node drawn in it read as a mesh member. Fuchsia is unused
 * elsewhere on the canvas and stays clear of the violet that means
 * metered-and-outside and the red that means failed.
 */
export const DEGRADED_COLOUR = [217, 70, 239] as const;

/**
 * Flow drawn along an edge that reports an observed rate.
 *
 * Rendered as a travelling dash on the edge's own path rather than dots
 * sampled along it: the paths are bundled curves, and anything that samples
 * them approximately drifts off the line exactly where edges are densest.
 *
 * Rate changes the spacing between marks, never their speed. More messages
 * should read as more traffic, not as traffic in a hurry.
 */
export const EDGE_FLOW = {
  /** Length of one travelling mark, in screen pixels. */
  DASH_PX: 5,
  /** Gap at saturation — the densest an edge is ever drawn. */
  MIN_GAP_PX: 30,
  /** Gap at the lowest measurable rate: one lonely mark on a long edge. */
  MAX_GAP_PX: 260,
  /** Travel speed, screen pixels per second. */
  SPEED_PX_PER_S: 38,
  /** Calls a minute at which spacing stops tightening. */
  SATURATION_PER_MINUTE: 30,
  /** Marks sit brighter than the line they run along. */
  ALPHA: 0.95,
  LINE_WIDTH: 2.4,
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
  mimir: 14,
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
  mimir: 11,
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

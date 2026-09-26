/**
 * Every timing value, threshold and count the memory scene uses, in one
 * place — nothing downstream hardcodes a magic number of its own.
 */

/** Orbit camera field of view, distance limits and easing. */
export const CAMERA3D = {
  FOV: 50,
  MIN_DISTANCE: 4,
  MAX_DISTANCE: 400,
  INITIAL_DISTANCE: 90,
  INITIAL_AZIMUTH: Math.PI / 4,
  INITIAL_POLAR: Math.PI / 3,
  POLAR_MIN: 0.08,
  POLAR_MAX: Math.PI - 0.08,
  /** Radians of orbit per pixel of drag. */
  ORBIT_PER_PX: 0.005,
  /** Extra headroom beyond the exact fit distance. */
  FIT_PADDING: 1.15,
  FOCUS_DISTANCE: 32,
  /** Per-frame interpolation factor for eased camera travel (0-1). */
  FOCUS_EASING: 0.12,
  /** Distance below which an eased camera move is considered arrived. */
  FOCUS_SETTLE_DISTANCE: 0.05,
} as const;

/** 2D view: same camera model, locked straight down with no rotation. */
export const CAMERA2D = {
  POLAR: 0.001,
  AZIMUTH: 0,
} as const;

/** Layout — clustering and force simulation. */
export const LAYOUT = {
  /** Radius of the circle mount centres are spread around. */
  MOUNT_RING_RADIUS: 60,
  /** Iterations of the repulsion/spring simulation. */
  ITERATIONS: 70,
  /** Spatial hash cell size, in world units — should be ~ the repulsion radius. */
  CELL_SIZE: 6,
  /** Repulsion strength between two nodes at unit distance. */
  REPULSION: 26,
  /** Spring constant pulling linked nodes together. */
  SPRING_STRENGTH: 0.02,
  /** Rest length of an edge spring. */
  SPRING_LENGTH: 5,
  /** Pull strength toward the node's mount centre. */
  MOUNT_PULL: 0.02,
  /** Per-iteration velocity damping (0-1). */
  DAMPING: 0.85,
  /** Maximum displacement per iteration, to keep the simulation stable. */
  MAX_STEP: 4,
  /** Vertical spread (z in the flattened 2D view; y in 3D) applied by mount. */
  DECK_HEIGHT: 10,
} as const;

/** Node sizing and appearance. */
export const NODE3D = {
  MIN_RADIUS: 0.35,
  MAX_RADIUS: 1.6,
  /** Degree at/above which a node reaches MAX_RADIUS. */
  DEGREE_FOR_MAX_RADIUS: 24,
  DISPUTE_RING_SCALE: 1.6,
  BORN_RING_SCALE: 2.2,
} as const;

/** How many of the highest-degree nodes get a permanent label. */
export const HUB_LABEL_COUNT = 24;

/** Replay: a node born within this many ms of `asOf` gets the "just born" ring. */
export const BORN_WINDOW_MS = 3 * 24 * 60 * 60 * 1000;

/** Screen-space picking radius, in CSS pixels. */
export const PICK_RADIUS_PX = 14;

/** Pointer movement, in CSS pixels, beyond which a press+release counts as a drag, not a click. */
export const CLICK_DRAG_THRESHOLD_PX = 5;

/** Label collision-avoidance box size, in CSS pixels (width, height). */
export const LABEL_BOX = { width: 96, height: 18 } as const;

/** Depth-fog range, in world units, applied in the 3D view. */
export const FOG = { NEAR: 40, FAR: 220 } as const;

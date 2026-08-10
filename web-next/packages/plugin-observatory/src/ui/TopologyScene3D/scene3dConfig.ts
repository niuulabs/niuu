/**
 * Constants for the navigable 3D view.
 *
 * Same rule as the 2D canvas: every number that decides what the view looks
 * like lives here, never inline. The two views deliberately share the *data*
 * (layout, styles, layer colours) and nothing else — a plan drawing and a
 * model of the same estate need different numbers to be legible.
 */

export const CAMERA3D = {
  FOV: 52,
  NEAR: 2,
  FAR: 60000,

  /** How close the eye may come to what it is looking at. */
  MIN_DISTANCE: 90,
  /** How far back it may pull. Past this the estate is a smudge. */
  MAX_DISTANCE: 14000,

  /** Multiplicative dolly per wheel tick or button press. */
  DOLLY_STEP: 1.14,

  /** Radians of orbit per pixel dragged. */
  ORBIT_PER_PX: 0.0052,

  /**
   * Polar limits, measured from straight up.
   *
   * The floor is stopped just short of horizontal: the realm plates are flat,
   * and an eye exactly level with them sees the estate edge-on as a line. The
   * zenith is stopped short too, because azimuth is undefined there and the
   * view spins under the cursor.
   */
  POLAR_MIN: 0.12,
  POLAR_MAX: Math.PI / 2 - 0.05,

  /** A raking three-quarter view — the tiers read as tiers from here. */
  INITIAL_POLAR: 0.98,
  INITIAL_AZIMUTH: -Math.PI * 0.28,
  INITIAL_DISTANCE: 2600,

  /**
   * Room left around the estate when framing the whole thing.
   *
   * Small, because the fit already solves for the worst corner of the box
   * exactly. A generous factor on top of an exact solve is how the estate
   * ended up occupying a third of the window with sky on every side.
   */
  FIT_PADDING: 1.04,

  /** Where the camera settles when it flies to a selection. */
  FOCUS_DISTANCE: 640,
  /** Fraction of the remaining travel closed each frame. */
  FOCUS_EASING: 0.12,
  /** Below this the camera has arrived and stops easing. */
  FOCUS_SETTLE_DISTANCE: 1.5,

  /**
   * The drift the camera falls into when nobody is touching it.
   *
   * An estate seen from a dead-still camera reads as a diagram of something
   * that has stopped. A slow turn keeps the depth legible — parallax is what
   * tells you which deck a thing is on — and it means the Observatory left on
   * a wall carries on saying something.
   *
   * Slow enough that it is never what you are watching: a full turn takes
   * about three minutes, and the first touch of a pointer or a key stops it.
   */
  IDLE_DELAY_MS: 5000,
  IDLE_AZIMUTH_PER_SECOND: 0.035,
  /** A gentle rise and fall on top of the turn, so it is not a turntable. */
  IDLE_POLAR_SWAY: 0.06,
  IDLE_SWAY_PERIOD_MS: 47000,

  /** Radians of orbit per arrow-key press. */
  KEY_ORBIT_STEP: 0.08,
  /** World units panned per shift+arrow press. */
  KEY_PAN_STEP: 110,
} as const;

/**
 * Height of each containment tier, in world units above the realm floor.
 *
 * This is the whole reason the 3D view exists: in plan, a host and the agents
 * on it are two marks the same distance from the eye, and containment has to
 * be inferred from a ring. Lifting each level of the chain onto its own deck
 * makes "runs on" a direction you can see from the side.
 */
export const TIER = {
  /** Realms and clouds — the ground the estate stands on. */
  FLOOR: 0,
  CLUSTER: 130,
  NAMESPACE: 250,
  /** Hosts and workflow sessions: things that are both a place and a thing. */
  HOST: 345,
  /** Everything that runs on something: agents, services, models, run steps. */
  LEAF: 455,
} as const;

export const ZONE3D = {
  /**
   * Clearance between a region's contents and the shell drawn around them.
   *
   * Per kind, and widening outward: a namespace hugging its hosts and a realm
   * hugging its clusters at the same distance would leave the two shells
   * almost touching, and an operator cannot tell which of two coincident
   * surfaces they are looking at.
   */
  VOLUME_PADDING: {
    realm: 70,
    cloud: 30,
    cluster: 40,
    namespace: 26,
  },

  /** Floor on any half-extent, so a region holding one small thing is still a region. */
  MIN_HALF_EXTENT: 46,

  /**
   * Clear air between two sibling regions.
   *
   * Shells that touch read as regions that overlap, which is a claim about the
   * estate that nothing in the data supports.
   */
  SIBLING_GAP: 44,

  /**
   * How far the plan may be opened out to make room for the shells.
   *
   * A sphere big enough to hold a cluster's four decks of contents is wider
   * than the plan's packing ever needed it to be, so the regions have to move
   * apart. Only the spacing between regions is stretched — what is packed
   * inside a host stays exactly as the plan drew it.
   */
  MAX_SPREAD: 6,
  SPREAD_PASSES: 8,

  /**
   * How far a corner bracket reaches along each half-extent.
   *
   * Short enough that the middle of every face stays clear for whatever is
   * standing inside the region, long enough that three of them still read as
   * a corner rather than as three unrelated ticks.
   */
  BRACKET_ARM: 0.26,

  /** Base strength of the wash a shell is drawn with. */
  REALM_FILL_ALPHA: 0,
  REALM_EDGE_ALPHA: 0.85,
  CLUSTER_FILL_ALPHA: 0.016,
  CLUSTER_EDGE_ALPHA: 0.3,
  NAMESPACE_FILL_ALPHA: 0,
  NAMESPACE_EDGE_ALPHA: 0.75,
  CLOUD_FILL_ALPHA: 0.03,
  CLOUD_EDGE_ALPHA: 0.32,

  /**
   * How much brighter a shell gets as it turns away from the eye.
   *
   * A sphere filled at a flat opacity is a flat disc — nothing about it says
   * which way its surface is facing, so it reads as a circle painted over the
   * estate rather than as a volume around it. Brightening toward the
   * silhouette, the way a soap bubble does, is what makes it round; it also
   * makes the outline legible without drawing a wireframe cage over everything
   * the region contains.
   */
  RIM_GAIN: 9,
  RIM_POWER: 5,
} as const;

/**
 * The faint vertical line from a node down to the deck its parent sits on.
 *
 * Without them the tiers float and nothing says which cluster a host belongs
 * to once the camera is off-axis. They are drawn barely-there on purpose:
 * one per node is several hundred lines, and they are scaffolding, not content.
 */
export const RISER = {
  ALPHA: 0.22,
  /** Stop short of the node itself so the line does not touch the glyph. */
  CLEARANCE: 4,
} as const;

export const EDGE3D = {
  /** Points sampled along each arc. Enough to read as a curve at any zoom. */
  SEGMENTS: 22,
  /** Rise at the apex, as a fraction of the edge's length. */
  BOW_RATIO: 0.19,
  BOW_MIN: 26,
  BOW_MAX: 420,
  /** Sideways spread per relation lane, so parallel relations separate. */
  LANE_OFFSET: 16,
  ALPHA: 0.42,
  /** Anything the operator is not tracing drops to this. */
  DIMMED_ALPHA: 0.07,
  /** Travelling motes on edges that report a measured rate. */
  FLOW_SPEED: 0.16,
  FLOW_SIZE: 9,
  FLOW_ALPHA: 0.95,
  /** Calls a minute at which an edge carries its full complement of motes. */
  FLOW_SATURATION_PER_MINUTE: 30,
  FLOW_MAX_MOTES: 5,
} as const;

export const NODE3D = {
  /** World units of glyph radius per unit of the 2D style radius. */
  SCALE: 1.7,
  /** Smallest a node may draw, so a 4-unit service is still a visible mark. */
  MIN_RADIUS: 9,

  /**
   * A node is a small, quiet mark.
   *
   * The instrument work belongs to the regions. Eighty entities each carrying
   * their own dials is a stage nobody can read — and it buries the one thing
   * the marks are for, which is seeing at a glance what kind of thing is
   * where, and which of them are not well.
   */
  MARK_ALPHA: 0.95,
  MARK_ALPHA_EMPHASISED: 1,
  /** How big the mark draws, as a multiple of the node's radius. */
  MARK_SCALE: 2.2,
  /** Additive glow behind each mark, as a multiple of its radius. */
  HALO_SCALE: 4.2,
  HALO_ALPHA: 0.12,
  HALO_ALPHA_EMPHASISED: 0.42,
  /** Everything the operator is not tracing steps back to here. */
  DIMMED_ALPHA: 0.24,
  /** A switched-off compute class goes further back than a mere dim. */
  FILTERED_ALPHA: 0.09,
  /** How solid a host or a session draws next to what it holds. */
  CONTAINER_OPACITY: 0.45,
  /**
   * How near you have to be for a node to be built rather than marked.
   *
   * The two readings cross-fade. Far off, an estate of eighty wireframe bodies
   * is a smear that hides everything it is drawn to show, so each node is a
   * single quiet mark. Close to, a mark says almost nothing, so the body
   * arrives — and with it whether that machine has accelerators in it, whether
   * that agent peers with others, what kind of thing you are actually looking
   * at.
   */
  BODY_NEAR: 1400,
  BODY_FAR: 3400,
  /** How brightly a body's edges and faces are drawn once it has arrived. */
  BODY_EDGE_ALPHA: 0.8,
  BODY_EDGE_ALPHA_EMPHASISED: 1,
  BODY_FACE_ALPHA: 0.14,
  BODY_FACE_ALPHA_EMPHASISED: 0.34,
  BODY_RIM_GAIN: 2.4,
  BODY_RIM_POWER: 2.4,
  /**
   * How sharp a fold has to be to count as an edge of a body, in degrees.
   *
   * High enough that a cylinder's barrel stays a barrel instead of coming
   * through as a picket fence, low enough to catch a gem's facets.
   */
  EDGE_ANGLE: 18,
  /** The collar an agent in a flock wears, as a multiple of its radius. */
  COLLAR_RADIUS: 1.65,
  COLLAR_ALPHA: 0.75,

  /** Breath applied to a selected node, as a fraction of its size. */
  SELECTED_BREATH: 0.09,
  SELECTED_BREATH_PERIOD_MS: 1500,
} as const;

/**
 * The instrument a region wears.
 *
 * A graduated ring lying in the ground plane at the region's waist, with two
 * gauges on it, and a readout panel beside it. Lying flat rather than facing
 * the camera on purpose: it grounds the region in the scene and reads as part
 * of the estate rather than as a sticker over it.
 */
export const INSTRUMENT = {
  /** Where the ring sits, as a multiple of the region's half-width. */
  RING_RADIUS: 1.06,
  /** Where each gauge sits, relative to the ring. */
  HEALTH_RADIUS: 1.15,
  TRAFFIC_RADIUS: 0.99,

  /** Graduations. Majors every twelfth, so the scale reads as quarters. */
  TICKS: 72,
  MAJOR_EVERY: 6,
  MINOR_LENGTH: 0.035,
  MAJOR_LENGTH: 0.085,
  ARC_SEGMENTS: 96,

  RING_ALPHA: 0.34,
  TICK_ALPHA: 0.5,
  /** The health gauge is the loudest thing on a region: a gap in it matters. */
  HEALTH_ALPHA: 0.9,
  TRAFFIC_ALPHA: 0.75,

  /**
   * Readout height in screen pixels, and how near you have to be for one.
   *
   * Deliberately short range. A panel of figures for every region at once is
   * eight panels over an estate they are describing — the reading gets buried
   * under the readouts. Far off, a region is its ring and its name; the
   * figures arrive as you go and look.
   */
  READOUT_SIZE: 58,
  READOUT_MAX_DISTANCE: 3200,
  /** Where the panel hangs, as a multiple of the region's half-extent. */
  READOUT_OFFSET: 1.15,
} as const;

/**
 * The expanding rings that mark the members of an agent mesh.
 *
 * Billboarded rather than laid flat: a mesh spans clusters and therefore
 * spans tiers, and a ring lying in the floor plane disappears the moment the
 * camera comes down to a raking angle — which is the angle the 3D view is
 * for.
 */
export const MESH_PULSE3D = {
  PERIOD_MS: 1700,
  /** Ring radius at the start of a cycle, as a multiple of member radius. */
  START_SCALE: 2.4,
  /** Radius at the end of a cycle. */
  END_SCALE: 5.6,
  PEAK_ALPHA: 0.95,
  /** A second ring runs half a cycle behind the first. */
  PHASE_OFFSET: 0.5,
  TRAILING_DIM: 0.7,
  /** Steady halo so members stay marked while a ring is faded out. */
  HALO_SCALE: 2.6,
  HALO_ALPHA: 0.5,
} as const;

export const STARS3D = {
  COUNT: 900,
  /** Radius of the shell the stars sit on. */
  RADIUS: 26000,
  /**
   * Screen pixels, not world units: the field is a backdrop at a fixed
   * distance, so a star that grew as the camera pulled back would read as an
   * object in the estate rather than as sky.
   */
  SIZE: 2.2,
  ALPHA: 0.4,
} as const;

/** Label sizes in world units, and the camera distance each stops drawing at. */
export const LABEL3D = {
  /**
   * Label heights, in screen pixels.
   *
   * Pixels, not world units — the same choice the plan makes, for the same
   * reason. A name sized in the world is a speck at overview and a banner
   * across the whole stage once you have flown in to read it, and there is no
   * middle setting that is right at both ends. Held at a constant size on
   * screen it is always legible, and the distance limits below are what keep
   * the far ones from piling up.
   */
  PRIMARY_SIZE: 13,
  SECONDARY_SIZE: 11,
  ZONE_SIZE: 15,
  /** Primary labels survive out to here; secondary ones to a third of it. */
  PRIMARY_MAX_DISTANCE: 9000,
  SECONDARY_MAX_DISTANCE: 2600,
  ZONE_MAX_DISTANCE: 14000,
  /** Cap on live text sprites, so a large estate cannot flood the GPU. */
  MAX_LIVE: 90,
  /** Clear air a name insists on, in screen pixels. */
  COLLISION_PADDING: 6,
  /** Height above the node the label floats at, as a multiple of its radius. */
  RISE: 2.1,
  ALPHA: 0.86,
} as const;

/** Ambient and key light. Enough shading to read form; not a lit studio. */
export const LIGHT3D = {
  AMBIENT_INTENSITY: 0.55,
  KEY_INTENSITY: 1.6,
  KEY_POSITION: { x: 1200, y: 2400, z: 1600 },
  RIM_INTENSITY: 1.1,
  RIM_POSITION: { x: -1600, y: 900, z: -1400 },
} as const;

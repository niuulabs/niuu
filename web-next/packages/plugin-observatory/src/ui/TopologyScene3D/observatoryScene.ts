/**
 * The three.js scene for the Observatory's 3D view.
 *
 * Everything here is construction and per-frame animation over a
 * `Scene3DModel`. It owns no React state and no DOM events: given a model it
 * builds a scene, and given a frame description it moves what should move.
 * That split is what lets the interaction layer stay small and lets the scene
 * be built and stepped in a test without a GPU.
 *
 * The visual vocabulary is deliberately the plan's: the same compute hues, the
 * same layer colours, the same mesh pulse, the same well. A 3D view that
 * invented its own palette would be a second product, and an operator would
 * have to learn the estate twice.
 */

import {
  AdditiveBlending,
  AmbientLight,
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  Color,
  CylinderGeometry,
  EdgesGeometry,
  IcosahedronGeometry,
  Float32BufferAttribute,
  SphereGeometry,
  DirectionalLight,
  DoubleSide,
  Group,
  Line,
  LineLoop,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  OctahedronGeometry,
  Object3D,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Raycaster,
  Scene,
  ShaderMaterial,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  TetrahedronGeometry,
  Vector2,
  Vector3,
  type BufferGeometry as TBufferGeometry,
  type Material,
  type Texture,
} from 'three';

import type { ComputeClass } from '../../domain/computeClass';
import type { Rgb } from '../TopologyCanvas/nodeStyle';
import {
  CAMERA3D,
  EDGE3D,
  INSTRUMENT,
  LABEL3D,
  LIGHT3D,
  MESH_PULSE3D,
  NODE3D,
  RISER,
  STARS3D,
  ZONE3D,
} from './scene3dConfig';
import { LAYER_COLOUR } from '../TopologyCanvas/renderer';
import {
  moteCountFor,
  pointAlongArc,
  type Edge3D,
  type Node3D,
  type Scene3DModel,
  type Zone3D,
} from './sceneModel';
import { partsOf, type FormPart } from './nodeForm';
import { placeLabels, type LabelCandidate } from './labelLayout';
import type { Vec3 } from './vec3';
import { CANVAS } from '../TopologyCanvas/config';
import {
  createBackdropTexture,
  createGlowTexture,
  createLabelTexture,
  estimateLabelAspect,
  createMarkTexture,
  createMoteTexture,
  createReadoutTexture,
  createRingTexture,
} from './textures';
import { eyePosition, worldUnitsPerPixel, type OrbitCamera } from './orbitCamera';
import { arcRing, circleRing, graduatedRing } from './instruments';
import type { RegionReadout } from '../../domain/regionStats';

// ── Frame description ─────────────────────────────────────────────────────────

export interface Scene3DFrame {
  /** Animation clock, in milliseconds. */
  now: number;
  hoveredId: string | null;
  selectedId: string | null;
  /**
   * The ids lit because they touch whatever is being pointed at, or null when
   * nothing is. Everything outside the set steps back.
   */
  litIds: ReadonlySet<string> | null;
  /** Members of the agent mesh being engaged with, if any. */
  meshMemberIds: ReadonlySet<string> | null;
  hiddenCompute?: ReadonlySet<ComputeClass>;
  reducedMotion: boolean;
  camera: OrbitCamera;
  /**
   * Stage size in CSS pixels.
   *
   * Height sets how big a name is drawn; both together decide which names fit
   * on screen at all.
   */
  viewportWidth: number;
  viewportHeight: number;
}

// ── Colour helpers ────────────────────────────────────────────────────────────

/** An 0–255 triple as a three.js colour in the renderer's working space. */
export function toColor(rgb: Rgb, target = new Color()): Color {
  return target.setRGB(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, SRGBColorSpace);
}

// ── Geometry vocabulary ───────────────────────────────────────────────────────

/**
 * The solid one part of a body is cut from.
 *
 * Unit-sized: a form scales its parts, and a node scales the whole body, so
 * one geometry serves every body that uses it however big the thing is.
 */
export function geometryForSolid(solid: FormPart['solid']): TBufferGeometry {
  switch (solid) {
    case 'box':
      return new BoxGeometry(1, 1, 1);
    case 'gem':
      return new IcosahedronGeometry(1, 1);
    case 'spindle':
      // Two pyramids base to base: pointed at both ends.
      return new OctahedronGeometry(1, 0);
    case 'pillar':
      // Six-sided rather than round: a cylinder with enough segments to look
      // smooth is a drum, and a drum seen from above is a doughnut.
      return new CylinderGeometry(1, 1, 1, 6);
    case 'wafer':
      return new CylinderGeometry(1, 1, 1, 6);
    case 'wedge':
    default:
      return new TetrahedronGeometry(1, 0);
  }
}

/**
 * The material a region's shell is drawn with.
 *
 * A flat wash on a sphere renders as a flat disc: nothing about a constant
 * opacity says which way the surface is facing, so the region reads as a
 * circle painted over the estate rather than as a volume around it. This
 * brightens the surface as it turns away from the eye — the way a soap bubble
 * does — which is what makes it read as round, and makes the silhouette clear
 * enough that no wireframe is needed over the contents.
 */
export function createShellMaterial(
  colour: Color,
  alpha: number,
  rimGain: number = ZONE3D.RIM_GAIN,
  rimPower: number = ZONE3D.RIM_POWER,
): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uColour: { value: colour },
      uAlpha: { value: alpha },
      uRimGain: { value: rimGain },
      uRimPower: { value: rimPower },
    },
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
        vNormal = normalize(normalMatrix * normal);
        vView = normalize(-viewPosition.xyz);
        gl_Position = projectionMatrix * viewPosition;
      }
    `,
    fragmentShader: `
      uniform vec3 uColour;
      uniform float uAlpha;
      uniform float uRimGain;
      uniform float uRimPower;
      varying vec3 vNormal;
      varying vec3 vView;
      void main() {
        // Absolute, so the far wall of the shell lights the same way the near
        // one does and the silhouette reads from either side.
        float facing = abs(dot(normalize(vNormal), normalize(vView)));
        float rim = pow(1.0 - facing, uRimPower);
        gl_FragColor = vec4(uColour, uAlpha * (1.0 + rim * uRimGain));
      }
    `,
    transparent: true,
    depthWrite: false,
    side: DoubleSide,
    blending: AdditiveBlending,
  });
}

/**
 * A unit cube drawn as corner brackets rather than as twelve full edges.
 *
 * A complete wireframe box is a modelling-package artefact: twelve identical
 * hairlines that say "here is a cuboid" and nothing else, and at the size a
 * realm occupies they are the heaviest thing in the frame. Brackets say the
 * same thing with a fraction of the ink, leave the middle of each face clear
 * for what is standing inside it, and read as an instrument rather than as
 * geometry.
 *
 * `arm` is the fraction of each half-extent a bracket reaches along.
 */
export function boxWireframe(arm: number = ZONE3D.BRACKET_ARM): TBufferGeometry {
  const reach = Math.max(0.02, Math.min(arm, 1));
  const points: number[] = [];

  for (const sx of [-1, 1]) {
    for (const sy of [-1, 1]) {
      for (const sz of [-1, 1]) {
        // Three arms per corner, one down each axis toward its neighbour.
        points.push(sx, sy, sz, sx - sx * reach, sy, sz);
        points.push(sx, sy, sz, sx, sy - sy * reach, sz);
        points.push(sx, sy, sz, sx, sy, sz - sz * reach);
      }
    }
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(points, 3));
  return geometry;
}

/** Shapes whose solid reads right only when it lies flat on its deck. */ // ── Deterministic starfield ───────────────────────────────────────────────────

/**
 * Stars on a distant shell, placed by a hash rather than by `Math.random`.
 *
 * The field has to be identical on every mount: a background that reshuffles
 * whenever the operator toggles back to 2D and returns reads as the estate
 * having moved.
 */
export function starPositions(count: number, radius: number): Float32Array {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    let hash = 5381 ^ (i * 2654435761);
    hash = (hash ^ (hash >>> 15)) >>> 0;
    const u = ((hash % 10007) / 10007) * 2 - 1;
    const v = (((hash >>> 8) % 10009) / 10009) * Math.PI * 2;
    const planar = Math.sqrt(Math.max(0, 1 - u * u));
    positions[i * 3] = radius * planar * Math.cos(v);
    positions[i * 3 + 1] = radius * u;
    positions[i * 3 + 2] = radius * planar * Math.sin(v);
  }
  return positions;
}

// ── Node alpha ────────────────────────────────────────────────────────────────

/**
 * How much of a node's body has arrived at this distance.
 *
 * One at arm's length, nothing from across the estate, eased between so that
 * eighty of them do not all switch on in the same frame.
 */
export function bodyDetail(node: Node3D, eye: Vec3): number {
  const distance = Math.hypot(
    node.position.x - eye.x,
    node.position.y - eye.y,
    node.position.z - eye.z,
  );
  if (distance <= NODE3D.BODY_NEAR) return 1;
  if (distance >= NODE3D.BODY_FAR) return 0;
  const t = (NODE3D.BODY_FAR - distance) / (NODE3D.BODY_FAR - NODE3D.BODY_NEAR);
  return t * t * (3 - 2 * t);
}

/**
 * How present a node is this frame.
 *
 * A switched-off compute class outranks hover: the operator has said they are
 * not interested in that silicon at all, and a hover should not bring it back.
 */
export function nodeAlpha(node: Node3D, frame: Scene3DFrame): number {
  if (frame.hiddenCompute?.has(node.computeClass)) return NODE3D.FILTERED_ALPHA;
  if (!frame.hoveredId) return 1;
  if (node.id === frame.hoveredId || frame.litIds?.has(node.id)) return 1;
  return NODE3D.DIMMED_ALPHA;
}

// ── Internal records ──────────────────────────────────────────────────────────

interface NodeVisual {
  node: Node3D;
  /** The mark itself. Null for Mímir, which is drawn as a well. */
  mark: Sprite | null;
  markMaterial: SpriteMaterial | null;
  /** The body, which fades in as the camera closes. */
  body: Group | null;
  bodyFaces: ShaderMaterial[];
  bodyEdges: LineBasicMaterial[];
  halo: Sprite;
  haloMaterial: SpriteMaterial;
  baseHaloScale: number;
}

interface FlowMote {
  edge: Edge3D;
  offset: number;
}

export interface ObservatorySceneOptions {
  /** Suppress the parts that breathe, spin and travel. */
  reducedMotion?: boolean;
  /**
   * The figures a region shows on its instrument.
   *
   * Passed in rather than derived here: what a region has to say about itself
   * is a question about the estate, and this module only knows how to draw.
   */
  readoutFor?: (regionId: string) => RegionReadout | null;
}

/** Everything the interaction layer needs from a built scene. */
export interface ObservatoryScene {
  scene: Scene;
  camera: PerspectiveCamera;
  /** Applies a camera record to the three.js camera. */
  applyCamera(camera: OrbitCamera, aspect: number): void;
  /** Node id under a normalised device coordinate, or null. */
  pick(ndc: { x: number; y: number }): string | null;
  update(frame: Scene3DFrame): void;
  dispose(): void;
}

// ── Build ─────────────────────────────────────────────────────────────────────

export function createObservatoryScene(
  model: Scene3DModel,
  options: ObservatorySceneOptions = {},
): ObservatoryScene {
  const scene = new Scene();
  const camera = new PerspectiveCamera(CAMERA3D.FOV, 1, CAMERA3D.NEAR, CAMERA3D.FAR);
  const readoutFor = options.readoutFor;

  const disposables: Array<{ dispose: () => void }> = [];
  const track = <T extends { dispose: () => void }>(value: T): T => {
    disposables.push(value);
    return value;
  };

  const glowTexture = createGlowTexture();
  const ringTexture = createRingTexture();
  const moteTexture = createMoteTexture();
  for (const texture of [glowTexture, ringTexture, moteTexture]) {
    if (texture) disposables.push(texture);
  }

  // The plan's own wash, so the ground does not change colour when the
  // operator switches views. Falls back to the flat edge colour where there is
  // no canvas to draw a gradient on.
  const backdrop = createBackdropTexture(CANVAS.BACKDROP_CENTRE, CANVAS.BACKDROP_EDGE);
  if (backdrop) {
    disposables.push(backdrop);
    scene.background = backdrop;
  }

  scene.add(new AmbientLight(0xdff1ff, LIGHT3D.AMBIENT_INTENSITY));
  const key = new DirectionalLight(0xe8f6ff, LIGHT3D.KEY_INTENSITY);
  key.position.set(LIGHT3D.KEY_POSITION.x, LIGHT3D.KEY_POSITION.y, LIGHT3D.KEY_POSITION.z);
  scene.add(key);
  const rim = new DirectionalLight(0x8fb6ff, LIGHT3D.RIM_INTENSITY);
  rim.position.set(LIGHT3D.RIM_POSITION.x, LIGHT3D.RIM_POSITION.y, LIGHT3D.RIM_POSITION.z);
  scene.add(rim);

  // ── Stars ───────────────────────────────────────────────────────────────────

  const starGeometry = track(new BufferGeometry());
  starGeometry.setAttribute(
    'position',
    new BufferAttribute(starPositions(STARS3D.COUNT, STARS3D.RADIUS), 3),
  );
  const starMaterial = track(
    new PointsMaterial({
      size: STARS3D.SIZE,
      map: moteTexture ?? undefined,
      color: new Color().setRGB(0.73, 0.9, 0.99, SRGBColorSpace),
      transparent: true,
      opacity: STARS3D.ALPHA,
      depthWrite: false,
      sizeAttenuation: false,
    }),
  );
  const stars = new Points(starGeometry, starMaterial);
  stars.frustumCulled = false;
  // Behind everything, always: the field is sky, and a star drawn over a node
  // reads as something standing in the estate.
  stars.renderOrder = -10;
  scene.add(stars);

  // ── Region volumes ──────────────────────────────────────────────────────────

  const zoneGroup = new Group();
  const zoneLabels: Array<{ sprite: Sprite; zone: Zone3D; aspect: number }> = [];
  scene.add(zoneGroup);

  const buildZone = (zone: Zone3D): void => {
    const colour = toColor(zone.colour);

    const shell = new Mesh(
      track(zone.shape === 'sphere' ? new SphereGeometry(1, 48, 32) : new BoxGeometry(2, 2, 2)),
      track(createShellMaterial(colour.clone(), zone.fillAlpha)),
    );
    shell.scale.set(zone.half.x, zone.half.y, zone.half.z);
    shell.position.set(zone.position.x, zone.position.y, zone.position.z);
    shell.renderOrder = -2;
    zoneGroup.add(shell);

    // A box gets its edges drawn as well: a cube's silhouette from a
    // three-quarter view is a hexagon, and the rim alone leaves it ambiguous
    // which way the thing is squared up. A sphere has no such problem, and a
    // cage of rings over its contents is all cost.
    if (zone.shape === 'box') {
      const outline = new LineSegments(
        track(boxWireframe()),
        track(
          new LineBasicMaterial({
            color: colour.clone(),
            transparent: true,
            opacity: zone.edgeAlpha,
            depthWrite: false,
          }),
        ),
      );
      outline.scale.set(zone.half.x, zone.half.y, zone.half.z);
      outline.position.set(zone.position.x, zone.position.y, zone.position.z);
      outline.renderOrder = -1;
      outline.userData.role = 'bracket';
      zoneGroup.add(outline);
    }

    if (!zone.label) return;
    const label = createLabelTexture(zone.label, { weight: 600 });
    if (!label) return;
    disposables.push(label.texture);
    const material = track(
      new SpriteMaterial({
        map: label.texture,
        color: colour.clone(),
        transparent: true,
        opacity: LABEL3D.ALPHA,
        depthWrite: false,
        depthTest: false,
      }),
    );
    const sprite = new Sprite(material);
    // Above the shell rather than inside it, where it would be read as a label
    // on whatever it happened to land in front of.
    sprite.position.set(zone.position.x, zone.position.y + zone.half.y, zone.position.z);
    sprite.renderOrder = 6;
    // The name is the region's hit target, exactly as it is in the plan. The
    // shell is not: it encloses everything inside it, so a click meant for an
    // agent would land on whichever regions happen to surround it.
    sprite.userData.nodeId = zone.id;
    sprite.userData.pickKind = 'zone';
    zoneGroup.add(sprite);
    zoneLabels.push({ sprite, zone, aspect: label.aspect });
  };

  for (const zone of model.zones) buildZone(zone);

  // ── Region instruments ──────────────────────────────────────────────────────

  /**
   * The dial a region wears: a graduated ring at its waist, a gauge for how
   * much of it is well, a second for how much of the estate's work it carries,
   * and a panel of the figures behind them.
   *
   * This is where the stage stops being shapes and starts being an instrument.
   * A shell says something is here; a ring with a gap in it says a sixth of
   * what is here is not well, from across the estate, without a click.
   */
  const readoutPanels: Array<{ sprite: Sprite; zone: Zone3D; aspect: number }> = [];
  /** Regions that have figures to show, and how wide the panel came out. */
  const panelIds = new Set<string>();
  const panelAspects = new Map<string, number>();

  const buildInstrument = (zone: Zone3D): void => {
    const readout = readoutFor?.(zone.id) ?? null;
    const colour = toColor(zone.colour);
    const radius = Math.max(zone.half.x, zone.half.z);

    const ring = (positions: Float32Array, alpha: number, scale: number, loop: boolean): void => {
      const geometry = track(new BufferGeometry());
      geometry.setAttribute('position', new BufferAttribute(positions, 3));
      const material = track(
        new LineBasicMaterial({
          color: colour.clone(),
          transparent: true,
          opacity: alpha,
          depthWrite: false,
        }),
      );
      const line = loop ? new LineLoop(geometry, material) : new Line(geometry, material);
      line.scale.set(radius * scale, 1, radius * scale);
      line.position.set(zone.position.x, zone.position.y, zone.position.z);
      line.renderOrder = -1;
      zoneGroup.add(line);
    };

    ring(circleRing(INSTRUMENT.ARC_SEGMENTS), INSTRUMENT.RING_ALPHA, INSTRUMENT.RING_RADIUS, true);

    const ticks = track(new BufferGeometry());
    ticks.setAttribute(
      'position',
      new BufferAttribute(
        graduatedRing(
          INSTRUMENT.TICKS,
          INSTRUMENT.MAJOR_EVERY,
          INSTRUMENT.MINOR_LENGTH,
          INSTRUMENT.MAJOR_LENGTH,
        ),
        3,
      ),
    );
    const tickLines = new LineSegments(
      ticks,
      track(
        new LineBasicMaterial({
          color: colour.clone(),
          transparent: true,
          opacity: INSTRUMENT.TICK_ALPHA,
          depthWrite: false,
        }),
      ),
    );
    tickLines.scale.set(radius * INSTRUMENT.RING_RADIUS, 1, radius * INSTRUMENT.RING_RADIUS);
    tickLines.position.set(zone.position.x, zone.position.y, zone.position.z);
    tickLines.renderOrder = -1;
    tickLines.userData.role = 'graduations';
    zoneGroup.add(tickLines);

    if (!readout) return;

    // A gauge is only drawn for a figure the snapshot actually reports. An
    // empty dial reads as a measurement of zero, which is a different claim
    // from having nothing to measure.
    if (readout.health != null) {
      ring(
        arcRing(readout.health, INSTRUMENT.ARC_SEGMENTS),
        INSTRUMENT.HEALTH_ALPHA,
        INSTRUMENT.HEALTH_RADIUS,
        false,
      );
    }
    if (readout.trafficShare != null && readout.trafficShare > 0) {
      ring(
        arcRing(readout.trafficShare, INSTRUMENT.ARC_SEGMENTS),
        INSTRUMENT.TRAFFIC_ALPHA,
        INSTRUMENT.TRAFFIC_RADIUS,
        false,
      );
    }

    if (readout.rows.length === 0) return;
    const panel = createReadoutTexture(readout.title, readout.rows);
    if (!panel) return;
    disposables.push(panel.texture);
    const sprite = new Sprite(
      track(
        new SpriteMaterial({
          map: panel.texture,
          color: colour.clone(),
          transparent: true,
          opacity: LABEL3D.ALPHA,
          depthWrite: false,
          depthTest: false,
        }),
      ),
    );
    // Off to one side rather than over the region, so it annotates it instead
    // of covering what it is describing.
    sprite.position.set(
      zone.position.x + zone.half.x * INSTRUMENT.READOUT_OFFSET,
      zone.position.y + zone.half.y * 0.5,
      zone.position.z,
    );
    sprite.renderOrder = 7;
    zoneGroup.add(sprite);
    panelIds.add(zone.id);
    panelAspects.set(zone.id, panel.aspect);
    readoutPanels.push({ sprite, zone, aspect: panel.aspect });
  };

  for (const zone of model.zones) buildInstrument(zone);

  // ── Risers ──────────────────────────────────────────────────────────────────

  if (model.risers.length > 0) {
    const positions = new Float32Array(model.risers.length * 6);
    model.risers.forEach((riser, index) => {
      positions.set(
        [riser.from.x, riser.from.y, riser.from.z, riser.to.x, riser.to.y, riser.to.z],
        index * 6,
      );
    });
    const geometry = track(new BufferGeometry());
    geometry.setAttribute('position', new BufferAttribute(positions, 3));
    const material = track(
      new LineBasicMaterial({
        color: new Color().setRGB(0.58, 0.74, 0.92, SRGBColorSpace),
        transparent: true,
        opacity: RISER.ALPHA,
        depthWrite: false,
      }),
    );
    const risers = new LineSegments(geometry, material);
    risers.renderOrder = -1;
    scene.add(risers);
  }

  // ── Edges ───────────────────────────────────────────────────────────────────

  const edgeGeometry = track(new BufferGeometry());
  const edgeMaterial = track(
    new LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: EDGE3D.ALPHA,
      depthWrite: false,
      blending: AdditiveBlending,
    }),
  );
  const edgeSegmentCounts: number[] = [];
  let edgeVertexCount = 0;
  for (const edge of model.edges) {
    const segments = Math.max(edge.points.length - 1, 0);
    edgeSegmentCounts.push(segments);
    edgeVertexCount += segments * 2;
  }
  const edgePositions = new Float32Array(edgeVertexCount * 3);
  const edgeColours = new Float32Array(edgeVertexCount * 3);
  {
    let cursor = 0;
    model.edges.forEach((edge) => {
      for (let i = 0; i + 1 < edge.points.length; i += 1) {
        const a = edge.points[i]!;
        const b = edge.points[i + 1]!;
        edgePositions.set([a.x, a.y, a.z, b.x, b.y, b.z], cursor * 3);
        cursor += 2;
      }
    });
  }
  edgeGeometry.setAttribute('position', new BufferAttribute(edgePositions, 3));
  edgeGeometry.setAttribute('color', new BufferAttribute(edgeColours, 3));
  const edgeLines = new LineSegments(edgeGeometry, edgeMaterial);
  edgeLines.frustumCulled = false;
  scene.add(edgeLines);

  const edgeColourCache = new Color();

  /**
   * Repaint every arc for the current emphasis.
   *
   * Line materials carry one opacity for the whole object, so "this arc is not
   * part of what you are tracing" has to be said by darkening its vertices
   * toward the field rather than by fading them. Only run when the emphasis
   * actually changes — it rewrites the whole buffer.
   */
  const paintEdges = (frame: Scene3DFrame): void => {
    let cursor = 0;
    model.edges.forEach((edge, edgeIndex) => {
      const traced =
        !frame.hoveredId || edge.sourceId === frame.hoveredId || edge.targetId === frame.hoveredId;
      const emphasis = traced ? 1 : EDGE3D.DIMMED_ALPHA / EDGE3D.ALPHA;
      toColor(edge.colour, edgeColourCache);
      const r = edgeColourCache.r * emphasis;
      const g = edgeColourCache.g * emphasis;
      const b = edgeColourCache.b * emphasis;
      const vertices = edgeSegmentCounts[edgeIndex]! * 2;
      for (let i = 0; i < vertices; i += 1) {
        edgeColours[(cursor + i) * 3] = r;
        edgeColours[(cursor + i) * 3 + 1] = g;
        edgeColours[(cursor + i) * 3 + 2] = b;
      }
      cursor += vertices;
    });
    edgeGeometry.getAttribute('color').needsUpdate = true;
  };

  // ── Flow motes ──────────────────────────────────────────────────────────────

  const motes: FlowMote[] = [];
  for (const edge of model.edges) {
    const count = moteCountFor(edge.ratePerMinute);
    for (let i = 0; i < count; i += 1) motes.push({ edge, offset: i / count });
  }
  const motePositions = new Float32Array(Math.max(motes.length, 1) * 3);
  const moteColours = new Float32Array(Math.max(motes.length, 1) * 3);
  motes.forEach((mote, index) => {
    toColor(mote.edge.colour, edgeColourCache);
    moteColours.set([edgeColourCache.r, edgeColourCache.g, edgeColourCache.b], index * 3);
  });
  const moteGeometry = track(new BufferGeometry());
  moteGeometry.setAttribute('position', new BufferAttribute(motePositions, 3));
  moteGeometry.setAttribute('color', new BufferAttribute(moteColours, 3));
  const moteMaterial = track(
    new PointsMaterial({
      size: EDGE3D.FLOW_SIZE,
      map: moteTexture ?? undefined,
      vertexColors: true,
      transparent: true,
      opacity: EDGE3D.FLOW_ALPHA,
      depthWrite: false,
      blending: AdditiveBlending,
    }),
  );
  const moteField = new Points(moteGeometry, moteMaterial);
  moteField.frustumCulled = false;
  moteField.visible = motes.length > 0;
  scene.add(moteField);

  // ── Nodes ───────────────────────────────────────────────────────────────────

  const nodeGroup = new Group();
  scene.add(nodeGroup);
  const haloGroup = new Group();
  scene.add(haloGroup);

  const visuals: NodeVisual[] = [];
  const visualById = new Map<string, NodeVisual>();

  const markByShape = new Map<string, Texture | null>();
  const markFor = (shape: string): Texture | null => {
    if (markByShape.has(shape)) return markByShape.get(shape) ?? null;
    const built = createMarkTexture(shape);
    if (built) disposables.push(built);
    markByShape.set(shape, built);
    return built;
  };

  /** One solid per part shape, shared by every body that uses it. */
  const solidCache = new Map<string, TBufferGeometry>();
  const solidFor = (solid: FormPart['solid']): TBufferGeometry => {
    const cached = solidCache.get(solid);
    if (cached) return cached;
    const built = track(geometryForSolid(solid));
    solidCache.set(solid, built);
    return built;
  };

  const edgeCache = new Map<string, TBufferGeometry>();
  const edgesForSolid = (solid: FormPart['solid']): TBufferGeometry => {
    const cached = edgeCache.get(solid);
    if (cached) return cached;
    // Derived from the solid, so a body and the lines around it can never
    // disagree about its shape.
    const built = track(new EdgesGeometry(solidFor(solid), NODE3D.EDGE_ANGLE));
    edgeCache.set(solid, built);
    return built;
  };

  for (const node of model.nodes) {
    // A region is its shell and its dial. Drawing a mark at its centre too
    // would put two marks on the stage for one thing, and stand the second in
    // the middle of everything the first is there to contain.
    if (node.isBoundary) continue;

    const colour = toColor(node.colour);

    let mark: Sprite | null;
    let markMaterial: SpriteMaterial | null;
    let body: Group | null = null;
    const bodyFaces: ShaderMaterial[] = [];
    const bodyEdges: LineBasicMaterial[] = [];

    {
      // The mark: camera-facing, so it reads the same from any angle, and the
      // only thing drawn once the estate is far enough away that a body would
      // be a smear.
      markMaterial = track(
        new SpriteMaterial({
          map: markFor(node.shape) ?? undefined,
          color: colour.clone(),
          transparent: true,
          opacity: NODE3D.MARK_ALPHA,
          depthWrite: false,
        }),
      );
      mark = new Sprite(markMaterial);
      mark.scale.setScalar(node.radius * NODE3D.MARK_SCALE);
      mark.position.set(node.position.x, node.position.y, node.position.z);
      mark.renderOrder = 3;
      mark.userData.nodeId = node.id;
      mark.userData.pickKind = 'mark';
      nodeGroup.add(mark);

      // The body, assembled from the parts its form is made of.
      const parts = partsOf(node.form);
      if (parts.length > 0) {
        body = new Group();
        for (const part of parts) {
          const faceMaterial = track(
            createShellMaterial(
              colour.clone(),
              NODE3D.BODY_FACE_ALPHA,
              NODE3D.BODY_RIM_GAIN,
              NODE3D.BODY_RIM_POWER,
            ),
          );
          const face = new Mesh(solidFor(part.solid), faceMaterial);
          face.position.set(...part.offset);
          face.scale.set(...part.scale);
          body.add(face);
          bodyFaces.push(faceMaterial);

          const edgeMaterial = track(
            new LineBasicMaterial({
              color: colour.clone(),
              transparent: true,
              opacity: NODE3D.BODY_EDGE_ALPHA,
              depthWrite: false,
            }),
          );
          const edges = new LineSegments(edgesForSolid(part.solid), edgeMaterial);
          edges.position.set(...part.offset);
          edges.scale.set(...part.scale);
          body.add(edges);
          bodyEdges.push(edgeMaterial);
        }

        // The collar: an agent that peers with others wears one, so a flock is
        // visible without having to select a member and watch what lights up.
        if (node.meshId) {
          const collarMaterial = track(
            new LineBasicMaterial({
              color: toColor(LAYER_COLOUR.mesh),
              transparent: true,
              opacity: NODE3D.COLLAR_ALPHA,
              depthWrite: false,
            }),
          );
          const collarGeometry = track(new BufferGeometry());
          collarGeometry.setAttribute(
            'position',
            new BufferAttribute(circleRing(INSTRUMENT.ARC_SEGMENTS), 3),
          );
          const collar = new LineLoop(collarGeometry, collarMaterial);
          collar.scale.set(NODE3D.COLLAR_RADIUS, 1, NODE3D.COLLAR_RADIUS);
          body.add(collar);
          bodyEdges.push(collarMaterial);
        }

        body.scale.setScalar(node.radius);
        body.position.set(node.position.x, node.position.y, node.position.z);
        body.renderOrder = 2;
        body.userData.nodeId = node.id;
        body.userData.pickKind = 'body';
        body.visible = false;
        nodeGroup.add(body);
      }
    }

    const haloMaterial = track(
      new SpriteMaterial({
        map: glowTexture ?? undefined,
        color: colour.clone(),
        transparent: true,
        opacity: NODE3D.HALO_ALPHA,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    );
    const halo = new Sprite(haloMaterial);
    const baseHaloScale = node.radius * NODE3D.HALO_SCALE;
    halo.scale.setScalar(baseHaloScale);
    halo.position.set(node.position.x, node.position.y, node.position.z);
    halo.renderOrder = 1;
    halo.userData.nodeId = node.id;
    halo.userData.pickKind = 'node';
    haloGroup.add(halo);

    const visual: NodeVisual = {
      node,
      mark,
      markMaterial,
      body,
      bodyFaces,
      bodyEdges,
      halo,
      haloMaterial,
      baseHaloScale,
    };
    visuals.push(visual);
    visualById.set(node.id, visual);
  }

  // ── Mesh pulse rings ────────────────────────────────────────────────────────

  const pulseGroup = new Group();
  scene.add(pulseGroup);
  const pulseSprites: Sprite[] = [];
  const pulseMaterials: SpriteMaterial[] = [];
  const meshColour = toColor(LAYER_COLOUR.mesh);

  const ensurePulseSprites = (count: number): void => {
    while (pulseSprites.length < count) {
      const material = track(
        new SpriteMaterial({
          map: ringTexture ?? undefined,
          color: meshColour.clone(),
          transparent: true,
          opacity: 0,
          depthWrite: false,
          depthTest: false,
          blending: AdditiveBlending,
        }),
      );
      const sprite = new Sprite(material);
      sprite.visible = false;
      sprite.renderOrder = 5;
      sprite.userData.pulse = true;
      pulseGroup.add(sprite);
      pulseSprites.push(sprite);
      pulseMaterials.push(material);
    }
  };

  // ── Labels ──────────────────────────────────────────────────────────────────

  const labelGroup = new Group();
  scene.add(labelGroup);
  const labelSprites = new Map<string, Sprite>();
  const labelMaterials = new Map<string, SpriteMaterial>();
  const labelTextures = new Map<string, Texture>();
  /** Each label's shape, so it can be re-sized every frame without stretching. */
  const labelAspects = new Map<string, number>();

  const createLabel = (node: Node3D): Sprite | null => {
    const built = createLabelTexture(node.detail ? `${node.label}  ·  ${node.detail}` : node.label);
    if (!built) return null;
    const material = new SpriteMaterial({
      map: built.texture,
      transparent: true,
      opacity: LABEL3D.ALPHA,
      depthWrite: false,
      depthTest: false,
    });
    const sprite = new Sprite(material);
    sprite.position.set(
      node.position.x,
      node.position.y + node.radius * LABEL3D.RISE,
      node.position.z,
    );
    sprite.renderOrder = 7;
    sprite.userData.label = true;
    labelGroup.add(sprite);
    labelSprites.set(node.id, sprite);
    labelMaterials.set(node.id, material);
    labelTextures.set(node.id, built.texture);
    labelAspects.set(node.id, built.aspect);
    return sprite;
  };

  const dropLabel = (id: string): void => {
    const sprite = labelSprites.get(id);
    if (sprite) labelGroup.remove(sprite);
    labelMaterials.get(id)?.dispose();
    labelTextures.get(id)?.dispose();
    labelSprites.delete(id);
    labelMaterials.delete(id);
    labelTextures.delete(id);
    labelAspects.delete(id);
  };

  /**
   * Which nodes carry a name this frame.
   *
   * Distance takes the place of the plan's zoom tiers: a name is drawn once
   * the camera is close enough that it would not collide with its neighbours,
   * and whatever is hovered or selected is named regardless, so detail is
   * never unreachable. The cap is the backstop — a large estate seen from just
   * inside the primary threshold would otherwise ask for several hundred text
   * sprites at once.
   */
  const projected = new Vector3();

  /**
   * Which nodes carry a name this frame.
   *
   * Distance decides who is eligible; the screen decides who actually fits.
   * Distance alone put two agents a metre apart in the same rack both in
   * range, so both labelled, and their names landed on top of each other —
   * an estate wearing a drift of overlapping text that says less than no text
   * would.
   */
  const wantedLabels = (frame: Scene3DFrame, viewport: { w: number; h: number }): Set<string> => {
    const eye = eyePosition(frame.camera);
    const candidates: LabelCandidate[] = [];

    /** Where a world point lands on the stage, or null when it is not on it. */
    const toScreen = (point: Vec3): { x: number; y: number } | null => {
      projected.set(point.x, point.y, point.z).project(camera);
      if (projected.z > 1) return null;
      const x = ((projected.x + 1) / 2) * viewport.w;
      const y = ((1 - projected.y) / 2) * viewport.h;
      if (x < 0 || x > viewport.w || y < 0 || y > viewport.h) return null;
      return { x, y };
    };

    // Regions go in first and outrank the entities inside them: a cluster's
    // name is the one an operator navigates by, and it is worth more than any
    // one of the dozen names standing in front of it.
    for (const { zone, aspect } of zoneLabels) {
      const distance = Math.hypot(
        zone.position.x - eye.x,
        zone.position.y - eye.y,
        zone.position.z - eye.z,
      );
      if (distance > LABEL3D.ZONE_MAX_DISTANCE) continue;
      const showsPanel = distance <= INSTRUMENT.READOUT_MAX_DISTANCE && panelIds.has(zone.id);
      const height = showsPanel ? INSTRUMENT.READOUT_SIZE : LABEL3D.ZONE_SIZE;
      const anchor = showsPanel
        ? {
            x: zone.position.x + zone.half.x * INSTRUMENT.READOUT_OFFSET,
            y: zone.position.y + zone.half.y * 0.5,
            z: zone.position.z,
          }
        : { x: zone.position.x, y: zone.position.y + zone.half.y, z: zone.position.z };
      const screen = toScreen(anchor);
      if (!screen) continue;
      candidates.push({
        id: `zone:${zone.id}`,
        x: screen.x,
        y: screen.y,
        width: height * (showsPanel ? (panelAspects.get(zone.id) ?? aspect) : aspect),
        height,
        priority: 1e5 + 1 / Math.max(distance, 1),
      });
    }

    for (const visual of visuals) {
      const node = visual.node;
      if (frame.hiddenCompute?.has(node.computeClass)) continue;

      const emphasised = node.id === frame.hoveredId || node.id === frame.selectedId;
      const limit =
        node.labelTier === 'primary'
          ? LABEL3D.PRIMARY_MAX_DISTANCE
          : LABEL3D.SECONDARY_MAX_DISTANCE;
      const distance = Math.hypot(
        node.position.x - eye.x,
        node.position.y - eye.y,
        node.position.z - eye.z,
      );
      if (!emphasised && distance > limit) continue;

      // Behind the eye, or off the stage: no room to argue about.
      const screen = toScreen(node.position);
      if (!screen) continue;

      const height = node.labelTier === 'primary' ? LABEL3D.PRIMARY_SIZE : LABEL3D.SECONDARY_SIZE;
      const text = node.detail ? `${node.label}  ·  ${node.detail}` : node.label;

      candidates.push({
        id: node.id,
        x: screen.x,
        y: screen.y,
        width: height * estimateLabelAspect(text),
        height,
        // What is being pointed at or has been chosen outranks everything;
        // then the entities that carry the story; then whatever is nearest.
        priority:
          (emphasised ? 1e6 : 0) +
          (node.labelTier === 'primary' ? 1e4 : 0) +
          1 / Math.max(distance, 1),
      });
    }

    return placeLabels(candidates, LABEL3D.MAX_LIVE, LABEL3D.COLLISION_PADDING);
  };

  // ── Per-frame ───────────────────────────────────────────────────────────────

  const raycaster = new Raycaster();
  const pointer = new Vector2();
  const reducedMotion = options.reducedMotion ?? false;
  let lastEmphasis = 'never-drawn';
  let lastLabelKey = 'never-laid-out';
  /** The names that won their space the last time the layout ran. */
  let chosenLabels = new Set<string>();

  const update = (frame: Scene3DFrame): void => {
    const still = frame.reducedMotion || reducedMotion;
    const now = still ? 0 : frame.now;

    const emphasisKey = `${frame.hoveredId ?? ''}|${[...(frame.hiddenCompute ?? [])].sort().join(',')}`;
    if (emphasisKey !== lastEmphasis) {
      paintEdges(frame);
      lastEmphasis = emphasisKey;
    }

    // Nodes.
    const breath = still
      ? 1
      : 1 +
        NODE3D.SELECTED_BREATH * Math.sin((now / NODE3D.SELECTED_BREATH_PERIOD_MS) * Math.PI * 2);
    const eye = eyePosition(frame.camera);
    for (const visual of visuals) {
      const alpha = nodeAlpha(visual.node, frame);
      const emphasised = visual.node.id === frame.hoveredId || visual.node.id === frame.selectedId;
      // A host or a session is a place as well as a thing; it steps back so
      // what runs on it stays the brighter mark.
      const solidity = visual.node.isContainer ? NODE3D.CONTAINER_OPACITY : 1;

      // The two readings cross-fade: a mark from across the estate, a body
      // once you are close enough for one to say anything.
      const built = emphasised ? 1 : bodyDetail(visual.node, eye);

      if (visual.markMaterial) {
        visual.markMaterial.opacity =
          (emphasised ? NODE3D.MARK_ALPHA_EMPHASISED : NODE3D.MARK_ALPHA) *
          alpha *
          solidity *
          (1 - built);
      }
      if (visual.mark) {
        visual.mark.visible = built < 0.99;
        visual.mark.scale.setScalar(
          visual.node.radius * NODE3D.MARK_SCALE * (emphasised ? breath : 1),
        );
      }
      if (visual.body) {
        visual.body.visible = built > 0.01;
        visual.body.scale.setScalar(visual.node.radius * (emphasised ? breath : 1));
        for (const face of visual.bodyFaces) {
          face.uniforms.uAlpha!.value =
            (emphasised ? NODE3D.BODY_FACE_ALPHA_EMPHASISED : NODE3D.BODY_FACE_ALPHA) *
            alpha *
            solidity *
            built;
        }
        for (const edge of visual.bodyEdges) {
          edge.opacity =
            (emphasised ? NODE3D.BODY_EDGE_ALPHA_EMPHASISED : NODE3D.BODY_EDGE_ALPHA) *
            alpha *
            solidity *
            built;
        }
      }
      visual.haloMaterial.opacity =
        (emphasised ? NODE3D.HALO_ALPHA_EMPHASISED : NODE3D.HALO_ALPHA) * alpha;
      visual.halo.scale.setScalar(visual.baseHaloScale * (emphasised ? breath : 1));
    }

    // Flow.
    if (motes.length > 0) {
      const travel = still ? 0 : (frame.now / 1000) * EDGE3D.FLOW_SPEED;
      motes.forEach((mote, index) => {
        const point = pointAlongArc(mote.edge.points, travel + mote.offset);
        motePositions.set([point.x, point.y, point.z], index * 3);
      });
      moteGeometry.getAttribute('position').needsUpdate = true;
    }

    // Mesh pulse.
    const members = frame.meshMemberIds;
    const memberVisuals = members
      ? [...members]
          .map((id) => visualById.get(id))
          .filter((visual): visual is NodeVisual => !!visual)
      : [];
    const phases = still
      ? [{ t: 0.32, dim: 1 }]
      : [
          { t: (frame.now % MESH_PULSE3D.PERIOD_MS) / MESH_PULSE3D.PERIOD_MS, dim: 1 },
          {
            t:
              ((frame.now % MESH_PULSE3D.PERIOD_MS) / MESH_PULSE3D.PERIOD_MS +
                MESH_PULSE3D.PHASE_OFFSET) %
              1,
            dim: MESH_PULSE3D.TRAILING_DIM,
          },
        ];
    ensurePulseSprites(memberVisuals.length * (phases.length + 1));

    let slot = 0;
    for (const visual of memberVisuals) {
      // A steady halo under the rings, so a member stays marked while a ring
      // is faded out and the mesh does not appear to blink out of existence.
      const haloSprite = pulseSprites[slot++]!;
      haloSprite.visible = true;
      haloSprite.position.copy(visual.halo.position);
      haloSprite.scale.setScalar(visual.node.radius * MESH_PULSE3D.HALO_SCALE);
      pulseMaterials[slot - 1]!.opacity = MESH_PULSE3D.HALO_ALPHA;

      for (const phase of phases) {
        const sprite = pulseSprites[slot++]!;
        const grown =
          MESH_PULSE3D.START_SCALE + phase.t * (MESH_PULSE3D.END_SCALE - MESH_PULSE3D.START_SCALE);
        sprite.visible = true;
        sprite.position.copy(visual.halo.position);
        sprite.scale.setScalar(visual.node.radius * grown);
        pulseMaterials[slot - 1]!.opacity =
          MESH_PULSE3D.PEAK_ALPHA * (1 - phase.t) ** 2 * phase.dim;
      }
    }
    for (let i = slot; i < pulseSprites.length; i += 1) pulseSprites[i]!.visible = false;

    // Labels — diffed rather than rebuilt, and only when something that could
    // change the set has changed.
    // The layout depends on where the camera is pointing as well as how far
    // back it stands: turning the estate moves every name on screen.
    const labelKey = [
      Math.round(frame.camera.distance),
      Math.round(frame.camera.azimuth * 40),
      Math.round(frame.camera.polar * 40),
      Math.round(frame.camera.target.x),
      Math.round(frame.camera.target.z),
      frame.hoveredId ?? '',
      frame.selectedId ?? '',
    ].join('|');
    if (labelKey !== lastLabelKey) {
      lastLabelKey = labelKey;
      chosenLabels = wantedLabels(frame, { w: frame.viewportWidth, h: frame.viewportHeight });
      for (const id of [...labelSprites.keys()]) {
        if (!chosenLabels.has(id)) dropLabel(id);
      }
      for (const id of chosenLabels) {
        if (labelSprites.has(id)) continue;
        const node = visualById.get(id)?.node;
        if (node) createLabel(node);
      }
    }
    // World units one screen pixel covers, so a name can be held at a constant
    // size however far back the camera stands.
    const perPixel = worldUnitsPerPixel(frame.camera, frame.viewportHeight);

    for (const [id, material] of labelMaterials) {
      const visual = visualById.get(id);
      material.opacity = LABEL3D.ALPHA * (visual ? nodeAlpha(visual.node, frame) : 1);

      const sprite = labelSprites.get(id);
      if (!sprite || !visual) continue;
      const height =
        (visual.node.labelTier === 'primary' ? LABEL3D.PRIMARY_SIZE : LABEL3D.SECONDARY_SIZE) *
        perPixel;
      sprite.scale.set(height * (labelAspects.get(id) ?? 1), height, 1);
      // Clear of the node by its own height, so the two never sit on top of
      // each other whatever the camera is doing.
      sprite.position.y = visual.node.position.y + visual.node.radius * LABEL3D.RISE + height * 0.6;
    }

    const showingPanel = new Set<string>();
    for (const { sprite, zone, aspect } of readoutPanels) {
      const distance = Math.hypot(
        zone.position.x - eye.x,
        zone.position.y - eye.y,
        zone.position.z - eye.z,
      );
      sprite.visible =
        distance <= INSTRUMENT.READOUT_MAX_DISTANCE && chosenLabels.has(`zone:${zone.id}`);
      if (sprite.visible) showingPanel.add(zone.id);
      const height = INSTRUMENT.READOUT_SIZE * perPixel;
      sprite.scale.set(height * aspect, height, 1);
    }

    for (const { sprite, zone, aspect } of zoneLabels) {
      // The panel already carries the name. Showing the label as well says it
      // twice, in two sizes, a few pixels apart.
      sprite.visible = chosenLabels.has(`zone:${zone.id}`) && !showingPanel.has(zone.id);
      const height = LABEL3D.ZONE_SIZE * perPixel;
      sprite.scale.set(height * aspect, height, 1);
      sprite.position.y = zone.position.y + zone.half.y + height;
    }
  };

  const applyCamera = (orbit: OrbitCamera, aspect: number): void => {
    const eye = eyePosition(orbit);
    camera.position.set(eye.x, eye.y, eye.z);
    camera.lookAt(orbit.target.x, orbit.target.y, orbit.target.z);
    camera.aspect = aspect > 0 ? aspect : 1;
    camera.updateProjectionMatrix();
    // The renderer would do this at draw time, but picking runs off pointer
    // events between draws — without it, a click is tested against wherever
    // the camera was standing last frame.
    camera.updateMatrixWorld();
  };

  /**
   * What is under the cursor.
   *
   * Aimed at the halo sprites rather than at the solids. A halo is billboarded
   * and several times the size of the body inside it, which gives a small
   * service the same generous target the plan's hit radius gives it — aiming
   * at the solids alone made a 7-unit model effectively unclickable from any
   * distance worth using. A region's name is tried only when nothing was hit,
   * and its shell is not a target at all — a shell encloses everything inside
   * it, so making it clickable would have every click meant for an agent land
   * on whichever regions happen to surround it instead.
   */
  const pick = (ndc: { x: number; y: number }): string | null => {
    // World matrices are refreshed by the renderer, so anything moved since
    // the last frame — or picked before the first one — would otherwise be
    // tested against where it used to be.
    scene.updateMatrixWorld();
    pointer.set(ndc.x, ndc.y);
    raycaster.setFromCamera(pointer, camera);
    for (const targets of [haloGroup.children, zoneGroup.children] as Object3D[][]) {
      const hits = raycaster.intersectObjects(targets, false);
      for (const hit of hits) {
        const id = hit.object.userData.nodeId;
        if (typeof id === 'string') return id;
      }
    }
    return null;
  };

  const dispose = (): void => {
    for (const id of [...labelSprites.keys()]) dropLabel(id);
    for (const item of disposables) item.dispose();
    disposables.length = 0;
    scene.traverse((object) => {
      const mesh = object as Partial<Mesh>;
      const material = mesh.material as Material | Material[] | undefined;
      if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
      else material?.dispose();
    });
    scene.clear();
  };

  paintEdges({
    now: 0,
    hoveredId: null,
    selectedId: null,
    litIds: null,
    meshMemberIds: null,
    reducedMotion,
    camera: { target: { x: 0, y: 0, z: 0 }, distance: 1, azimuth: 0, polar: 1 },
    viewportWidth: 1,
    viewportHeight: 1,
  });

  return { scene, camera, applyCamera, pick, update, dispose };
}

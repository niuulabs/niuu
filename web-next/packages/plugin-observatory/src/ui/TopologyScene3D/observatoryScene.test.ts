import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Line, LineLoop, LineSegments, Points, Sprite, type Object3D } from 'three';
import type { Topology, TopologyEdge, TopologyNode } from '../../domain';
import { computeLayout } from '../TopologyCanvas/layoutEngine';
import { buildTypeStyles, nodeStyle, type NodeStyle } from '../TopologyCanvas/nodeStyle';
import { buildScene3DModel, type Scene3DModel } from './sceneModel';
import {
  createObservatoryScene,
  boxWireframe,
  createShellMaterial,
  nodeAlpha,
  starPositions,
  toColor,
  type Scene3DFrame,
} from './observatoryScene';
import { defaultOrbitCamera } from './orbitCamera';
import { MESH_PULSE3D, NODE3D, STARS3D } from './scene3dConfig';
import { installCanvas2DMock } from './test-helpers';

const originalGetContext = HTMLCanvasElement.prototype.getContext;
const styleFor = (node: TopologyNode): NodeStyle => nodeStyle(node, buildTypeStyles(null));

function node(
  id: string,
  typeId: string,
  parentId: string | null = null,
  extra: Partial<TopologyNode> = {},
): TopologyNode {
  return { id, typeId, label: id, parentId, status: 'healthy', ...extra };
}

function edge(id: string, sourceId: string, targetId: string, extra: Partial<TopologyEdge> = {}) {
  return { id, sourceId, targetId, kind: 'solid' as const, ...extra };
}

const TOPOLOGY: Topology = {
  timestamp: '2026-08-08T00:00:00Z',
  nodes: [
    node('realm-a', 'realm'),
    node('cluster-a', 'cluster', 'realm-a'),
    node('host-a', 'host', 'cluster-a'),
    node('ravn-a', 'ravn_long', 'host-a', { flockId: 'forge' }),
    node('ravn-b', 'ravn_long', 'host-a', { flockId: 'forge' }),
    node('model-a', 'model', 'host-a', { location: 'external' }),
    node('mimir-a', 'mimir'),
  ],
  edges: [
    edge('e1', 'ravn-a', 'mimir-a', { relationType: 'reads', ratePerMinute: 20 }),
    edge('e2', 'ravn-b', 'model-a', { relationType: 'uses' }),
  ],
};

function buildModel(topology: Topology = TOPOLOGY): Scene3DModel {
  return buildScene3DModel(topology, computeLayout(topology), styleFor);
}

function frame(overrides: Partial<Scene3DFrame> = {}): Scene3DFrame {
  return {
    now: 1000,
    hoveredId: null,
    selectedId: null,
    litIds: null,
    meshMemberIds: null,
    reducedMotion: false,
    camera: defaultOrbitCamera(),
    viewportWidth: 960,
    viewportHeight: 600,
    ...overrides,
  };
}

/** A camera standing right next to a node, so nothing is lost to distance. */
function closeTo(model: Scene3DModel, nodeId: string) {
  return { ...defaultOrbitCamera(), target: model.nodeById.get(nodeId)!.position, distance: 200 };
}

/** How strongly one node's mark is drawn. */
function markOpacity(root: Object3D, nodeId: string): number {
  const mark = collect(
    root,
    (object) => object.userData.pickKind === 'mark' && object.userData.nodeId === nodeId,
  )[0]! as Sprite;
  return (mark.material as { opacity: number }).opacity;
}

/** How brightly one node's body is drawn, once it has arrived. */
function bodyOpacity(root: Object3D, nodeId: string): number {
  const body = collect(
    root,
    (object) => object.userData.pickKind === 'body' && object.userData.nodeId === nodeId,
  )[0]!;
  const edges = body.children.find((child) => child instanceof LineSegments) as LineSegments;
  return (edges.material as { opacity: number }).opacity;
}

/** A camera far enough back that every node is still only a mark. */
function farFrom(model: Scene3DModel, nodeId: string) {
  return {
    ...defaultOrbitCamera(),
    target: model.nodeById.get(nodeId)!.position,
    distance: NODE3D.BODY_FAR + 500,
  };
}

function collect(root: Object3D, predicate: (object: Object3D) => boolean): Object3D[] {
  const found: Object3D[] = [];
  root.traverse((object) => {
    if (predicate(object)) found.push(object);
  });
  return found;
}

beforeEach(installCanvas2DMock);
afterEach(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

describe('toColor', () => {
  it('reads an 0–255 triple, and can write into a colour it is handed', () => {
    const colour = toColor([255, 0, 0]);
    expect(colour.r).toBeGreaterThan(colour.g);
    const target = toColor([0, 0, 0]);
    expect(toColor([255, 255, 255], target)).toBe(target);
  });
});

describe('region shells', () => {
  it('brackets a cube at its corners rather than tracing all twelve edges', () => {
    // A complete wireframe box is a modelling-package artefact — twelve
    // identical hairlines that say "cuboid" and nothing else, and at the size
    // a realm occupies they are the heaviest thing in the frame.
    const arm = 0.25;
    const geometry = boxWireframe(arm);
    const position = geometry.getAttribute('position');
    // Eight corners, three arms each, two ends to an arm.
    expect(position.count).toBe(8 * 3 * 2);

    for (let i = 0; i < position.count; i += 1) {
      const axes = [position.getX(i), position.getY(i), position.getZ(i)];
      // Every vertex is on the box: either a corner, or an arm's far end,
      // which has come in along exactly one axis.
      const pulledIn = axes.filter((value) => Math.abs(Math.abs(value) - 1) > 1e-9);
      expect(pulledIn.length).toBeLessThanOrEqual(1);
      for (const value of pulledIn) expect(Math.abs(value)).toBeCloseTo(1 - arm, 9);
    }
    geometry.dispose();
  });

  it('keeps the middle of every face clear, whatever arm length it is given', () => {
    // The point of a bracket is the space it leaves for what is standing
    // inside the region.
    for (const arm of [0.05, 0.4, 5]) {
      const geometry = boxWireframe(arm);
      const position = geometry.getAttribute('position');
      let nearestToCentre = Infinity;
      for (let i = 0; i < position.count; i += 1) {
        nearestToCentre = Math.min(
          nearestToCentre,
          Math.max(
            Math.abs(position.getX(i)),
            Math.abs(position.getY(i)),
            Math.abs(position.getZ(i)),
          ),
        );
      }
      // Every vertex still lies on the box's surface, never inside it.
      expect(nearestToCentre).toBeCloseTo(1, 9);
      geometry.dispose();
    }
  });

  it('lights a shell brighter as it turns away from the eye', () => {
    // A flat wash on a sphere renders as a flat disc: nothing about a constant
    // opacity says which way the surface is facing.
    const material = createShellMaterial(toColor([120, 180, 255]), 0.05);
    expect(material.uniforms.uAlpha!.value).toBe(0.05);
    expect(material.uniforms.uRimGain!.value).toBeGreaterThan(1);
    expect(material.transparent).toBe(true);
    expect(material.depthWrite).toBe(false);
    material.dispose();
  });

  it('brackets a box and never cages a sphere, and graduates both', () => {
    // A cube seen three-quarter on is a hexagon in silhouette, and the rim
    // alone leaves it ambiguous which way the thing is squared up. A cage of
    // rings over a sphere's contents is all cost.
    const model = buildModel();
    const scene = createObservatoryScene(model);
    const boxes = model.zones.filter((zone) => zone.shape === 'box').length;
    const spheres = model.zones.filter((zone) => zone.shape === 'sphere').length;
    expect(boxes).toBeGreaterThan(0);
    expect(spheres).toBeGreaterThan(0);

    expect(collect(scene.scene, (object) => object.userData.role === 'bracket')).toHaveLength(
      boxes,
    );
    // Every region is graduated, whatever shape it is.
    expect(collect(scene.scene, (object) => object.userData.role === 'graduations')).toHaveLength(
      model.zones.length,
    );
    scene.dispose();
  });
});

describe('starPositions', () => {
  it('places every star on the shell', () => {
    const positions = starPositions(64, 1000);
    expect(positions).toHaveLength(64 * 3);
    for (let i = 0; i < 64; i += 1) {
      const radius = Math.hypot(positions[i * 3]!, positions[i * 3 + 1]!, positions[i * 3 + 2]!);
      expect(radius).toBeCloseTo(1000, 3);
    }
  });

  it('is identical on every call, so the sky does not reshuffle', () => {
    expect(Array.from(starPositions(32, 500))).toEqual(Array.from(starPositions(32, 500)));
  });
});

describe('nodeAlpha', () => {
  const built = buildModel().nodes.find((candidate) => candidate.id === 'ravn-a')!;

  it('shows everything when nothing is pointed at', () => {
    expect(nodeAlpha(built, frame())).toBe(1);
  });

  it('keeps what is pointed at, and what it touches, at full strength', () => {
    expect(nodeAlpha(built, frame({ hoveredId: 'ravn-a' }))).toBe(1);
    expect(nodeAlpha(built, frame({ hoveredId: 'x', litIds: new Set(['ravn-a']) }))).toBe(1);
  });

  it('steps everything else back', () => {
    expect(nodeAlpha(built, frame({ hoveredId: 'other', litIds: new Set() }))).toBe(
      NODE3D.DIMMED_ALPHA,
    );
  });

  it('lets a switched-off compute class outrank a hover', () => {
    // The operator has said they are not interested in that silicon at all;
    // pointing at it must not bring it back.
    expect(
      nodeAlpha(
        built,
        frame({ hoveredId: 'ravn-a', hiddenCompute: new Set([built.computeClass]) }),
      ),
    ).toBe(NODE3D.FILTERED_ALPHA);
  });
});

describe('createObservatoryScene', () => {
  it('gives every standing node a quiet mark, and every region an instrument', () => {
    const model = buildModel();
    const scene = createObservatoryScene(model);
    const standing = model.nodes.filter((candidate) => !candidate.isBoundary);

    const halos = collect(scene.scene, (object) => object.userData.pickKind === 'node');
    expect(halos.map((object) => object.userData.nodeId).sort()).toEqual(
      standing.map((candidate) => candidate.id).sort(),
    );

    // Camera-facing marks, so a node reads the same from any angle the
    // operator has orbited to — and small, because the instrument work belongs
    // to the regions.
    const marks = collect(scene.scene, (object) => object.userData.pickKind === 'mark');
    expect(marks).toHaveLength(standing.length);
    expect(marks.every((object) => object instanceof Sprite)).toBe(true);

    // A region's mark is its shell and its dial; its name is what is clicked.
    const names = collect(scene.scene, (object) => object.userData.pickKind === 'zone');
    expect(names.map((object) => object.userData.nodeId)).toContain('realm-a');
    expect(names.map((object) => object.userData.nodeId)).toContain('cluster-a');

    scene.dispose();
  });

  it('steps a host back so what runs on it stays the brighter thing', () => {
    const model = buildModel();
    const scene = createObservatoryScene(model);

    // Far off, both are marks.
    scene.update(frame({ camera: farFrom(model, 'host-a') }));
    expect(markOpacity(scene.scene, 'host-a')).toBeCloseTo(
      NODE3D.MARK_ALPHA * NODE3D.CONTAINER_OPACITY,
      6,
    );
    expect(markOpacity(scene.scene, 'ravn-a')).toBeCloseTo(NODE3D.MARK_ALPHA, 6);

    // Close to, both are bodies, and the same rule holds.
    scene.update(frame({ camera: closeTo(model, 'host-a') }));
    expect(bodyOpacity(scene.scene, 'host-a')).toBeCloseTo(
      NODE3D.BODY_EDGE_ALPHA * NODE3D.CONTAINER_OPACITY,
      6,
    );
    expect(bodyOpacity(scene.scene, 'ravn-a')).toBeCloseTo(NODE3D.BODY_EDGE_ALPHA, 6);
    scene.dispose();
  });

  it('cross-fades the mark into the body as the camera closes', () => {
    // Eighty wireframe bodies seen from across the estate is a smear that
    // hides everything it is drawn to show; one mark up close says nothing.
    const model = buildModel();
    const scene = createObservatoryScene(model);

    scene.update(frame({ camera: farFrom(model, 'ravn-a') }));
    expect(markOpacity(scene.scene, 'ravn-a')).toBeGreaterThan(0);
    expect(bodyOpacity(scene.scene, 'ravn-a')).toBeCloseTo(0, 6);

    scene.update(frame({ camera: closeTo(model, 'ravn-a') }));
    expect(markOpacity(scene.scene, 'ravn-a')).toBeCloseTo(0, 6);
    expect(bodyOpacity(scene.scene, 'ravn-a')).toBeGreaterThan(0);
    scene.dispose();
  });

  it('draws the starfield once, with the configured count', () => {
    const scene = createObservatoryScene(buildModel());
    const points = collect(scene.scene, (object) => object instanceof Points) as Points[];
    const stars = points.find(
      (candidate) => candidate.geometry.getAttribute('position').count === STARS3D.COUNT,
    );
    expect(stars).toBeDefined();
    scene.dispose();
  });

  it('builds an empty scene without complaint', () => {
    const scene = createObservatoryScene(buildScene3DModel(null, new Map(), styleFor));
    expect(() => scene.update(frame())).not.toThrow();
    scene.dispose();
  });

  it('points the camera at what it was told to look at', () => {
    const scene = createObservatoryScene(buildModel());
    scene.applyCamera({ ...defaultOrbitCamera(), target: { x: 100, y: 0, z: 0 } }, 1.6);
    expect(scene.camera.aspect).toBe(1.6);
    expect(scene.camera.position.length()).toBeGreaterThan(0);
    scene.applyCamera(defaultOrbitCamera(), 0);
    // A zero-width viewport must not produce a NaN projection matrix.
    expect(scene.camera.aspect).toBe(1);
    scene.dispose();
  });

  it('dims everything the operator is not tracing', () => {
    const model = buildModel();
    const scene = createObservatoryScene(model);
    scene.update(
      frame({ hoveredId: 'ravn-a', litIds: new Set(), camera: farFrom(model, 'ravn-a') }),
    );

    // What is pointed at keeps its body at any distance, so detail is never
    // unreachable; everything it does not touch steps back.
    expect(bodyOpacity(scene.scene, 'ravn-a')).toBeCloseTo(NODE3D.BODY_EDGE_ALPHA_EMPHASISED, 6);
    expect(markOpacity(scene.scene, 'ravn-b')).toBeCloseTo(
      NODE3D.MARK_ALPHA * NODE3D.DIMMED_ALPHA,
      6,
    );
    scene.dispose();
  });

  it('gives a region a dial, and only draws a gauge it has a figure for', () => {
    // An empty dial reads as a measurement of zero, which is a different claim
    // from having nothing to measure.
    const model = buildModel();
    const withFigures = createObservatoryScene(model, {
      readoutFor: (id) =>
        id === 'cluster-a'
          ? {
              id,
              title: 'cluster-a',
              rows: [{ label: 'RESIDENTS', value: '2' }],
              health: 0.5,
              trafficShare: 0.25,
            }
          : null,
    });
    const withNone = createObservatoryScene(model);

    const arcs = (scene: { scene: Object3D }) =>
      collect(scene.scene, (object) => object instanceof Line && !(object instanceof LineLoop));
    // Two gauges appear only when there are two figures to show.
    expect(arcs(withFigures).length).toBeGreaterThan(arcs(withNone).length);

    withFigures.dispose();
    withNone.dispose();
  });

  it('marks the members of the mesh being engaged with, and only them', () => {
    const scene = createObservatoryScene(buildModel());
    const pulses = () =>
      collect(scene.scene, (object) => object.userData.pulse === true).filter(
        (object) => object.visible,
      ).length;

    scene.update(frame());
    expect(pulses()).toBe(0);

    scene.update(frame({ meshMemberIds: new Set(['ravn-a', 'ravn-b']) }));
    // A steady halo plus two rings a half-cycle apart, per member.
    expect(pulses()).toBe(2 * 3);

    scene.update(frame());
    expect(pulses()).toBe(0);
    scene.dispose();
  });

  it('holds the pulse still, but still marks members, under reduced motion', () => {
    const scene = createObservatoryScene(buildModel(), { reducedMotion: true });
    scene.update(frame({ meshMemberIds: new Set(['ravn-a']) }));
    const visible = collect(scene.scene, (object) => object.userData.pulse === true).filter(
      (object) => object.visible,
    );
    // One halo and one held ring — motion is what was asked to stop, not the
    // marking.
    expect(visible).toHaveLength(2);
    expect(visible[0]!.scale.x).toBeCloseTo(
      buildModel().nodeById.get('ravn-a')!.radius * MESH_PULSE3D.HALO_SCALE,
    );
    scene.dispose();
  });

  it('moves the flow marks along their arcs, and only when time passes', () => {
    const scene = createObservatoryScene(buildModel());
    const motes = collect(scene.scene, (object) => object instanceof Points) as Points[];
    const flow = motes.find(
      (candidate) => candidate.geometry.getAttribute('position').count < STARS3D.COUNT,
    )!;

    scene.update(frame({ now: 0 }));
    const start = Array.from(flow.geometry.getAttribute('position').array);
    scene.update(frame({ now: 4000 }));
    const later = Array.from(flow.geometry.getAttribute('position').array);
    expect(later).not.toEqual(start);

    scene.update(frame({ now: 9000, reducedMotion: true }));
    const still = Array.from(flow.geometry.getAttribute('position').array);
    expect(still).toEqual(start);
    scene.dispose();
  });

  it('names what it has room for, and always names the selection', () => {
    const model = buildModel();
    const scene = createObservatoryScene(model);
    const labels = () => collect(scene.scene, (object) => object.userData.label === true);

    /** The loop always aims the camera before it draws; the layout needs that. */
    const look = (overrides: Partial<Scene3DFrame>) => {
      const next = frame(overrides);
      scene.applyCamera(next.camera, next.viewportWidth / next.viewportHeight);
      scene.update(next);
    };

    // Aimed at the estate, not at the world origin — a name off the side of
    // the stage is not a name competing for room on it.
    const at = model.nodeById.get('ravn-a')!.position;
    look({ camera: { ...defaultOrbitCamera(), target: at, distance: 600 } });
    const near = labels().length;
    expect(near).toBeGreaterThan(0);

    look({ camera: { ...defaultOrbitCamera(), target: at, distance: 13000 } });
    const far = labels().length;
    expect(far).toBeLessThan(near);

    // Whatever is selected keeps its name however far out the camera sits, so
    // detail is never unreachable.
    look({
      selectedId: 'ravn-a',
      camera: { ...defaultOrbitCamera(), target: at, distance: 13000 },
    });
    expect(labels().length).toBeGreaterThan(far);
    scene.dispose();
  });

  it('picks the node under the cursor, and reports empty space as empty', () => {
    const model = buildModel();
    const scene = createObservatoryScene(model);
    const target = model.nodeById.get('ravn-a')!;
    // Look straight at the node from close range, then aim at the centre of
    // the frame.
    scene.applyCamera(
      { target: target.position, distance: 400, azimuth: 0, polar: Math.PI / 2 - 0.05 },
      1.5,
    );
    expect(scene.pick({ x: 0, y: 0 })).toBe('ravn-a');
    expect(scene.pick({ x: -0.99, y: 0.99 })).toBeNull();
    scene.dispose();
  });

  it('picks a region by its name, and never by the shell around it', () => {
    // The shell encloses everything inside it. Were it a target, a click meant
    // for an agent would land on whichever regions happen to surround it — and
    // clicking empty sky would never clear the selection again.
    const model = buildModel();
    const scene = createObservatoryScene(model);
    const realm = model.zones.find((zone) => zone.kind === 'realm')!;
    const name = collect(
      scene.scene,
      (object) => object.userData.pickKind === 'zone' && object.userData.nodeId === realm.id,
    )[0]!;

    scene.applyCamera(
      { target: name.position.clone(), distance: 300, azimuth: 0, polar: Math.PI / 2 - 0.05 },
      1.5,
    );
    expect(scene.pick({ x: 0, y: 0 })).toBe(realm.id);

    // The middle of the realm, where its shell is but its name is not.
    scene.applyCamera({ target: realm.position, distance: 4000, azimuth: 0, polar: 0.2 }, 1.5);
    expect(scene.pick({ x: 0.97, y: -0.97 })).toBeNull();
    scene.dispose();
  });

  it('disposes everything it built', () => {
    const scene = createObservatoryScene(buildModel());
    scene.update(frame({ camera: { ...defaultOrbitCamera(), distance: 300 } }));
    scene.dispose();
    expect(scene.scene.children).toHaveLength(0);
  });

  it('builds without textures where there is no canvas to draw them on', () => {
    HTMLCanvasElement.prototype.getContext = (() => null) as never;
    const scene = createObservatoryScene(buildModel());
    expect(() => scene.update(frame({ meshMemberIds: new Set(['ravn-a']) }))).not.toThrow();
    scene.dispose();
  });
});

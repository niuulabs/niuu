import { describe, expect, it } from 'vitest';
import type { Topology, TopologyEdge, TopologyNode } from '../../domain';
import { computeLayout } from '../TopologyCanvas/layoutEngine';
import { buildTypeStyles, nodeStyle, type NodeStyle } from '../TopologyCanvas/nodeStyle';
import { OUTSIDE_COLOUR } from '../TopologyCanvas/renderer';
import { elevationFor } from './elevation';
import { EDGE3D, NODE3D, TIER, ZONE3D } from './scene3dConfig';
import {
  buildNode3D,
  buildScene3DModel,
  edgeArc,
  moteCountFor,
  node3dRadius,
  pointAlongArc,
  separationFactor,
  spreadPositions,
  volumeFor,
  type Node3D,
  type Zone3D,
} from './sceneModel';
import { vec3, type Vec3 } from './vec3';

const EMPTY_STYLES = new Map<string, { shape: string; size: number }>();
const styleFor = (node: TopologyNode): NodeStyle => nodeStyle(node, buildTypeStyles(null));

function node(
  id: string,
  typeId: string,
  parentId: string | null = null,
  extra: Partial<TopologyNode> = {},
): TopologyNode {
  return { id, typeId, label: id, parentId, status: 'healthy', ...extra };
}

function edge(
  id: string,
  sourceId: string,
  targetId: string,
  extra: Partial<TopologyEdge> = {},
): TopologyEdge {
  return { id, sourceId, targetId, kind: 'solid', ...extra };
}

/** A realm holding a cluster holding a host holding an agent, plus a Mímir. */
const TOPOLOGY: Topology = {
  timestamp: '2026-08-08T00:00:00Z',
  nodes: [
    node('realm-a', 'realm'),
    node('cluster-a', 'cluster', 'realm-a'),
    node('host-a', 'host', 'cluster-a'),
    node('ravn-a', 'ravn_long', 'host-a'),
    node('mimir-root', 'mimir'),
    node('mimir-nested', 'mimir', 'cluster-a'),
  ],
  edges: [
    edge('e-uses', 'ravn-a', 'mimir-root', { relationType: 'reads', ratePerMinute: 12 }),
    edge('e-contains', 'cluster-a', 'host-a', { relationType: 'contains' }),
    edge('e-self', 'ravn-a', 'ravn-a'),
    edge('e-missing', 'ravn-a', 'not-here'),
  ],
};

function build() {
  const positions = computeLayout(TOPOLOGY);
  return { positions, model: buildScene3DModel(TOPOLOGY, positions, styleFor) };
}

describe('node3dRadius', () => {
  it('scales up from the plan and never lets a node vanish', () => {
    expect(node3dRadius({ ...styleFor(node('x', 'service')), radius: 4 })).toBe(NODE3D.MIN_RADIUS);
    expect(node3dRadius({ ...styleFor(node('x', 'mimir')), radius: 40 })).toBe(40 * NODE3D.SCALE);
  });
});

describe('buildNode3D', () => {
  it('maps the plan’s y onto depth and takes height from the deck', () => {
    const source = node('ravn-a', 'ravn_long', 'host-a');
    const built = buildNode3D(source, { x: 12, y: -34 }, styleFor(source));
    expect(built.position).toEqual({ x: 12, y: TIER.LEAF, z: -34 });
  });

  it('marks every Mímir a well, and shrinks the ones inside a cluster', () => {
    const root = node('m1', 'mimir');
    const nested = node('m2', 'mimir', 'cluster-a');
    expect(buildNode3D(root, { x: 0, y: 0 }, styleFor(root)).wellScale).toBe(1);
    expect(buildNode3D(nested, { x: 0, y: 0 }, styleFor(nested)).wellScale).toBe(
      NODE3D.NESTED_WELL_SCALE,
    );
    expect(buildNode3D(nested, { x: 0, y: 0 }, styleFor(nested)).isWell).toBe(true);
  });
});

describe('region volumes', () => {
  it('draws a realm as a box and a cluster as a sphere', () => {
    // The plan draws addressable regions as rectangles and pools of compute as
    // circles; the model must not invent a second vocabulary for the same two
    // ideas.
    const { model } = build();
    expect(model.zones.find((zone) => zone.kind === 'realm')?.shape).toBe('box');
    expect(model.zones.find((zone) => zone.kind === 'cluster')?.shape).toBe('sphere');
  });

  it('encloses every one of its descendants, at every depth', () => {
    // This is the whole claim a container makes. A shell that clips through
    // one of its own agents makes it instead about that agent.
    const { model } = build();
    const inside = (zone: (typeof model.zones)[number], point: Vec3, radius: number) => {
      const d = {
        x: Math.abs(point.x - zone.position.x),
        y: Math.abs(point.y - zone.position.y),
        z: Math.abs(point.z - zone.position.z),
      };
      if (zone.shape === 'box') {
        return (
          d.x + radius <= zone.half.x + 1e-6 &&
          d.y + radius <= zone.half.y + 1e-6 &&
          d.z + radius <= zone.half.z + 1e-6
        );
      }
      // The ellipsoid test, taken at the far side of the node's own body: a
      // shell that merely contains a node's centre still cuts through it.
      const reach = Math.hypot(
        (d.x + radius) / zone.half.x,
        (d.y + radius) / zone.half.y,
        (d.z + radius) / zone.half.z,
      );
      return reach <= 1 + 1e-6;
    };

    const descendants = (id: string): string[] => {
      const found: string[] = [];
      for (const candidate of TOPOLOGY.nodes) {
        if (candidate.parentId !== id) continue;
        found.push(candidate.id, ...descendants(candidate.id));
      }
      return found;
    };

    for (const zone of model.zones) {
      for (const id of descendants(zone.id)) {
        const built = model.nodeById.get(id);
        // Nested regions have no body — nothing is drawn at their anchor, and
        // it is their shell that has to fit, which the next test checks.
        if (!built || built.isBoundary) continue;
        expect({
          zone: zone.id,
          node: id,
          inside: inside(zone, built.position, built.radius),
        }).toEqual({ zone: zone.id, node: id, inside: true });
      }
    }
  });

  it('encloses a nested region’s whole shell, not merely its contents', () => {
    // A cluster's sphere stands proud of the agents inside it. A realm sized
    // to those agents would have the sphere burst out of its own box.
    const { model } = build();
    const realm = model.zones.find((zone) => zone.kind === 'realm')!;
    const cluster = model.zones.find((zone) => zone.kind === 'cluster')!;

    for (const axis of ['x', 'y', 'z'] as const) {
      expect(cluster.position[axis] - cluster.half[axis]).toBeGreaterThanOrEqual(
        realm.position[axis] - realm.half[axis] - 1e-6,
      );
      expect(cluster.position[axis] + cluster.half[axis]).toBeLessThanOrEqual(
        realm.position[axis] + realm.half[axis] + 1e-6,
      );
    }
  });

  it('leaves room between a region and the shell of the one inside it', () => {
    // Two coincident surfaces are one surface as far as the eye is concerned.
    const { model } = build();
    const realm = model.zones.find((zone) => zone.kind === 'realm')!;
    const cluster = model.zones.find((zone) => zone.kind === 'cluster')!;
    expect(realm.half.x - cluster.half.x).toBeGreaterThan(20);
  });

  it('does not draw a boundary around nothing', () => {
    const bare: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('realm-empty', 'realm'), node('cluster-empty', 'cluster')],
      edges: [],
    };
    const model = buildScene3DModel(bare, computeLayout(bare), styleFor);
    expect(model.zones).toEqual([]);
  });

  it('gives a region holding one small thing a shell you can still see', () => {
    const tiny: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('cluster-a', 'cluster'), node('svc', 'service', 'cluster-a')],
      edges: [],
    };
    const model = buildScene3DModel(tiny, computeLayout(tiny), styleFor);
    expect(model.zones[0]!.half.x).toBeGreaterThanOrEqual(ZONE3D.MIN_HALF_EXTENT);
  });

  it('survives a parent cycle rather than recursing for ever', () => {
    const looped: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [
        { ...node('cluster-a', 'cluster'), parentId: 'cluster-b' },
        { ...node('cluster-b', 'cluster'), parentId: 'cluster-a' },
      ],
      edges: [],
    };
    expect(() => buildScene3DModel(looped, computeLayout(looped), styleFor)).not.toThrow();
  });
});

describe('making room', () => {
  it('leaves the shells clear of one another', () => {
    // Shells that touch read as regions that overlap, which is a claim about
    // the estate that nothing in the data supports.
    const { model } = build();
    for (let i = 0; i < model.zones.length; i += 1) {
      for (let j = i + 1; j < model.zones.length; j += 1) {
        const a = model.zones[i]!;
        const b = model.zones[j]!;
        if (a.shape !== 'sphere' || b.shape !== 'sphere') continue;
        // Unless one contains the other, which is what containment looks like.
        const gap = Math.hypot(
          a.position.x - b.position.x,
          a.position.y - b.position.y,
          a.position.z - b.position.z,
        );
        const nested = gap + Math.min(a.half.x, b.half.x) <= Math.max(a.half.x, b.half.x) + 1e-6;
        if (nested) continue;
        expect(gap).toBeGreaterThanOrEqual(a.half.x + b.half.x);
      }
    }
  });

  it('opens out the space between regions and leaves local packing alone', () => {
    // What is packed inside a host is the plan's arrangement, and stretching
    // it would send a host's own agents flying away from it.
    const spread = spreadPositions(TOPOLOGY.nodes, computeLayout(TOPOLOGY), 2);
    const plan = computeLayout(TOPOLOGY);

    const host = { plan: plan.get('host-a')!, spread: spread.get('host-a')! };
    const agent = { plan: plan.get('ravn-a')!, spread: spread.get('ravn-a')! };
    expect(agent.spread.x - host.spread.x).toBeCloseTo(agent.plan.x - host.plan.x, 6);

    // The cluster, which does carry a shell, moves away from its realm.
    const realm = { plan: plan.get('realm-a')!, spread: spread.get('realm-a')! };
    const cluster = { plan: plan.get('cluster-a')!, spread: spread.get('cluster-a')! };
    expect(cluster.spread.x - realm.spread.x).toBeCloseTo((cluster.plan.x - realm.plan.x) * 2, 6);
  });

  it('leaves the plan exactly as it found it when no room is needed', () => {
    const plan = computeLayout(TOPOLOGY);
    expect(spreadPositions(TOPOLOGY.nodes, plan, 1)).toBe(plan);
  });

  it('reports how much further apart two overlapping shells need to be', () => {
    const shell = (id: string, x: number, radius: number): Zone3D => ({
      id,
      kind: 'cluster',
      shape: 'sphere',
      position: { x, y: 0, z: 0 },
      half: { x: radius, y: radius, z: radius },
      label: id,
      colour: [0, 0, 0],
      fillAlpha: 0,
      edgeAlpha: 0,
    });
    const siblings = [node('a', 'cluster', 'p'), node('b', 'cluster', 'p')];
    const volumes = new Map([
      ['a', shell('a', -100, 120)],
      ['b', shell('b', 100, 120)],
    ]);

    const needed = separationFactor(volumes, new Map([['p', siblings]]), []);
    expect(needed).toBeGreaterThan(1);
    // Far enough apart, and it asks for nothing.
    const clear = new Map([
      ['a', shell('a', -1000, 120)],
      ['b', shell('b', 1000, 120)],
    ]);
    expect(separationFactor(clear, new Map([['p', siblings]]), [])).toBe(1);
  });

  it('does not ask for room it could never make', () => {
    // Two shells stacked on the same spot cannot be pulled apart by opening
    // out the ground plane, and asking for it would spread the estate for ever.
    const shell = (id: string): Zone3D => ({
      id,
      kind: 'cluster',
      shape: 'sphere',
      position: { x: 0, y: 0, z: 0 },
      half: { x: 200, y: 200, z: 200 },
      label: id,
      colour: [0, 0, 0],
      fillAlpha: 0,
      edgeAlpha: 0,
    });
    const siblings = [node('a', 'cluster', 'p'), node('b', 'cluster', 'p')];
    const volumes = new Map([
      ['a', shell('a')],
      ['b', shell('b')],
    ]);
    expect(separationFactor(volumes, new Map([['p', siblings]]), [])).toBe(1);
  });
});

describe('volumeFor', () => {
  const bounds = { min: vec3(-100, -60, -40), max: vec3(100, 60, 40) };
  const corners: Vec3[] = [];
  for (const x of [-100, 100]) {
    for (const y of [-60, 60]) {
      for (const z of [-40, 40]) corners.push(vec3(x, y, z));
    }
  }

  it('gives a box the bounds it was handed, plus clearance', () => {
    const { centre, half } = volumeFor('box', bounds, corners, 10);
    expect(centre).toEqual({ x: 0, y: 0, z: 0 });
    expect(half).toEqual({ x: 110, y: 70, z: 50 });
  });

  it('never lets an axis collapse below a size you can see', () => {
    const { half } = volumeFor('box', { min: vec3(0, 0, 0), max: vec3(0, 0, 0) }, [], 0);
    expect(half).toEqual({ x: 46, y: 46, z: 46 });
  });

  it('grows a sphere until it swallows the furthest thing it has to hold', () => {
    const { half } = volumeFor('sphere', bounds, corners, 0);
    for (const corner of corners) {
      expect(Math.hypot(corner.x, corner.y, corner.z)).toBeLessThanOrEqual(half.x + 1e-6);
    }
    expect(half.x).toBeCloseTo(Math.hypot(100, 60, 40), 6);
  });

  it('keeps a sphere round, however tall the region it holds', () => {
    // Fitted per axis it would come out an egg, and a squashed region says
    // something about the shape of the estate that is not true. The room it
    // needs is found by opening the plan out, not by deforming the region.
    const tall = { min: vec3(-100, -800, -100), max: vec3(100, 800, 100) };
    const { half } = volumeFor('sphere', tall, [vec3(0, 800, 0)], 0);
    expect(half.x).toBe(half.y);
    expect(half.y).toBe(half.z);
  });

  it('measures a sphere from what it actually holds, not from the box around it', () => {
    const { half } = volumeFor('sphere', bounds, [vec3(100, 0, 0)], 0);
    expect(half.x).toBe(100);
  });
});

describe('edgeArc', () => {
  const from: Node3D = {
    id: 'a',
    typeId: 'ravn_long',
    shape: 'agent',
    position: { x: 0, y: TIER.LEAF, z: 0 },
    radius: 10,
    colour: [1, 2, 3],
    computeClass: 'k8s',
    label: 'a',
    detail: '',
    labelTier: 'primary',
    isWell: false,
    wellScale: 1,
  };
  const to: Node3D = { ...from, id: 'b', position: { x: 600, y: TIER.LEAF, z: 0 } };

  it('starts and ends at the surfaces, not the centres', () => {
    const arc = edgeArc(edge('e', 'a', 'b'), from, to);
    expect(arc[0]!.x).toBeCloseTo(from.radius);
    expect(arc[arc.length - 1]!.x).toBeCloseTo(to.position.x - to.radius);
  });

  it('rises between its ends, so bundles separate by height', () => {
    const arc = edgeArc(edge('e', 'a', 'b'), from, to);
    const apex = Math.max(...arc.map((point) => point.y));
    expect(apex).toBeGreaterThan(TIER.LEAF);
  });

  it('lifts a longer connection higher than a short one', () => {
    const near: Node3D = { ...to, position: { x: 90, y: TIER.LEAF, z: 0 } };
    const lift = (target: Node3D) =>
      Math.max(...edgeArc(edge('e', 'a', 'b'), from, target).map((point) => point.y));
    expect(lift(to)).toBeGreaterThan(lift(near));
  });

  it('spreads parallel relations sideways without changing their height', () => {
    const manages = edgeArc(edge('e1', 'a', 'b', { relationType: 'manages' }), from, to);
    const reads = edgeArc(edge('e2', 'a', 'b', { relationType: 'reads' }), from, to);
    const mid = Math.floor(manages.length / 2);
    expect(manages[mid]!.z).not.toBeCloseTo(reads[mid]!.z);
    expect(manages[mid]!.y).toBeCloseTo(reads[mid]!.y);
  });

  it('returns one more point than the segments it was asked for', () => {
    expect(edgeArc(edge('e', 'a', 'b'), from, to, 4)).toHaveLength(5);
    expect(edgeArc(edge('e', 'a', 'b'), from, to)).toHaveLength(EDGE3D.SEGMENTS + 1);
  });
});

describe('buildScene3DModel', () => {
  it('returns an empty model, and nothing to frame, for no topology', () => {
    const model = buildScene3DModel(null, new Map(), styleFor);
    expect(model.nodes).toEqual([]);
    expect(model.edges).toEqual([]);
    expect(model.framePoints).toEqual([]);
  });

  it('places every laid-out node and indexes them by id', () => {
    const { model } = build();
    expect(model.nodes).toHaveLength(TOPOLOGY.nodes.length);
    expect(model.nodeById.get('ravn-a')?.position.y).toBe(TIER.LEAF);
    expect(model.nodeById.get('cluster-a')?.position.y).toBe(TIER.CLUSTER);
  });

  it('gives every container a shell and nothing else one', () => {
    const { model } = build();
    // Hosts are containers too, but they are objects with a body of their
    // own; a shell around each of them turned the inside of a cluster into a
    // thicket of overlapping outlines.
    expect(model.zones.map((zone) => zone.kind).sort()).toEqual(['cluster', 'realm']);
  });

  it('ties each node down to the deck its parent stands on', () => {
    const { model } = build();
    const riser = model.risers.find((candidate) => candidate.nodeId === 'host-a')!;
    expect(riser.to.y).toBe(TIER.CLUSTER);
    expect(riser.from.y).toBeLessThan(TIER.HOST);
    expect(riser.from.x).toBe(riser.to.x);
    expect(riser.from.z).toBe(riser.to.z);
  });

  it('draws no riser when parent and child share a deck', () => {
    // A session running on a host is a containment the decks cannot express,
    // and a riser with nowhere to descend to is a stub of a line pointing at
    // the node's own feet.
    const flat: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('host-a', 'host'), node('run-a', 'run', 'host-a')],
      edges: [],
    };
    const model = buildScene3DModel(flat, computeLayout(flat), styleFor);
    expect(elevationFor(flat.nodes[1]!)).toBe(elevationFor(flat.nodes[0]!));
    expect(model.risers.find((riser) => riser.nodeId === 'run-a')).toBeUndefined();
  });

  it('drops the edges that would draw nothing or say nothing', () => {
    const { model } = build();
    const ids = model.edges.map((candidate) => candidate.id);
    // Containment is the deck and the riser; a self-loop has no arc; an edge to
    // a node that was never placed has no second end.
    expect(ids).toEqual(['e-uses']);
  });

  it('colours an edge by its layer, and metered calls by where they land', () => {
    const outside: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [
        node('ravn-a', 'ravn_long'),
        node('model-a', 'model', null, { location: 'external' }),
      ],
      edges: [edge('e-infer', 'ravn-a', 'model-a', { relationType: 'uses' })],
    };
    const model = buildScene3DModel(outside, computeLayout(outside), styleFor);
    expect(model.edges[0]!.layer).toBe('inference');
    expect(model.edges[0]!.colour).toEqual([...OUTSIDE_COLOUR]);
  });

  it('offers the camera the extremes of every node and every shell', () => {
    const { model } = build();
    const spans = (pick: (point: { x: number; y: number; z: number }) => number) => ({
      min: Math.min(...model.framePoints.map(pick)),
      max: Math.max(...model.framePoints.map(pick)),
    });
    const x = spans((point) => point.x);
    const y = spans((point) => point.y);

    for (const built of model.nodes) {
      // Framed to a node's centre, half of it sits off the edge of the window.
      expect(x.min).toBeLessThanOrEqual(built.position.x - built.radius);
      expect(x.max).toBeGreaterThanOrEqual(built.position.x + built.radius);
      expect(y.min).toBeLessThanOrEqual(built.position.y - built.radius);
      expect(y.max).toBeGreaterThanOrEqual(built.position.y + built.radius);
    }
    for (const zone of model.zones) {
      expect(x.min).toBeLessThanOrEqual(zone.position.x - zone.half.x);
      expect(x.max).toBeGreaterThanOrEqual(zone.position.x + zone.half.x);
    }
  });

  it('skips a node the layout never placed', () => {
    const model = buildScene3DModel(TOPOLOGY, new Map(), styleFor);
    expect(model.nodes).toEqual([]);
    expect(model.zones).toEqual([]);
    expect(model.framePoints).toEqual([]);
  });

  it('does not draw a realm plate around nothing', () => {
    const bare: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('r', 'realm')],
      edges: [],
    };
    const model = buildScene3DModel(bare, computeLayout(bare), styleFor);
    expect(model.zones).toEqual([]);
  });

  it('resolves a style for a node the registry has never been taught', () => {
    expect(nodeStyle(node('x', 'brand-new'), EMPTY_STYLES).shape).toBe('box');
  });
});

describe('moteCountFor', () => {
  it('animates nothing on an edge nobody measured', () => {
    expect(moteCountFor(0)).toBe(0);
    expect(moteCountFor(-1)).toBe(0);
  });

  it('adds marks with the rate, up to a ceiling', () => {
    expect(moteCountFor(0.1)).toBe(1);
    expect(moteCountFor(EDGE3D.FLOW_SATURATION_PER_MINUTE)).toBe(EDGE3D.FLOW_MAX_MOTES);
    expect(moteCountFor(10_000)).toBe(EDGE3D.FLOW_MAX_MOTES);
  });
});

describe('pointAlongArc', () => {
  const arc = [
    { x: 0, y: 0, z: 0 },
    { x: 10, y: 0, z: 0 },
    { x: 20, y: 0, z: 0 },
  ];

  it('walks the polyline and wraps past its end', () => {
    expect(pointAlongArc(arc, 0)).toEqual({ x: 0, y: 0, z: 0 });
    expect(pointAlongArc(arc, 0.5)).toEqual({ x: 10, y: 0, z: 0 });
    expect(pointAlongArc(arc, 1.25)).toEqual({ x: 5, y: 0, z: 0 });
    expect(pointAlongArc(arc, -0.25).x).toBeCloseTo(15);
  });

  it('copes with a degenerate arc rather than reading off the end of it', () => {
    expect(pointAlongArc([], 0.5)).toEqual({ x: 0, y: 0, z: 0 });
    expect(pointAlongArc([{ x: 3, y: 4, z: 5 }], 0.9)).toEqual({ x: 3, y: 4, z: 5 });
  });
});

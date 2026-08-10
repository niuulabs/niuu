import { describe, expect, it } from 'vitest';
import type { TopologyNode } from '../../domain';
import {
  bundleWaypoint,
  crossContainerRoutePoints,
  edgeDrawPriority,
  edgeHash,
  edgeProfile,
  edgeRelationLane,
  identityRune,
  nodeColour,
  nodeEdgeRadius,
  nodeIconGlyph,
  nodeSwatchSize,
  parentChain,
  rgba,
  sharedAncestor,
  structureLabel,
  trimToNodeBoundary,
  workflowLabelPlacement,
} from './renderer';

const realm: TopologyNode = {
  id: 'realm-1',
  typeId: 'realm',
  label: 'asgard_realm',
  parentId: null,
  status: 'healthy',
};

const cluster: TopologyNode = {
  id: 'cluster-1',
  typeId: 'cluster',
  label: 'alpha-cluster',
  parentId: 'realm-1',
  status: 'healthy',
};

const runNode: TopologyNode = {
  id: 'run-1',
  typeId: 'run',
  label: 'run-one',
  parentId: 'cluster-1',
  status: 'healthy',
};

const hostNode: TopologyNode = {
  id: 'host-1',
  typeId: 'host',
  label: 'host_one',
  parentId: 'cluster-1',
  status: 'healthy',
};

describe('renderer helpers', () => {
  it('formats colors, runes, and structure labels', () => {
    expect(rgba([1, 2, 3], 0.5)).toBe('rgba(1,2,3,0.5)');
    expect(nodeColour('ting')).toEqual([125, 211, 252]);
    expect(nodeColour('unknown-type')).toEqual([148, 163, 184]);
    expect(identityRune('ravn_long')).toBeTruthy();
    expect(identityRune('warden')).toBe('ᚹ');
    expect(identityRune('service')).not.toBe(identityRune('ravn_long'));
    expect(identityRune('unknown')).toBe('');
    expect(nodeIconGlyph('ting')).toBe('ᚦ');
    expect(nodeIconGlyph('unknown')).toBe('U');
    expect(nodeSwatchSize('service')).toBe(20);
    expect(structureLabel(realm)).toBe('asgard_realm');
  });

  it('computes workflow label placements for different node groups', () => {
    expect(workflowLabelPlacement({ ...runNode, typeId: 'trigger' }, 12)).toMatchObject({
      align: 'center',
      baseline: 'alphabetic',
    });
    expect(
      workflowLabelPlacement({ ...runNode, layoutHints: { packGroup: 'resource' } }, 12),
    ).toMatchObject({
      align: 'right',
      baseline: 'middle',
    });
    expect(workflowLabelPlacement({ ...runNode, typeId: 'gate' }, 12)).toMatchObject({
      align: 'left',
      baseline: 'middle',
    });
    expect(workflowLabelPlacement({ ...runNode, typeId: 'end' }, 12)).toMatchObject({
      align: 'center',
      baseline: 'top',
    });
  });

  it('derives edge radii, trims points, and finds ancestor chains', () => {
    expect(nodeEdgeRadius(undefined)).toBe(8);
    expect(nodeEdgeRadius({ ...realm, typeId: 'mimir' })).toBe(nodeSwatchSize('mimir') / 2 + 3);
    expect(nodeEdgeRadius(hostNode)).toBeGreaterThan(20);
    expect(nodeEdgeRadius(runNode)).toBe(50);
    expect(nodeEdgeRadius({ ...realm, typeId: 'service' })).toBe(nodeSwatchSize('service') / 2 + 3);

    expect(
      trimToNodeBoundary(
        hostNode,
        { x: 0, y: 0, containerWidth: 120, containerHeight: 80 },
        { x: 120, y: 0 },
      ),
    ).toMatchObject({ y: 0 });
    expect(
      trimToNodeBoundary(
        runNode,
        { x: 0, y: 0, containerWidth: 100, containerHeight: 100 },
        { x: 100, y: 0 },
      ),
    ).toMatchObject({ y: 0 });
    expect(trimToNodeBoundary(realm, { x: 0, y: 0 }, { x: 0, y: 0 })).toMatchObject({ x: 0, y: 0 });

    const nodeById = new Map<string, TopologyNode>([
      [realm.id, realm],
      [cluster.id, cluster],
      [runNode.id, runNode],
      [hostNode.id, hostNode],
    ]);
    expect(parentChain(runNode, nodeById).map((node) => node.id)).toEqual(['cluster-1', 'realm-1']);
    expect(sharedAncestor(runNode, hostNode, nodeById)?.id).toBe('cluster-1');
    expect(sharedAncestor(runNode, undefined, nodeById)).toBeUndefined();
  });

  it('computes waypoints, hashes, and edge profiles for each style', () => {
    expect(bundleWaypoint({ x: 0, y: 0 }, { x: 10, y: 10 }, { x: 5, y: 5 }, 0.5)).toMatchObject({
      x: expect.any(Number),
      y: expect.any(Number),
    });
    expect(edgeHash('edge-a')).not.toBe(edgeHash('edge-b'));

    expect(edgeProfile('solid', 10)).toMatchObject({ dash: [], bend: 18 });
    expect(edgeProfile('dashed-short', 80).dash).toEqual([3, 5]);
    expect(edgeProfile('dashed-anim', 80).dashOffset).toBe(-1);
    expect(edgeProfile('dashed-long', 120).dash).toEqual([6, 4]);
    expect(edgeProfile('dashed-long', 120, 'reads').dash).toEqual([2, 4]);
    expect(edgeProfile('dashed-long', 110, 'writes')).toMatchObject({
      dash: [7, 3],
      dashOffset: -1,
    });
    expect(edgeProfile('dashed-anim', 52, 'signals_to')).toMatchObject({
      dash: [2, 3],
      dashOffset: -1,
    });
    expect(edgeProfile('dashed-anim', 96, 'observes')).toMatchObject({
      dash: [1, 5],
      dashOffset: -1,
    });
    expect(edgeProfile('soft', 10)).toMatchObject({ lineWidth: 0.85 });
    expect(edgeProfile('run', 10)).toMatchObject({ bend: 34 });
    expect(edgeProfile('mystery', 10)).toMatchObject({ lineWidth: 0.9 });
    expect(
      edgeRelationLane({
        id: 'write-edge',
        sourceId: 'a',
        targetId: 'b',
        kind: 'dashed-long',
        relationType: 'writes',
      }),
    ).toBe(-2);
    expect(
      edgeDrawPriority({
        id: 'run-edge',
        sourceId: 'a',
        targetId: 'b',
        kind: 'run',
      }),
    ).toBeGreaterThan(
      edgeDrawPriority({
        id: 'read-edge',
        sourceId: 'a',
        targetId: 'b',
        kind: 'dashed-long',
        relationType: 'reads',
      }),
    );
  });

  it('routes concrete cross-container relations around the shared container', () => {
    const points = crossContainerRoutePoints(
      { x: -80, y: -20 },
      { x: 80, y: 20 },
      { x: 0, y: 0 },
      {
        id: 'edge:volundr:session',
        sourceId: 'runtime:noatun:volundr:volundr:volundr',
        targetId: 'runtime:noatun:skuld:skuld:session',
        kind: 'solid',
        relationType: 'manages',
      },
    );

    expect(points).toHaveLength(5);
    expect(points[0]).toEqual({ x: -80, y: -20 });
    expect(points[4]).toEqual({ x: 80, y: 20 });
    expect(Math.hypot(points[2]!.x, points[2]!.y)).toBeGreaterThan(20);
  });
});

import { describe, expect, it } from 'vitest';
import type { WorkflowEdge, WorkflowNode } from './workflow';
import {
  estimateWorkflowNodeSize,
  feedbackLaneAssignments,
  nodeBounds,
  portAnchor,
} from './workflowGeometry';
import { nodePortCatalog } from './workflowPorts';

describe('workflow geometry', () => {
  it('anchors each measured port on the correct node boundary', () => {
    const node: WorkflowNode = {
      id: 'trigger',
      kind: 'trigger',
      label: 'Start',
      source: 'manual',
      dispatchEvent: 'research.requested',
      position: { x: 30, y: 50 },
    };
    const output = nodePortCatalog(node).outputs[0]!;
    const context = {
      measurements: new Map([[node.id, { width: 240, height: 80, portTop: 40 }]]),
    };
    expect(nodeBounds(node, context)).toEqual({ x: 30, y: 50, width: 240, height: 80 });
    expect(portAnchor(node, output, context)).toEqual({ x: 270, y: 110 });
  });

  it('assigns feedback lanes deterministically', () => {
    const makeEdge = (id: string, label: string): WorkflowEdge => ({
      id,
      source: 'b',
      target: 'a',
      label,
      cp1: { x: 0, y: 0 },
      cp2: { x: 0, y: 0 },
    });
    const lanes = feedbackLaneAssignments([
      makeEdge('z-repair', 'delivery.repair.required -> delivery.repair.required'),
      makeEdge('forward', 'research.analyzed -> research.analyzed'),
      makeEdge('a-retry', 'review.changes_requested -> plan.requested'),
    ]);
    expect([...lanes.entries()]).toEqual([
      ['a-retry', 0],
      ['z-repair', 1],
    ]);
  });

  it('estimates tall stage cards from members and connected typed ports', () => {
    const node: WorkflowNode = {
      id: 'research',
      kind: 'stage',
      label: 'Research',
      runId: null,
      personaIds: ['lead', 'critic', 'writer'],
      stageMembers: [],
      executionMode: 'parallel',
      maxConcurrent: 3,
      joinMode: 'all',
      position: { x: 0, y: 0 },
    };
    const edges: WorkflowEdge[] = [
      {
        id: 'in',
        source: 'start',
        target: 'research',
        label: 'research.requested -> research.requested',
        cp1: { x: 0, y: 0 },
        cp2: { x: 0, y: 0 },
      },
      {
        id: 'out',
        source: 'research',
        target: 'done',
        label: 'research.completed -> research.completed',
        cp1: { x: 0, y: 0 },
        cp2: { x: 0, y: 0 },
      },
    ];
    expect(estimateWorkflowNodeSize(node, { edges })).toEqual({
      width: 224,
      height: 172,
      portTop: 158,
    });
  });
});

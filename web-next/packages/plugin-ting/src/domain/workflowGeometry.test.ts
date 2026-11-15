import { describe, expect, it } from 'vitest';
import type { WorkflowEdge, WorkflowNode } from './workflow';
import {
  CARD_HEADER_HEIGHT,
  CARD_PORT_PAD,
  CARD_PORT_ROW,
  CARD_WIDTH,
  END_CARD_HEIGHT,
  END_CARD_WIDTH,
  estimateWorkflowNodeSize,
  feedbackLaneAssignments,
  nodeBounds,
  portAnchor,
  workflowCardSize,
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

  it('sizes a stage card by its port rows, not by how many members it has', () => {
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
    // One row: research.requested in, research.completed out.
    expect(estimateWorkflowNodeSize(node, { edges })).toEqual({
      width: CARD_WIDTH,
      height: CARD_HEADER_HEIGHT + CARD_PORT_PAD * 2 + CARD_PORT_ROW,
      portTop: CARD_HEADER_HEIGHT + CARD_PORT_PAD,
      portBottom: CARD_PORT_PAD,
    });
  });

  it('sizes an include card at its base size with no connected ports', () => {
    const node: WorkflowNode = {
      id: 'delivery-planning',
      kind: 'include',
      label: 'Plan the delivery',
      workflow: 'planning',
      nodes: { 'planning-analysis': 'delivery-plan-author' },
      position: { x: 0, y: 0 },
    };
    expect(estimateWorkflowNodeSize(node)).toEqual({
      width: CARD_WIDTH,
      height: CARD_HEADER_HEIGHT,
    });
  });

  it('grows an include card to fit ports derived from its provided ids', () => {
    const node: WorkflowNode = {
      id: 'delivery-planning',
      kind: 'include',
      label: 'Plan the delivery',
      workflow: 'planning',
      nodes: { 'planning-analysis': 'delivery-plan-author' },
      position: { x: 0, y: 0 },
    };
    const edges: WorkflowEdge[] = [
      {
        id: 'in',
        source: 'trigger',
        target: 'delivery-plan-author',
        label: 'plan.requested -> plan.requested',
        cp1: { x: 0, y: 0 },
        cp2: { x: 0, y: 0 },
      },
    ];
    expect(estimateWorkflowNodeSize(node, { edges })).toEqual({
      width: CARD_WIDTH,
      height: CARD_HEADER_HEIGHT + CARD_PORT_PAD * 2 + CARD_PORT_ROW,
      portTop: CARD_HEADER_HEIGHT + CARD_PORT_PAD,
      portBottom: CARD_PORT_PAD,
    });
  });

  it('draws the end of a workflow as a pill whatever its ports', () => {
    expect(workflowCardSize('end', 3)).toEqual({
      width: END_CARD_WIDTH,
      height: END_CARD_HEIGHT,
    });
  });
});

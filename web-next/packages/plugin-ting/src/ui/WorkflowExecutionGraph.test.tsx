import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type {
  WorkflowExecutionEdge,
  WorkflowExecutionNode,
  WorkflowExecutionStatus,
} from '../domain/workflowExecutionGraph';
import { WorkflowExecutionGraph, layoutExecutionNodes } from './WorkflowExecutionGraph';

const baseNodes: WorkflowExecutionNode[] = [
  {
    id: 'plan',
    label: 'Plan the implementation and identify affected contracts',
    kind: 'author',
    status: 'recorded',
    detail: 'A public planning outcome is available; lifecycle completion is not implied.',
    outputs: [
      {
        id: 'summary',
        label: 'Summary',
        format: 'markdown',
        value: '# Plan accepted\n- Preserve **public contracts**\n- Add `verification`',
        isPublic: true,
      },
      {
        id: 'result',
        label: 'Result data',
        format: 'structured',
        value: { passed: true, requirements: ['REQ-1'] },
        evidence: [{ id: 'receipt', label: 'Signed receipt', kind: 'receipt' }],
      },
    ],
    events: [
      {
        id: 'earlier',
        eventType: 'plan.authored',
        persona: 'author',
        timestamp: '2026-09-19T10:00:00Z',
        summary: 'The author published the initial plan.',
      },
      {
        id: 'later',
        eventType: 'plan.reviewed',
        persona: 'reviewer',
        verdict: 'accepted',
        timestamp: '2026-09-19T11:00:00Z',
        summary: 'The reviewer accepted the revised plan.',
        fields: { revision: 2 },
        evidence: [{ id: 'review', label: 'Review outcome' }],
      },
    ],
    childWorkflows: [
      {
        id: 'child-1',
        label: 'Implement API changes',
        status: 'running',
        detail: 'Child workflow run 4',
      },
    ],
  },
  {
    id: 'build',
    label: 'Build and verify the candidate',
    kind: 'workstream',
    status: 'running',
    outputs: [],
    events: [],
  },
];

const baseEdges: WorkflowExecutionEdge[] = [
  {
    id: 'plan-build',
    source: 'plan',
    target: 'build',
    label: 'plan.accepted',
    status: 'running',
  },
];

describe('WorkflowExecutionGraph', () => {
  it('renders every execution status without treating recorded output as completed', () => {
    const statuses: WorkflowExecutionStatus[] = [
      'unobserved',
      'recorded',
      'running',
      'completed',
      'failed',
      'waiting',
    ];
    const nodes = statuses.map<WorkflowExecutionNode>((status, index) => ({
      id: status,
      label: `${status} node`,
      kind: 'step',
      status,
      position: { x: index * 260, y: 20 },
    }));

    render(<WorkflowExecutionGraph nodes={nodes} edges={[]} />);

    for (const status of statuses) {
      expect(screen.getByTestId(`execution-node-${status}`)).toHaveAttribute('data-status', status);
    }
    expect(screen.getByTestId('execution-node-recorded')).toHaveAccessibleName(
      'recorded node, Output recorded',
    );
    expect(screen.getByTestId('execution-node-completed')).toHaveAccessibleName(
      'completed node, Completed',
    );
  });

  it('shows useful empty states for both an empty graph and an observed node with no output', () => {
    const { rerender } = render(
      <WorkflowExecutionGraph nodes={[]} edges={[]} emptyLabel="Waiting for a projected trace." />,
    );

    expect(screen.getByText('Waiting for a projected trace.')).toBeInTheDocument();
    expect(screen.getByText('Select a node')).toBeInTheDocument();

    rerender(<WorkflowExecutionGraph nodes={[baseNodes[1]!]} edges={[]} />);
    expect(screen.getByText('No public output has been recorded.')).toBeInTheDocument();
    expect(
      screen.getByText('No public history has been recorded for this node.'),
    ).toBeInTheDocument();
  });

  it('preserves authoritative public history order and delegates evidence and child workflow actions', () => {
    const onOpenEvidence = vi.fn();
    const onOpenChildWorkflow = vi.fn();
    render(
      <WorkflowExecutionGraph
        nodes={baseNodes}
        edges={baseEdges}
        onOpenEvidence={onOpenEvidence}
        onOpenChildWorkflow={onOpenChildWorkflow}
      />,
    );

    const historySection = screen.getByRole('heading', { name: 'History' }).closest('section');
    expect(historySection).not.toBeNull();
    const historyItems = within(historySection!).getAllByRole('listitem');
    expect(historyItems[0]).toHaveTextContent('plan.authored');
    expect(historyItems[1]).toHaveTextContent('plan.reviewed');
    expect(screen.getByText('Structured fields')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Evidence · Review outcome' }));
    expect(onOpenEvidence).toHaveBeenCalledWith(expect.objectContaining({ id: 'review' }), {
      nodeId: 'plan',
      eventId: 'later',
    });

    fireEvent.click(screen.getByRole('button', { name: /Implement API changes/ }));
    expect(onOpenChildWorkflow).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'child-1' }),
      expect.objectContaining({ id: 'plan' }),
    );
  });

  it('switches between markdown and structured outputs and preserves evidence context', () => {
    const onOpenEvidence = vi.fn();
    render(
      <WorkflowExecutionGraph
        nodes={baseNodes}
        edges={baseEdges}
        onOpenEvidence={onOpenEvidence}
      />,
    );

    expect(screen.getByRole('heading', { name: 'Plan accepted' })).toBeInTheDocument();
    expect(screen.getByText('public contracts')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(screen.getByRole('tab', { name: 'Result data' }));
    expect(screen.getByText(/"passed": true/)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Result data' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Evidence · Signed receipt' }));
    expect(onOpenEvidence).toHaveBeenCalledWith(expect.objectContaining({ id: 'receipt' }), {
      nodeId: 'plan',
      outputId: 'result',
    });
  });

  it('supports keyboard zoom, traversal, and direct node selection', () => {
    const onSelectedNodeChange = vi.fn();
    render(
      <WorkflowExecutionGraph
        nodes={baseNodes}
        edges={baseEdges}
        onSelectedNodeChange={onSelectedNodeChange}
      />,
    );

    const canvas = screen.getByRole('application');
    expect(screen.getByText('115%')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: '+' });
    expect(screen.getByText('138%')).toBeInTheDocument();

    fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    expect(onSelectedNodeChange).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'build' }));
    expect(screen.getByLabelText('Build and verify the candidate inspector')).toBeInTheDocument();

    const planNode = screen.getByTestId('execution-node-plan');
    fireEvent.keyDown(planNode, { key: 'Enter' });
    expect(onSelectedNodeChange).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'plan' }));
    expect(planNode).toHaveAttribute('aria-pressed', 'true');
  });

  it('preserves the viewport when polling supplies new arrays with unchanged geometry', () => {
    const { rerender } = render(<WorkflowExecutionGraph nodes={baseNodes} edges={baseEdges} />);

    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(screen.getByText('138%')).toBeInTheDocument();

    rerender(
      <WorkflowExecutionGraph
        nodes={baseNodes.map((node) => ({ ...node, status: 'completed' }))}
        edges={baseEdges.map((edge) => ({ ...edge }))}
      />,
    );

    expect(screen.getByText('138%')).toBeInTheDocument();
  });

  it('supports the remaining viewport controls, wheel directions, and pointer panning', () => {
    const onSelectedNodeChange = vi.fn();
    render(
      <WorkflowExecutionGraph
        nodes={baseNodes}
        edges={baseEdges}
        onSelectedNodeChange={onSelectedNodeChange}
      />,
    );

    const canvas = screen.getByRole('application');
    const transformedGraph = canvas.querySelector('g[transform]');
    expect(transformedGraph).not.toBeNull();

    fireEvent.keyDown(canvas, { key: '-' });
    expect(screen.getByText('96%')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: 'f' });
    expect(screen.getByText('115%')).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: 'Escape' });
    expect(screen.getByText('115%')).toBeInTheDocument();

    fireEvent.wheel(canvas, { deltaY: -10 });
    expect(screen.getByText('129%')).toBeInTheDocument();
    fireEvent.wheel(canvas, { deltaY: 10 });
    expect(screen.getByText('115%')).toBeInTheDocument();

    fireEvent.keyDown(canvas, { key: 'ArrowLeft' });
    expect(onSelectedNodeChange).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'build' }));

    const beforePan = transformedGraph!.getAttribute('transform');
    fireEvent.pointerDown(canvas, { button: 1, pointerId: 2, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 80, clientY: 90 });
    expect(transformedGraph).toHaveAttribute('transform', beforePan);

    fireEvent.pointerDown(canvas, { button: 0, pointerId: 3, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { pointerId: 4, clientX: 80, clientY: 90 });
    expect(transformedGraph).toHaveAttribute('transform', beforePan);
    fireEvent.pointerMove(canvas, { pointerId: 3, clientX: 80, clientY: 90 });
    expect(transformedGraph!.getAttribute('transform')).not.toBe(beforePan);
    fireEvent.pointerUp(canvas, { pointerId: 3 });
    fireEvent.pointerCancel(canvas, { pointerId: 3 });

    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    fireEvent.click(screen.getByRole('button', { name: 'Fit' }));
    expect(screen.getByText('115%')).toBeInTheDocument();
  });

  it('honors controlled selection while still reporting attempted node changes', () => {
    const onSelectedNodeChange = vi.fn();
    render(
      <WorkflowExecutionGraph
        nodes={baseNodes}
        edges={baseEdges}
        selectedNodeId={null}
        onSelectedNodeChange={onSelectedNodeChange}
      />,
    );

    const planNode = screen.getByTestId('execution-node-plan');
    fireEvent.click(planNode);
    expect(onSelectedNodeChange).toHaveBeenCalledWith(expect.objectContaining({ id: 'plan' }));
    expect(planNode).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText('Select a node')).toBeInTheDocument();
  });

  it('renders reverse, vertical, missing, and minimally observed graph projections', () => {
    const nodes: WorkflowExecutionNode[] = [
      {
        id: 'origin',
        label: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890',
        kind: 'step',
        status: 'waiting',
        position: { x: 500, y: 500 },
        outputs: [
          { id: 'empty', label: 'Empty note', format: 'markdown', value: '' },
          { id: 'undefined', label: 'Missing value', format: 'structured', value: undefined },
        ],
        events: [
          {
            id: 'invalid-time',
            eventType: 'activity.recorded',
            verdict: 'noted',
            timestamp: 'time unavailable',
            summary: 'A public activity was recorded.',
            fields: {},
          },
        ],
        evidence: [{ id: 'node-evidence', label: 'Node manifest' }],
        childWorkflows: [{ id: 'child', label: 'Child without state' }],
      },
      {
        id: 'left',
        label: 'Left node',
        kind: 'step',
        status: 'unobserved',
        position: { x: 100, y: 500 },
      },
      {
        id: 'above',
        label: 'Above node',
        kind: 'step',
        status: 'completed',
        position: { x: 500, y: 100 },
      },
    ];
    const edges: WorkflowExecutionEdge[] = [
      { id: 'reverse', source: 'origin', target: 'left' },
      {
        id: 'vertical',
        source: 'origin',
        target: 'above',
        status: 'unobserved',
        label: 'very.long.edge.label.that.must.be.truncated.for.display',
      },
      { id: 'missing-target', source: 'origin', target: 'missing' },
      { id: 'missing-source', source: 'missing', target: 'origin' },
    ];

    render(<WorkflowExecutionGraph nodes={nodes} edges={edges} />);

    expect(screen.getByTestId('execution-edge-reverse')).toBeInTheDocument();
    expect(screen.getByTestId('execution-edge-vertical')).toBeInTheDocument();
    expect(screen.queryByTestId('execution-edge-missing-target')).not.toBeInTheDocument();
    expect(screen.queryByTestId('execution-edge-missing-source')).not.toBeInTheDocument();
    expect(screen.getByText(/very\.long\.edge\.label/)).toBeInTheDocument();
    expect(screen.getByText('No content was recorded.')).toBeInTheDocument();
    expect(screen.getByText('time unavailable')).toBeInTheDocument();
    expect(screen.getByText('noted')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Evidence · Node manifest' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Child without state' })).toBeDisabled();

    fireEvent.click(screen.getByRole('tab', { name: 'Missing value' }));
    expect(screen.getByText('null')).toBeInTheDocument();
  });

  it('lays out cyclic, positionless graphs deterministically in vertical bands', () => {
    const nodes = Array.from({ length: 12 }, (_, index): WorkflowExecutionNode => ({
      id: `node-${index}`,
      label: `Node ${index}`,
      kind: 'step',
      status: 'unobserved',
    }));
    nodes[3] = { ...nodes[3]!, position: { x: 17, y: 29 } };
    const edges = Array.from({ length: 11 }, (_, index): WorkflowExecutionEdge => ({
      id: `edge-${index}`,
      source: `node-${index}`,
      target: `node-${index + 1}`,
    }));
    edges.push({ id: 'repair-cycle', source: 'node-11', target: 'node-0' });
    edges.push({ id: 'unknown-source', source: 'unknown', target: 'node-1' });
    edges.push({ id: 'unknown-target', source: 'node-1', target: 'unknown' });

    const first = layoutExecutionNodes(nodes, edges);
    const second = layoutExecutionNodes(nodes, edges);

    expect([...first.entries()]).toEqual([...second.entries()]);
    expect(first.get('node-3')).toEqual({ x: 17, y: 29 });
    expect(new Set([...first.values()].map((position) => position.x)).size).toBeLessThanOrEqual(5);
    expect(first.get('node-11')!.y).toBeGreaterThan(first.get('node-0')!.y);
  });
});

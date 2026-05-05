import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PipelineView } from './PipelineView';
import type { WorkflowNode, WorkflowEdge } from '../../domain/workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const stageA: WorkflowNode = {
  id: 'stage-a',
  kind: 'stage',
  label: 'Stage A',
  raidId: null,
  personaIds: [],
  position: { x: 0, y: 0 },
};

const stageB: WorkflowNode = {
  id: 'stage-b',
  kind: 'stage',
  label: 'Stage B',
  raidId: null,
  personaIds: ['persona-build'],
  position: { x: 200, y: 0 },
};

const gateC: WorkflowNode = {
  id: 'gate-c',
  kind: 'gate',
  label: 'Gate C',
  condition: 'ok',
  position: { x: 400, y: 0 },
};

const condD: WorkflowNode = {
  id: 'cond-d',
  kind: 'cond',
  label: 'Cond D',
  predicate: 'x > 0',
  position: { x: 600, y: 0 },
};

const linearEdges: WorkflowEdge[] = [
  { id: 'e1', source: 'stage-a', target: 'stage-b', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
  { id: 'e2', source: 'stage-b', target: 'gate-c', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
  { id: 'e3', source: 'gate-c', target: 'cond-d', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PipelineView', () => {
  it('renders the pipeline-view container', () => {
    render(
      <PipelineView
        nodes={[stageA, stageB]}
        edges={[
          {
            id: 'e1',
            source: 'stage-a',
            target: 'stage-b',
            cp1: { x: 80, y: 0 },
            cp2: { x: -80, y: 0 },
          },
        ]}
      />,
    );
    expect(screen.getByTestId('pipeline-view')).toBeInTheDocument();
  });

  it('renders empty state when nodes is empty', () => {
    render(<PipelineView nodes={[]} edges={[]} />);
    expect(screen.getByTestId('pipeline-view')).toBeInTheDocument();
    expect(screen.getByText(/no nodes/i)).toBeInTheDocument();
  });

  it('renders a node button for each node', () => {
    render(<PipelineView nodes={[stageA, stageB, gateC, condD]} edges={linearEdges} />);
    expect(screen.getByTestId('pipeline-node-stage-a')).toBeInTheDocument();
    expect(screen.getByTestId('pipeline-node-stage-b')).toBeInTheDocument();
    expect(screen.getByTestId('pipeline-node-gate-c')).toBeInTheDocument();
    expect(screen.getByTestId('pipeline-node-cond-d')).toBeInTheDocument();
  });

  it('displays node labels', () => {
    render(<PipelineView nodes={[stageA]} edges={[]} />);
    expect(screen.getByText('Stage A')).toBeInTheDocument();
  });

  it('calls onSelectNode when a node is clicked', () => {
    const onSelectNode = vi.fn();
    render(
      <PipelineView
        nodes={[stageA, stageB]}
        edges={[
          {
            id: 'e1',
            source: 'stage-a',
            target: 'stage-b',
            cp1: { x: 80, y: 0 },
            cp2: { x: -80, y: 0 },
          },
        ]}
        onSelectNode={onSelectNode}
      />,
    );
    fireEvent.click(screen.getByTestId('pipeline-node-stage-a'));
    expect(onSelectNode).toHaveBeenCalledWith('stage-a');
  });

  it('marks the selected node', () => {
    render(
      <PipelineView
        nodes={[stageA, stageB]}
        edges={[
          {
            id: 'e1',
            source: 'stage-a',
            target: 'stage-b',
            cp1: { x: 80, y: 0 },
            cp2: { x: -80, y: 0 },
          },
        ]}
        selectedNodeId="stage-a"
      />,
    );
    expect(screen.getByTestId('pipeline-node-stage-a')).toHaveAttribute('data-selected', 'true');
    expect(screen.getByTestId('pipeline-node-stage-b')).not.toHaveAttribute('data-selected');
  });

  it('shows stage persona count', () => {
    render(<PipelineView nodes={[stageB]} edges={[]} />);
    expect(screen.getByText(/1 persona/)).toBeInTheDocument();
  });

  it('shows cycle nodes section when a cycle is present', () => {
    const cyclicNodes: WorkflowNode[] = [
      {
        id: 'a',
        kind: 'stage',
        label: 'A',
        raidId: null,
        personaIds: [],
        position: { x: 0, y: 0 },
      },
      {
        id: 'b',
        kind: 'stage',
        label: 'B',
        raidId: null,
        personaIds: [],
        position: { x: 200, y: 0 },
      },
    ];
    const cyclicEdges: WorkflowEdge[] = [
      { id: 'e1', source: 'a', target: 'b', cp1: { x: 80, y: 0 }, cp2: { x: -80, y: 0 } },
      { id: 'e2', source: 'b', target: 'a', cp1: { x: -80, y: 0 }, cp2: { x: 80, y: 0 } },
    ];
    render(<PipelineView nodes={cyclicNodes} edges={cyclicEdges} />);
    expect(screen.getByText(/cycle nodes/i)).toBeInTheDocument();
  });

  it('shows layer labels for linear chain', () => {
    render(<PipelineView nodes={[stageA, stageB, gateC, condD]} edges={linearEdges} />);
    // Each node in a different layer
    expect(screen.getByText('Layer 0')).toBeInTheDocument();
    expect(screen.getByText('Layer 1')).toBeInTheDocument();
  });

  it('does not call onSelectNode when no handler provided', () => {
    render(<PipelineView nodes={[stageA]} edges={[]} />);
    // clicking should not throw
    expect(() => fireEvent.click(screen.getByTestId('pipeline-node-stage-a'))).not.toThrow();
  });
});

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { GraphView } from './GraphView';
import type { WorkflowNode, WorkflowEdge } from '../../domain/workflow';
import { MIMIR_MOUNT_MIME, serializeWorkflowRegistryMount } from './mimirRegistry';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const stageNode: WorkflowNode = {
  id: 'stage-1',
  kind: 'stage',
  label: 'Stage 1',
  raidId: null,
  personaIds: [],
  position: { x: 100, y: 100 },
};

const gateNode: WorkflowNode = {
  id: 'gate-1',
  kind: 'gate',
  label: 'Gate',
  condition: 'ok',
  position: { x: 300, y: 100 },
};

const condNode: WorkflowNode = {
  id: 'cond-1',
  kind: 'cond',
  label: 'Cond',
  predicate: 'x > 0',
  position: { x: 500, y: 100 },
};

const triggerNode: WorkflowNode = {
  id: 'trigger-1',
  kind: 'trigger',
  label: 'Start',
  source: 'manual dispatch',
  dispatchEvent: 'code.requested',
  position: { x: 40, y: 80 },
};

const endNode: WorkflowNode = {
  id: 'end-1',
  kind: 'end',
  label: 'Done',
  position: { x: 700, y: 80 },
};

const edge: WorkflowEdge = {
  id: 'e1',
  source: 'stage-1',
  target: 'gate-1',
  cp1: { x: 80, y: 0 },
  cp2: { x: -80, y: 0 },
};

function defaultProps() {
  return {
    nodes: [stageNode, gateNode, condNode],
    edges: [edge],
    selectedNodeId: null,
    connectingFromId: null,
    onSelectNode: vi.fn(),
    onInspectNode: vi.fn(),
    onAddNode: vi.fn(),
    onAddMimirResource: vi.fn(),
    onDeleteNode: vi.fn(),
    onMoveNode: vi.fn(),
    onStartConnect: vi.fn(),
    onCancelConnect: vi.fn(),
    onCompleteConnect: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphView', () => {
  it('renders the graph-view container', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  });

  it('renders the SVG canvas', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('graph-canvas')).toBeInTheDocument();
  });

  it('renders a node element for each node', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('workflow-node-stage-1')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-gate-1')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-cond-1')).toBeInTheDocument();
  });

  it('renders an edge element for each edge', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('workflow-edge-e1')).toBeInTheDocument();
  });

  it('node elements have correct data-kind attributes', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('workflow-node-stage-1')).toHaveAttribute('data-kind', 'stage');
    expect(screen.getByTestId('workflow-node-gate-1')).toHaveAttribute('data-kind', 'gate');
    expect(screen.getByTestId('workflow-node-cond-1')).toHaveAttribute('data-kind', 'cond');
  });

  it('shows data-selected on selected node', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getByTestId('workflow-node-stage-1')).toHaveAttribute('data-selected', 'true');
  });

  it('does not show data-selected on unselected node', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getByTestId('workflow-node-gate-1')).not.toHaveAttribute('data-selected');
  });

  it('renders add-stage toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('add-stage')).toBeInTheDocument();
  });

  it('renders add-gate toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('add-gate')).toBeInTheDocument();
  });

  it('renders add-resource toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('add-resource')).toBeInTheDocument();
  });

  it('renders add-cond toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('add-cond')).toBeInTheDocument();
  });

  it('calls onAddNode("stage") when add-stage clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('add-stage'));
    expect(props.onAddNode).toHaveBeenCalledWith('stage');
  });

  it('calls onAddNode("gate") when add-gate clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('add-gate'));
    expect(props.onAddNode).toHaveBeenCalledWith('gate');
  });

  it('calls onAddNode("cond") when add-cond clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('add-cond'));
    expect(props.onAddNode).toHaveBeenCalledWith('cond');
  });

  it('routes dropped Mimir mount payloads to onAddMimirResource', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    fireEvent.drop(screen.getByTestId('graph-canvas'), {
      clientX: 140,
      clientY: 160,
      dataTransfer: {
        getData: (key: string) => {
          if (key === MIMIR_MOUNT_MIME) {
            return serializeWorkflowRegistryMount({
              id: 'shared-mimir',
              name: 'Shared Mimir',
              kind: 'remote',
              lifecycle: 'registered',
              role: 'shared',
              url: 'https://mimir.example',
              path: '/shared',
              categories: ['decision'],
              authRef: 'mimir-secret',
              defaultReadPriority: 5,
              enabled: true,
              healthStatus: 'healthy',
              healthMessage: 'ok',
              desc: 'Shared team mount',
            });
          }
          return '';
        },
      },
    });
    expect(props.onAddMimirResource).toHaveBeenCalledTimes(1);
  });

  it('shows delete-selected button when a node is selected', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getByTestId('delete-selected')).toBeInTheDocument();
  });

  it('does not show delete-selected button when no node selected', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.queryByTestId('delete-selected')).toBeNull();
  });

  it('calls onDeleteNode when delete-selected is clicked', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('delete-selected'));
    expect(props.onDeleteNode).toHaveBeenCalledWith('stage-1');
  });

  it('shows delete button on selected node', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getByTestId('delete-btn-stage-1')).toBeInTheDocument();
  });

  it('does not show delete buttons on unselected nodes', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.queryByTestId('delete-btn-gate-1')).toBeNull();
  });

  it('shows "Click target input…" hint when in connecting mode', () => {
    const props = { ...defaultProps(), connectingFromId: 'stage-1', selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getByText(/click target input/i)).toBeInTheDocument();
  });

  it('renders with no nodes', () => {
    const props = { ...defaultProps(), nodes: [], edges: [] };
    render(<GraphView {...props} />);
    expect(screen.getByTestId('graph-canvas')).toBeInTheDocument();
  });

  it('calls onDeleteNode via delete button on node', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('delete-btn-stage-1'));
    expect(props.onDeleteNode).toHaveBeenCalledWith('stage-1');
  });

  it('calls onStartConnect from a trigger output port', () => {
    const props = {
      ...defaultProps(),
      nodes: [triggerNode, stageNode],
      edges: [],
    };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('trigger-output-trigger-1'));
    expect(props.onStartConnect).toHaveBeenCalledWith('trigger-1', 'code.requested');
  });

  it('calls onCompleteConnect from an end input port in connecting mode', () => {
    const props = {
      ...defaultProps(),
      nodes: [stageNode, endNode],
      edges: [],
      connectingFromId: 'stage-1',
      selectedNodeId: 'stage-1',
    };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('end-input-end-1'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('end-1', 'complete');
  });
});

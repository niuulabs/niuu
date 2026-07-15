import { describe, it, expect, vi } from 'vitest';
import { act, render, screen, fireEvent, createEvent } from '@testing-library/react';
import {
  GraphView,
  buildIssueLevelMap,
  edgeAnchor,
  isGraphNodeKind,
  renderedStageHeight,
  splitEdgePorts,
  stagePortLists,
} from './GraphView';
import type { WorkflowNode, WorkflowEdge } from '../../domain/workflow';
import { MIMIR_MOUNT_MIME, serializeWorkflowRegistryMount } from './mimirRegistry';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const stageNode: WorkflowNode = {
  id: 'stage-1',
  kind: 'stage',
  label: 'Stage 1',
  runId: null,
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

const resourceNode: WorkflowNode = {
  id: 'resource-1',
  kind: 'resource',
  label: 'Research Mimir',
  resourceType: 'mimir',
  bindingMode: 'registry',
  registryEntryId: 'shared-mimir',
  seedFromRegistryId: null,
  categories: ['decision'],
  path: '/shared',
  url: 'https://mimir.example',
  role: 'shared',
  authRef: 'mimir-secret',
  defaultReadPriority: 5,
  position: { x: 220, y: 220 },
};

const edge: WorkflowEdge = {
  id: 'e1',
  source: 'stage-1',
  target: 'gate-1',
  cp1: { x: 80, y: 0 },
  cp2: { x: -80, y: 0 },
};

const personaFixtures = [
  {
    id: 'ravn-alpha',
    name: 'Ravn Alpha',
    consumes: ['brief'],
    produces: ['notes'],
  },
  {
    id: 'ravn-beta',
    name: 'Ravn Beta',
    consumes: ['notes'],
    produces: ['summary'],
  },
] as never[];

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

  it('renders add-trigger and add-end toolbar buttons', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('add-trigger')).toBeInTheDocument();
    expect(screen.getByTestId('add-end')).toBeInTheDocument();
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

  it('calls onAddNode("resource") when add-resource clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('add-resource'));
    expect(props.onAddNode).toHaveBeenCalledWith('resource');
  });

  it('calls onAddNode for trigger and end toolbar actions', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('add-trigger'));
    fireEvent.click(screen.getByTestId('add-end'));
    expect(props.onAddNode).toHaveBeenCalledWith('trigger');
    expect(props.onAddNode).toHaveBeenCalledWith('end');
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

  it('renders resource nodes, surfaces issues, and inspects on context menu', () => {
    const warningStage: WorkflowNode = {
      ...stageNode,
      id: 'stage-warning',
      label: 'Warning stage',
      position: { x: 80, y: 260 },
    };
    const errorStage: WorkflowNode = {
      ...stageNode,
      id: 'stage-error',
      label: 'Error stage',
      position: { x: 320, y: 260 },
    };
    const props = {
      ...defaultProps(),
      nodes: [resourceNode, warningStage, errorStage],
      selectedNodeId: 'resource-1',
      issues: [
        {
          kind: 'resource_warn',
          nodeId: 'resource-1',
          message: 'Resource warning',
          severity: 'warning',
        },
        {
          kind: 'stage_warn',
          nodeId: 'stage-warning',
          message: 'Stage warning',
          severity: 'warning',
        },
        { kind: 'stage_error', nodeId: 'stage-error', message: 'Stage error', severity: 'error' },
        { kind: 'ignored', nodeId: null, message: 'Ignored', severity: 'warning' },
      ],
      onDeleteEdge: vi.fn(),
    };

    render(<GraphView {...props} />);

    expect(screen.getByTestId('workflow-node-resource-1')).toHaveAttribute('data-kind', 'resource');
    expect(screen.getByTestId('delete-btn-resource-1')).toBeInTheDocument();
    expect(screen.getAllByText('WARN')).toHaveLength(1);
    expect(screen.getAllByText('ERR')).toHaveLength(1);

    fireEvent.contextMenu(screen.getByTestId('workflow-node-resource-1'));
    expect(props.onInspectNode).toHaveBeenCalledWith('resource-1');
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

  it('inspects trigger and end nodes from their context menus', () => {
    const props = {
      ...defaultProps(),
      nodes: [triggerNode, endNode],
      edges: [],
      onDeleteEdge: vi.fn(),
    };

    render(<GraphView {...props} />);
    fireEvent.contextMenu(screen.getByTestId('workflow-node-trigger-1'));
    fireEvent.contextMenu(screen.getByTestId('workflow-node-end-1'));

    expect(props.onInspectNode).toHaveBeenCalledWith('trigger-1');
    expect(props.onInspectNode).toHaveBeenCalledWith('end-1');
  });

  it('zooms on wheel events and pans the canvas while dragging empty space', () => {
    const props = { ...defaultProps(), onDeleteEdge: vi.fn() };
    render(<GraphView {...props} />);
    const svg = screen.getByTestId('graph-canvas');
    Object.defineProperty(svg, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    });

    fireEvent.wheel(svg, { deltaY: -100 });
    const graphLayer = svg.querySelector('g');
    expect(graphLayer).toHaveAttribute('transform', 'translate(0,0) scale(1.1)');

    fireEvent.mouseDown(svg, { clientX: 10, clientY: 20 });
    expect(svg).toHaveStyle({ cursor: 'grabbing' });
    expect(props.onSelectNode).toHaveBeenCalledWith(null);

    fireEvent.mouseMove(svg, { clientX: 30, clientY: 55 });
    expect(graphLayer).toHaveAttribute('transform', 'translate(20,35) scale(1.1)');

    fireEvent.mouseUp(svg);
    expect(svg).toHaveStyle({ cursor: 'default' });
  });

  it('finishes a batched canvas pan without reading cleared drag state', () => {
    render(<GraphView {...defaultProps()} />);
    const svg = screen.getByTestId('graph-canvas');
    const graphLayer = svg.querySelector('g');

    expect(() => {
      act(() => {
        svg.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 10, clientY: 20 }));
        svg.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 30, clientY: 55 }));
        svg.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      });
    }).not.toThrow();
    expect(graphLayer).toHaveAttribute('transform', 'translate(20,35) scale(1)');
  });

  it('cancels connect mode when the canvas is clicked', () => {
    const props = {
      ...defaultProps(),
      connectingFromId: 'stage-1',
      connectingFromLabel: 'code.changed',
      onDeleteEdge: vi.fn(),
    };
    render(<GraphView {...props} />);
    fireEvent.mouseDown(screen.getByTestId('graph-canvas'), { clientX: 5, clientY: 5 });
    expect(props.onCancelConnect).toHaveBeenCalledTimes(1);
  });

  it('selects an edge and deletes it from the toolbar and keyboard', () => {
    const props = { ...defaultProps(), onDeleteEdge: vi.fn() };
    render(<GraphView {...props} />);

    const edgeTarget = screen.getByTestId('workflow-edge-e1').querySelector('path')!;
    fireEvent.click(edgeTarget);
    fireEvent.click(screen.getByTestId('delete-selected-edge'));
    expect(props.onDeleteEdge).toHaveBeenCalledWith('e1');

    fireEvent.click(edgeTarget);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }));
    expect(props.onDeleteEdge).toHaveBeenCalledWith('e1');
  });

  it('deletes the selected node from the keyboard', () => {
    const props = {
      ...defaultProps(),
      selectedNodeId: 'stage-1',
      onDeleteEdge: vi.fn(),
    };
    render(<GraphView {...props} />);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace' }));
    expect(props.onDeleteNode).toHaveBeenCalledWith('stage-1');
  });

  it('drops a persona onto a stage and selects that stage', () => {
    const props = {
      ...defaultProps(),
      nodes: [stageNode],
      edges: [],
      onDeleteEdge: vi.fn(),
      onAddPersonaToStage: vi.fn(),
    };
    render(<GraphView {...props} />);
    const svg = screen.getByTestId('graph-canvas');
    Object.defineProperty(svg, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    });

    const drop = createEvent.drop(svg);
    Object.defineProperty(drop, 'clientX', { value: 140 });
    Object.defineProperty(drop, 'clientY', { value: 140 });
    Object.defineProperty(drop, 'dataTransfer', {
      value: {
        getData: (key: string) => (key === 'application/niuu-persona-id' ? 'coder' : ''),
      },
    });
    fireEvent(svg, drop);

    expect(props.onAddPersonaToStage).toHaveBeenCalledWith('stage-1', 'coder');
    expect(props.onSelectNode).toHaveBeenCalledWith('stage-1');
  });

  it('routes stage port clicks and connect-mode node clicks to connect handlers', () => {
    const stageWithPorts: WorkflowNode = {
      ...stageNode,
      personaIds: ['ravn-alpha', 'ravn-beta'],
    };

    const connectProps = {
      ...defaultProps(),
      nodes: [stageWithPorts, gateNode],
      edges: [],
      personas: personaFixtures,
      selectedNodeId: 'stage-1',
      connectingFromId: 'gate-1',
      connectingFromLabel: 'summary',
      onDeleteEdge: vi.fn(),
    };

    const { rerender } = render(<GraphView {...connectProps} />);
    const stageGroup = screen.getByTestId('workflow-node-stage-1');
    const connectCircles = stageGroup.querySelectorAll('circle');
    expect(connectCircles).toHaveLength(4);

    fireEvent.click(connectCircles[0]!);
    fireEvent.click(connectCircles[3]!);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-gate-1'), { clientX: 300, clientY: 100 });

    expect(connectProps.onCompleteConnect).toHaveBeenCalledWith('stage-1', 'brief');
    expect(connectProps.onStartConnect).toHaveBeenCalledWith('stage-1', 'summary');
    expect(connectProps.onCompleteConnect).toHaveBeenCalledWith('gate-1');

    const idleProps = {
      ...defaultProps(),
      nodes: [stageWithPorts],
      edges: [],
      personas: personaFixtures,
      selectedNodeId: 'stage-1',
      onDeleteEdge: vi.fn(),
    };

    rerender(<GraphView {...idleProps} />);
    const idleStageGroup = screen.getByTestId('workflow-node-stage-1');
    const outputPorts = idleStageGroup.querySelectorAll('circle.niuu\\:cursor-pointer');
    expect(outputPorts).toHaveLength(2);

    fireEvent.click(outputPorts[1]!);
    expect(idleProps.onStartConnect).toHaveBeenCalledWith('stage-1', 'summary');
  });

  it('moves nodes only after the drag threshold and clears drag state on mouse up', () => {
    const props = {
      ...defaultProps(),
      nodes: [stageNode],
      edges: [],
      onDeleteEdge: vi.fn(),
    };

    render(<GraphView {...props} />);
    const stage = screen.getByTestId('workflow-node-stage-1');

    fireEvent.mouseDown(stage, { clientX: 100, clientY: 100 });
    fireEvent.mouseMove(stage, { clientX: 103, clientY: 103 });
    expect(props.onMoveNode).not.toHaveBeenCalled();

    fireEvent.mouseMove(stage, { clientX: 112, clientY: 118 });
    expect(props.onMoveNode).toHaveBeenCalledWith('stage-1', { x: 112, y: 118 });

    fireEvent.mouseUp(stage);
    fireEvent.mouseMove(stage, { clientX: 130, clientY: 140 });
    expect(props.onMoveNode).toHaveBeenCalledTimes(1);
  });

  it('creates a new stage when a persona is dropped on empty canvas', () => {
    const props = {
      ...defaultProps(),
      nodes: [gateNode],
      edges: [],
      onDeleteEdge: vi.fn(),
      onAddStageWithPersona: vi.fn(),
    };
    render(<GraphView {...props} />);
    const svg = screen.getByTestId('graph-canvas');
    Object.defineProperty(svg, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    });

    const drop = createEvent.drop(svg);
    Object.defineProperty(drop, 'clientX', { value: 50 });
    Object.defineProperty(drop, 'clientY', { value: 60 });
    Object.defineProperty(drop, 'dataTransfer', {
      value: {
        getData: (key: string) => (key === 'application/niuu-persona-id' ? 'reviewer' : ''),
      },
    });
    fireEvent(svg, drop);

    expect(props.onAddStageWithPersona).toHaveBeenCalledWith(
      'reviewer',
      undefined,
      expect.objectContaining({ x: 50, y: 60 }),
    );
  });

  it('creates a node when a node-kind payload is dropped', () => {
    const props = { ...defaultProps(), onDeleteEdge: vi.fn() };
    render(<GraphView {...props} />);
    const svg = screen.getByTestId('graph-canvas');
    Object.defineProperty(svg, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    });

    const drop = createEvent.drop(svg);
    Object.defineProperty(drop, 'clientX', { value: 75 });
    Object.defineProperty(drop, 'clientY', { value: 90 });
    Object.defineProperty(drop, 'dataTransfer', {
      value: {
        getData: (key: string) => (key === 'application/niuu-node-kind' ? 'gate' : ''),
      },
    });
    fireEvent(svg, drop);

    expect(props.onAddNode).toHaveBeenCalledWith('gate', expect.objectContaining({ x: 75, y: 90 }));
  });

  it('ignores unsupported drops, supports drag-over, and cancels with Escape', () => {
    const props = {
      ...defaultProps(),
      onDeleteEdge: vi.fn(),
      connectingFromId: 'stage-1',
    };
    render(<GraphView {...props} />);
    const svg = screen.getByTestId('graph-canvas');

    const dragOver = createEvent.dragOver(svg);
    fireEvent(svg, dragOver);
    expect(dragOver.defaultPrevented).toBe(true);

    const drop = createEvent.drop(svg);
    Object.defineProperty(drop, 'clientX', { value: 25 });
    Object.defineProperty(drop, 'clientY', { value: 30 });
    Object.defineProperty(drop, 'dataTransfer', {
      value: {
        getData: () => '',
      },
    });
    fireEvent(svg, drop);
    expect(props.onAddNode).not.toHaveBeenCalled();

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(props.onCancelConnect).toHaveBeenCalledTimes(1);
  });

  it('skips rendering edges when a source or target node is missing', () => {
    const props = {
      ...defaultProps(),
      nodes: [stageNode],
      edges: [{ ...edge, id: 'missing-edge', target: 'unknown-node' }],
      onDeleteEdge: vi.fn(),
    };

    render(<GraphView {...props} />);
    expect(screen.queryByTestId('workflow-edge-missing-edge')).toBeNull();
  });

  it('derives stage port lists and rendered heights from persona IO', () => {
    const stageWithMembers: WorkflowNode = {
      ...stageNode,
      personaIds: ['ravn-alpha', 'ravn-beta'],
    };

    expect(stagePortLists(stageWithMembers as never, personaFixtures as never)).toEqual({
      knownInputs: ['brief', 'notes'],
      knownOutputs: ['notes', 'summary'],
    });
    expect(
      renderedStageHeight(stageWithMembers as never, personaFixtures as never),
    ).toBeGreaterThan(renderedStageHeight(stageNode as never, [] as never));
  });

  it('splits edge labels into source and target ports', () => {
    expect(splitEdgePorts()).toEqual({ sourcePort: null, targetPort: null });
    expect(splitEdgePorts('notes -> brief')).toEqual({ sourcePort: 'notes', targetPort: 'brief' });
    expect(splitEdgePorts('summary')).toEqual({ sourcePort: 'summary', targetPort: 'summary' });
    expect(splitEdgePorts('notes -> ')).toEqual({ sourcePort: 'notes', targetPort: '' });
  });

  it('builds issue levels with error precedence and guards supported node kinds', () => {
    expect(buildIssueLevelMap(null as never).size).toBe(0);

    const issueMap = buildIssueLevelMap([
      { kind: 'warn', nodeId: 'stage-1', message: 'warning first', severity: 'warning' },
      { kind: 'ignore', nodeId: null, message: 'ignore me', severity: 'error' },
      { kind: 'error', nodeId: 'stage-1', message: 'error wins', severity: 'error' },
      { kind: 'late-warning', nodeId: 'stage-1', message: 'error still wins', severity: 'warning' },
    ]);

    expect(issueMap.get('stage-1')).toBe('error');
    expect(isGraphNodeKind('trigger')).toBe(true);
    expect(isGraphNodeKind('stage')).toBe(true);
    expect(isGraphNodeKind('resource')).toBe(true);
    expect(isGraphNodeKind('gate')).toBe(true);
    expect(isGraphNodeKind('cond')).toBe(true);
    expect(isGraphNodeKind('end')).toBe(true);
    expect(isGraphNodeKind('unknown')).toBe(false);
  });

  it('anchors stage edges to matching ports and falls back to node centres otherwise', () => {
    const stageWithMembers = {
      ...stageNode,
      personaIds: ['ravn-alpha', 'ravn-beta'],
    };

    expect(
      edgeAnchor(stageWithMembers as never, 'source', 'summary', personaFixtures as never),
    ).toMatchObject({
      x: stageWithMembers.position.x + 172 - 10,
    });
    expect(
      edgeAnchor(stageWithMembers as never, 'target', 'brief', personaFixtures as never),
    ).toMatchObject({
      x: stageWithMembers.position.x + 10,
    });
    expect(
      edgeAnchor(stageWithMembers as never, 'source', 'missing', personaFixtures as never),
    ).toEqual({
      x: 186,
      y: 157,
    });
    expect(edgeAnchor(gateNode as never, 'target', 'brief', personaFixtures as never)).toEqual({
      x: 338,
      y: 138,
    });
  });

  it('renders labeled edges and supports selection from either edge path', () => {
    const props = {
      ...defaultProps(),
      onDeleteEdge: vi.fn(),
      edges: [{ ...edge, id: 'labeled-edge', label: 'notes -> brief' }],
    };

    render(<GraphView {...props} />);

    const paths = screen.getByTestId('workflow-edge-labeled-edge').querySelectorAll('path');
    expect(screen.getByText('notes -> brief')).toBeInTheDocument();

    fireEvent.click(paths[1]!);
    expect(screen.getByTestId('delete-selected-edge')).toBeInTheDocument();
  });

  it('inspects stage, gate, and cond nodes and completes connects from connect-mode mouse down', () => {
    const longStage: WorkflowNode = {
      ...stageNode,
      id: 'stage-long',
      label: 'Stage label that is definitely longer than eighteen',
      personaIds: ['ravn-alpha', 'ravn-beta'],
      stageMembers: [
        { personaId: 'ravn-alpha', model: 'gpt-4.1', budget: 40 },
        { personaId: 'ravn-beta', model: '', budget: 12 },
      ],
      position: { x: 100, y: 120 },
    };
    const longGate: WorkflowNode = {
      ...gateNode,
      id: 'gate-long',
      label: 'Gate label',
      position: { x: 360, y: 120 },
    };
    const longCond: WorkflowNode = {
      ...condNode,
      id: 'cond-long',
      label: 'Condition label',
      position: { x: 520, y: 120 },
    };
    const props = {
      ...defaultProps(),
      nodes: [longStage, longGate, longCond],
      edges: [],
      personas: personaFixtures,
      selectedNodeId: 'stage-long',
      connectingFromId: 'trigger-1',
      connectingFromLabel: 'summary',
      onDeleteEdge: vi.fn(),
    };

    render(<GraphView {...props} />);

    expect(screen.getByTestId('workflow-node-stage-long')).toHaveStyle({ cursor: 'crosshair' });
    expect(screen.getByTestId('workflow-node-gate-long')).toHaveStyle({ cursor: 'crosshair' });
    expect(screen.getByTestId('workflow-node-cond-long')).toHaveStyle({ cursor: 'crosshair' });
    expect(screen.queryByTestId('delete-btn-stage-long')).toBeNull();
    expect(screen.getByText('gpt-4.1')).toBeInTheDocument();
    expect(screen.getByText('budget 12')).toBeInTheDocument();
    expect(screen.getByText('Stage label that…')).toBeInTheDocument();
    expect(screen.getByText('Gate la…')).toBeInTheDocument();
    expect(screen.getByText('Condi…')).toBeInTheDocument();

    fireEvent.contextMenu(screen.getByTestId('workflow-node-stage-long'));
    fireEvent.contextMenu(screen.getByTestId('workflow-node-gate-long'));
    fireEvent.contextMenu(screen.getByTestId('workflow-node-cond-long'));

    expect(props.onInspectNode).toHaveBeenCalledWith('stage-long');
    expect(props.onInspectNode).toHaveBeenCalledWith('gate-long');
    expect(props.onInspectNode).toHaveBeenCalledWith('cond-long');

    fireEvent.mouseDown(screen.getByTestId('workflow-node-gate-long'), {
      clientX: 360,
      clientY: 120,
    });
    fireEvent.mouseDown(screen.getByTestId('workflow-node-cond-long'), {
      clientX: 520,
      clientY: 120,
    });

    expect(props.onCompleteConnect).toHaveBeenCalledWith('gate-long');
    expect(props.onCompleteConnect).toHaveBeenCalledWith('cond-long');
  });

  it('renders resource, trigger, and end fallback branches', () => {
    const ephemeralResource: WorkflowNode = {
      ...resourceNode,
      id: 'resource-ephemeral',
      label: 'Resource node label that is definitely longer than twenty',
      bindingMode: 'ephemeral_local',
      position: { x: 220, y: 220 },
    };
    const fallbackTrigger: WorkflowNode = {
      ...triggerNode,
      id: 'trigger-fallback',
      label: 'Trigger label definitely longer than twenty characters',
      dispatchEvent: undefined,
      position: { x: 40, y: 260 },
    };
    const shortEnd: WorkflowNode = {
      ...endNode,
      id: 'end-short',
      label: 'Finish flow now',
      position: { x: 460, y: 250 },
    };
    const props = {
      ...defaultProps(),
      nodes: [ephemeralResource, fallbackTrigger, shortEnd],
      edges: [],
      selectedNodeId: 'resource-ephemeral',
      onDeleteEdge: vi.fn(),
    };

    render(<GraphView {...props} />);

    expect(screen.getByText('EPHEMERAL')).toBeInTheDocument();
    expect(screen.getByText('Resource node labe…')).toBeInTheDocument();
    expect(screen.getByText('Trigger label defi…')).toBeInTheDocument();
    expect(screen.getByText('code.requested')).toBeInTheDocument();
    expect(screen.getByText('Finish f…')).toBeInTheDocument();
    expect(screen.getByTestId('delete-btn-resource-ephemeral')).toBeInTheDocument();
  });
});

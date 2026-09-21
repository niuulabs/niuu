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
  waitPortLists,
} from './GraphView';
import type { WorkflowNode, WorkflowEdge } from '../../domain/workflow';
import { MIMIR_MOUNT_MIME, serializeWorkflowRegistryMount } from './mimirRegistry';
import { STAGE_WIDTH, WAIT_HEIGHT, stageNodeHeight } from './graphUtils';

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

const waitNode: WorkflowNode = {
  id: 'wait-1',
  kind: 'wait',
  label: 'Wait for CI',
  position: { x: 600, y: 220 },
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

function openAddMenu() {
  fireEvent.click(screen.getByTestId('open-add-step'));
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GraphView', () => {
  function pickerBounds() {
    const bounds = {
      left: 50,
      top: 60,
      width: 800,
      height: 600,
      right: 850,
      bottom: 660,
      x: 50,
      y: 60,
      toJSON: () => ({}),
    };
    vi.spyOn(screen.getByTestId('graph-view'), 'getBoundingClientRect').mockReturnValue(bounds);
    vi.spyOn(screen.getByTestId('graph-canvas'), 'getBoundingClientRect').mockReturnValue(bounds);
  }

  it('opens all eight node kinds on background right-click and inserts at the transformed point', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    pickerBounds();
    const canvas = screen.getByTestId('graph-canvas');
    fireEvent.mouseDown(canvas, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.mouseMove(canvas, { clientX: 150, clientY: 120 });
    fireEvent.mouseUp(canvas);
    fireEvent.click(screen.getByTestId('zoom-in'));
    const scale = Number(canvas.getAttribute('data-scale'));
    fireEvent.mouseDown(canvas, { button: 2, clientX: 350, clientY: 260 });
    fireEvent.contextMenu(canvas, { clientX: 350, clientY: 260 });
    expect(screen.getByTestId('library-search')).toHaveFocus();
    for (const kind of [
      'trigger',
      'stage',
      'gate',
      'cond',
      'wait',
      'subworkflow',
      'resource',
      'end',
    ]) {
      expect(screen.getByTestId(`library-add-${kind}`)).toBeInTheDocument();
    }
    fireEvent.change(screen.getByTestId('library-search'), { target: { value: 'child' } });
    expect(screen.queryByTestId('library-add-stage')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('library-add-subworkflow'));
    expect(props.onAddNode).toHaveBeenCalledWith('subworkflow', { x: 250 / scale, y: 180 / scale });
    expect(screen.queryByTestId('canvas-node-picker')).not.toBeInTheDocument();
    expect(screen.getByTestId('graph-view')).toHaveFocus();
  });

  it('clamps the picker at the canvas edge and dismisses with Escape or outside pointer', () => {
    render(<GraphView {...defaultProps()} />);
    pickerBounds();
    const canvas = screen.getByTestId('graph-canvas');
    fireEvent.contextMenu(canvas, { clientX: 840, clientY: 650 });
    expect(screen.getByTestId('canvas-node-picker')).toHaveStyle({ left: '468px', top: '108px' });
    fireEvent.keyDown(screen.getByTestId('library-search'), { key: 'Escape' });
    expect(screen.queryByTestId('canvas-node-picker')).not.toBeInTheDocument();
    fireEvent.contextMenu(canvas, { clientX: 200, clientY: 200 });
    fireEvent.pointerDown(screen.getByTestId('library-search'));
    expect(screen.getByTestId('canvas-node-picker')).toBeInTheDocument();
    fireEvent.pointerDown(canvas);
    expect(screen.queryByTestId('canvas-node-picker')).not.toBeInTheDocument();
  });

  it('uses the injected persona and resource actions at the chosen point', () => {
    const props = defaultProps();
    const onAddStageWithPersona = vi.fn();
    render(
      <GraphView
        {...props}
        onAddStageWithPersona={onAddStageWithPersona}
        personas={[{ id: 'analyst', label: 'Analyst', role: 'plan' }]}
      />,
    );
    pickerBounds();
    const canvas = screen.getByTestId('graph-canvas');
    fireEvent.contextMenu(canvas, { clientX: 200, clientY: 200 });
    fireEvent.click(screen.getByText('Analyst'));
    expect(onAddStageWithPersona).toHaveBeenCalledWith('analyst', undefined, { x: 150, y: 140 });
    fireEvent.contextMenu(canvas, { clientX: 200, clientY: 200 });
    fireEvent.click(document.querySelector('[data-testid^="mimir-mount-"]')!);
    expect(props.onAddMimirResource).toHaveBeenCalledWith(
      expect.objectContaining({ lifecycle: 'ephemeral' }),
      { x: 150, y: 140 },
    );
  });

  it('keeps node right-click inspection and immutable workflows free of creation menus', () => {
    const props = defaultProps();
    const view = render(<GraphView {...props} />);
    fireEvent.contextMenu(screen.getByTestId('workflow-node-stage-1'));
    expect(props.onInspectNode).toHaveBeenCalledWith('stage-1');
    expect(screen.queryByTestId('canvas-node-picker')).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'), { button: 2 });
    fireEvent.mouseMove(screen.getByTestId('workflow-node-stage-1'), {
      clientX: 200,
      clientY: 200,
    });
    fireEvent.mouseUp(screen.getByTestId('workflow-node-stage-1'));
    expect(props.onMoveNode).not.toHaveBeenCalled();
    const onEdit = vi.fn();
    view.rerender(<GraphView {...props} readOnly onEdit={onEdit} />);
    pickerBounds();
    fireEvent.contextMenu(screen.getByTestId('graph-canvas'));
    expect(screen.getByText('Viewing saved workflow')).toBeInTheDocument();
    expect(screen.queryByTestId('library-add-stage')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit workflow' }));
    expect(onEdit).toHaveBeenCalledOnce();
    expect(props.onAddNode).not.toHaveBeenCalled();
    view.rerender(
      <GraphView {...props} readOnly readOnlyMessage="Loading the selected version…" />,
    );
    fireEvent.contextMenu(screen.getByTestId('graph-canvas'));
    expect(screen.getByText('Loading the selected version…')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit workflow' })).not.toBeInTheDocument();
  });

  it('opens on Control-click without starting a pan or moving the insertion position', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    pickerBounds();
    const canvas = screen.getByTestId('graph-canvas');
    fireEvent.mouseDown(canvas, { button: 0, ctrlKey: true, clientX: 200, clientY: 200 });
    fireEvent.mouseMove(canvas, { clientX: 260, clientY: 240 });
    fireEvent.mouseUp(canvas);
    fireEvent.click(screen.getByTestId('library-add-end'));
    expect(props.onAddNode).toHaveBeenCalledWith('end', { x: 150, y: 140 });
    expect(props.onSelectNode).not.toHaveBeenCalled();
  });

  it('renders the graph-view container', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  });

  it('hides the auto-layout button when onAutoLayout is not supplied', () => {
    render(<GraphView {...defaultProps()} />);
    expect(screen.queryByTestId('auto-layout')).not.toBeInTheDocument();
  });

  it('shows the auto-layout button and calls the handler on click', () => {
    const onAutoLayout = vi.fn();
    render(<GraphView {...defaultProps()} onAutoLayout={onAutoLayout} />);
    openAddMenu();
    fireEvent.click(screen.getByTestId('auto-layout'));
    expect(onAutoLayout).toHaveBeenCalledTimes(1);
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

  it('derives passive wait ports from custom connected edge events', () => {
    const customEdges: WorkflowEdge[] = [
      {
        id: 'wait-in',
        source: 'stage-1',
        target: 'wait-1',
        label: 'custom.build.queued->custom.build.waiting',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
      {
        id: 'wait-in-review',
        source: 'stage-1',
        target: 'wait-1',
        label: 'custom.review.queued  ->  custom.review.waiting',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
      {
        id: 'wait-out',
        source: 'wait-1',
        target: 'end-1',
        label: 'custom.build.finished->custom.build.observed',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
      {
        id: 'wait-out-merge',
        source: 'wait-1',
        target: 'end-1',
        label: 'custom.merge.finished -> custom.merge.observed',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ];
    const props = {
      ...defaultProps(),
      nodes: [stageNode, waitNode, endNode],
      edges: customEdges,
    };
    const { rerender } = render(<GraphView {...props} />);

    expect(screen.getByTestId('workflow-node-wait-1')).toHaveAttribute('data-kind', 'wait');
    expect(screen.getByText('PASSIVE · 2 IN · 2 OUT')).toBeInTheDocument();
    expect(screen.getByTestId('wait-output-wait-1')).toHaveAttribute(
      'data-event-type',
      'custom.build.finished',
    );
    expect(screen.getByTestId('wait-output-wait-1-1')).toHaveAttribute(
      'data-event-type',
      'custom.merge.finished',
    );
    fireEvent.click(screen.getByTestId('wait-output-wait-1'));
    expect(props.onStartConnect).toHaveBeenCalledWith('wait-1', 'custom.build.finished');
    fireEvent.click(screen.getByTestId('wait-output-wait-1-1'));
    expect(props.onStartConnect).toHaveBeenCalledWith('wait-1', 'custom.merge.finished');

    rerender(
      <GraphView {...props} connectingFromId="stage-1" connectingFromLabel="custom.build.queued" />,
    );
    fireEvent.click(screen.getByTestId('wait-input-wait-1'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('wait-1', 'custom.build.waiting');
    fireEvent.click(screen.getByTestId('wait-input-wait-1-1'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('wait-1', 'custom.review.waiting');
  });

  it('prompts for event types when connecting an unconfigured wait', () => {
    const prompt = vi
      .spyOn(window, 'prompt')
      .mockReturnValueOnce('custom.build.finished')
      .mockReturnValueOnce('custom.build.waiting');
    const props = { ...defaultProps(), nodes: [waitNode], edges: [] };
    const { rerender } = render(<GraphView {...props} />);

    expect(screen.getByText('PASSIVE · CONFIGURE EVENTS')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('wait-output-wait-1'));
    expect(props.onStartConnect).toHaveBeenCalledWith('wait-1', 'custom.build.finished');

    rerender(
      <GraphView {...props} connectingFromId="stage-1" connectingFromLabel="custom.build.queued" />,
    );
    fireEvent.click(screen.getByTestId('wait-input-wait-1'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('wait-1', 'custom.build.waiting');
    expect(prompt).toHaveBeenNthCalledWith(
      2,
      'Incoming event type for this wait',
      'custom.build.queued',
    );
    prompt.mockRestore();
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
    openAddMenu();
    expect(screen.getByTestId('library-add-stage')).toBeInTheDocument();
  });

  it('renders add-gate toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    openAddMenu();
    expect(screen.getByTestId('library-add-gate')).toBeInTheDocument();
  });

  it('renders add-resource toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    openAddMenu();
    expect(screen.getByTestId('library-add-resource')).toBeInTheDocument();
  });

  it('renders add-cond toolbar button', () => {
    render(<GraphView {...defaultProps()} />);
    openAddMenu();
    expect(screen.getByTestId('library-add-cond')).toBeInTheDocument();
  });

  it('renders add-trigger and add-end toolbar buttons', () => {
    render(<GraphView {...defaultProps()} />);
    openAddMenu();
    expect(screen.getByTestId('library-add-trigger')).toBeInTheDocument();
    expect(screen.getByTestId('library-add-end')).toBeInTheDocument();
    expect(screen.getByTestId('library-add-wait')).toBeInTheDocument();
  });

  it('calls onAddNode("stage") when add-stage clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-stage'));
    expect(props.onAddNode).toHaveBeenCalledWith('stage', expect.any(Object));
  });

  it('calls onAddNode("gate") when add-gate clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-gate'));
    expect(props.onAddNode).toHaveBeenCalledWith('gate', expect.any(Object));
  });

  it('calls onAddNode("cond") when add-cond clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-cond'));
    expect(props.onAddNode).toHaveBeenCalledWith('cond', expect.any(Object));
  });

  it('calls onAddNode("resource") when add-resource clicked', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-resource'));
    expect(props.onAddNode).toHaveBeenCalledWith('resource', expect.any(Object));
  });

  it('calls onAddNode for trigger and end toolbar actions', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-trigger'));
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-end'));
    openAddMenu();
    fireEvent.click(screen.getByTestId('library-add-wait'));
    expect(props.onAddNode).toHaveBeenCalledWith('trigger', expect.any(Object));
    expect(props.onAddNode).toHaveBeenCalledWith('end', expect.any(Object));
    expect(props.onAddNode).toHaveBeenCalledWith('wait', expect.any(Object));
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
    expect(screen.getByText(/choose a compatible input/i)).toBeInTheDocument();
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
      selectedNodeId: 'trigger-1',
    };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('workflow-socket-trigger-1-output-0'));
    expect(props.onStartConnect).toHaveBeenCalledWith('trigger-1', 'code.requested');
  });

  it('calls onCompleteConnect from an end input port in connecting mode', () => {
    const props = {
      ...defaultProps(),
      nodes: [stageNode, endNode],
      edges: [
        {
          id: 'existing-end-input',
          source: 'stage-1',
          target: 'end-1',
          label: 'stage.completed -> complete',
          cp1: { x: 80, y: 0 },
          cp2: { x: -80, y: 0 },
        },
      ],
      connectingFromId: 'stage-1',
      selectedNodeId: 'stage-1',
    };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('workflow-socket-end-1-input-0'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('end-1', 'complete');
  });

  it('keeps foreignObject card roots non-positioned for WebKit SVG transforms', () => {
    const { container } = render(
      <GraphView
        {...defaultProps()}
        nodes={[
          triggerNode,
          stageNode,
          gateNode,
          condNode,
          waitNode,
          endNode,
          resourceNode,
        ]}
        edges={[]}
      />,
    );

    const leftAccentCards = ['stage', 'trigger', 'wait', 'resource'];
    const topAccentCards = ['gate', 'cond'];
    for (const kind of [...leftAccentCards, ...topAccentCards, 'end']) {
      const card = container.querySelector(`.workflow-${kind}-card`);
      expect(card).not.toBeNull();
      expect(card).not.toHaveClass('niuu:relative');
    }
    for (const kind of leftAccentCards) {
      expect(container.querySelector(`.workflow-${kind}-card`)).toHaveStyle({
        borderLeftWidth: '3px',
      });
    }
    for (const kind of topAccentCards) {
      expect(container.querySelector(`.workflow-${kind}-card`)).toHaveStyle({
        borderTopWidth: '3px',
      });
    }
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
    screen.getByTestId('graph-view').focus();
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
    screen.getByTestId('graph-view').focus();
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
    // Stage ports are HTML buttons inside a foreignObject (see StageNode),
    // not SVG <circle>s — connecting mode shows both input ports (only
    // rendered while connecting) and output ports (always rendered).
    const connectPorts = stageGroup.querySelectorAll('[data-testid^="stage-port-"]');
    expect(connectPorts).toHaveLength(4);

    fireEvent.click(connectPorts[0]!);
    fireEvent.click(connectPorts[3]!);
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
    // Idle (not connecting): only output ports render — inputs are gated on
    // isConnectingMode, same as before.
    const outputPorts = idleStageGroup.querySelectorAll('[data-testid^="stage-port-out-"]');
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
    expect(props.onMoveNode).not.toHaveBeenCalled();

    fireEvent.mouseUp(stage);
    expect(props.onMoveNode).toHaveBeenCalledWith('stage-1', { x: 112, y: 118 });
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

    screen.getByTestId('graph-view').focus();
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
    expect(splitEdgePorts('notes->brief')).toEqual({ sourcePort: 'notes', targetPort: 'brief' });
    expect(splitEdgePorts(' notes  ->  brief ')).toEqual({
      sourcePort: 'notes',
      targetPort: 'brief',
    });
    expect(splitEdgePorts('summary')).toEqual({ sourcePort: 'summary', targetPort: 'summary' });
    expect(splitEdgePorts('notes -> ')).toEqual({ sourcePort: 'notes', targetPort: '' });
  });

  it('derives unique wait ports with the canonical whitespace-tolerant parser', () => {
    const edges: WorkflowEdge[] = [
      { ...edge, id: 'in-1', target: 'wait-1', label: 'queued->waiting' },
      { ...edge, id: 'in-duplicate', target: 'wait-1', label: 'retry -> waiting' },
      { ...edge, id: 'in-2', target: 'wait-1', label: 'review  ->  review.waiting' },
      { ...edge, id: 'out-1', source: 'wait-1', label: 'observed->publish' },
      { ...edge, id: 'out-duplicate', source: 'wait-1', label: 'observed -> archive' },
      { ...edge, id: 'invalid', source: 'wait-1', label: 'missing target' },
    ];

    expect(waitPortLists('wait-1', edges)).toEqual({
      incomingEvents: ['waiting', 'review.waiting'],
      outgoingEvents: ['observed'],
    });
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
    expect(isGraphNodeKind('wait')).toBe(true);
    expect(isGraphNodeKind('end')).toBe(true);
    expect(isGraphNodeKind('unknown')).toBe(false);
  });

  it('anchors stage edges to explicit boundary ports and never falls back to node centres', () => {
    const stageWithMembers = {
      ...stageNode,
      personaIds: ['ravn-alpha', 'ravn-beta'],
    };

    expect(
      edgeAnchor(stageWithMembers as never, 'source', 'summary', personaFixtures as never),
    ).toMatchObject({
      x: stageWithMembers.position.x + STAGE_WIDTH,
    });
    expect(
      edgeAnchor(stageWithMembers as never, 'target', 'brief', personaFixtures as never),
    ).toMatchObject({
      x: stageWithMembers.position.x,
    });
    expect(
      edgeAnchor(stageWithMembers as never, 'source', 'missing', personaFixtures as never),
    ).toBeNull();
    expect(edgeAnchor(gateNode as never, 'target', 'brief', personaFixtures as never)).toBeNull();
  });

  it('anchors wait edges to their configured topic rows', () => {
    const edges: WorkflowEdge[] = [
      { ...edge, id: 'in-1', target: 'wait-1', label: 'queued->waiting' },
      { ...edge, id: 'in-2', target: 'wait-1', label: 'review->review.waiting' },
      { ...edge, id: 'out-1', source: 'wait-1', label: 'observed->publish' },
      { ...edge, id: 'out-2', source: 'wait-1', label: 'merged->done' },
    ];

    expect(edgeAnchor(waitNode, 'target', 'review.waiting', [], edges)).toEqual({
      x: waitNode.position.x,
      y: waitNode.position.y + WAIT_HEIGHT + 16 + 7,
    });
    expect(edgeAnchor(waitNode, 'source', 'merged', [], edges)).toEqual({
      x: waitNode.position.x + 168,
      y: waitNode.position.y + WAIT_HEIGHT + 16 + 7,
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
    expect(screen.queryByText('notes -> brief')).not.toBeInTheDocument();

    fireEvent.click(paths[1]!);
    expect(screen.getByText('notes -> brief')).toBeInTheDocument();
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

  // ---------------------------------------------------------------------------
  // InsertFromPortMenu — "add a step from a port"
  // ---------------------------------------------------------------------------

  const insertPersonas = [
    {
      id: 'reviewer',
      label: 'reviewer',
      role: 'review',
      consumes: ['code.changed'],
      produces: ['review.completed'],
    },
    {
      id: 'unrelated',
      label: 'unrelated',
      role: 'build',
      consumes: ['other.event'],
      produces: ['other.done'],
    },
  ];

  it('does not show the insert-from-port menu when onAddStageWithPersona is not supplied', () => {
    render(
      <GraphView
        {...defaultProps()}
        personas={insertPersonas as never}
        connectingFromId="stage-1"
        connectingFromLabel="code.changed"
      />,
    );
    expect(screen.queryByTestId('insert-from-port-menu')).not.toBeInTheDocument();
  });

  it('lists only personas that consume the port event type, and inserts + exits connecting mode on pick', () => {
    const onAddStageWithPersona = vi.fn();
    const onCancelConnect = vi.fn();
    render(
      <GraphView
        {...defaultProps()}
        personas={insertPersonas as never}
        connectingFromId="stage-1"
        connectingFromLabel="code.changed"
        onAddStageWithPersona={onAddStageWithPersona}
        onCancelConnect={onCancelConnect}
      />,
    );

    expect(screen.getByTestId('insert-from-port-menu')).toBeInTheDocument();
    expect(screen.getByTestId('insert-from-port-reviewer')).toBeInTheDocument();
    expect(screen.queryByTestId('insert-from-port-unrelated')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('insert-from-port-reviewer'));

    // stageNode fixture sits at (100, 100); the inserted stage lands to its right.
    expect(onAddStageWithPersona).toHaveBeenCalledWith('reviewer', undefined, {
      x: 100 + STAGE_WIDTH + 96,
      y: 100,
    });
    expect(onCancelConnect).toHaveBeenCalledTimes(1);
  });

  it('shows an empty state when nothing in the catalog consumes the port event type', () => {
    render(
      <GraphView
        {...defaultProps()}
        personas={insertPersonas as never}
        connectingFromId="stage-1"
        connectingFromLabel="no.such.event"
        onAddStageWithPersona={vi.fn()}
      />,
    );
    expect(screen.getByTestId('insert-from-port-menu')).toHaveTextContent(
      'Nothing in the persona catalog consumes',
    );
  });

  it('creates a persona stage from the exact selected output contract', () => {
    const onAddStageFromPort = vi.fn();
    render(
      <GraphView
        {...defaultProps()}
        personas={insertPersonas as never}
        connectingFromId="stage-1"
        connectingFromLabel="code.changed"
        onAddStageWithPersona={vi.fn()}
        onAddStageFromPort={onAddStageFromPort}
      />,
    );

    fireEvent.click(screen.getByTestId('insert-from-port-reviewer'));
    expect(onAddStageFromPort).toHaveBeenCalledWith('stage-1', 'code.changed', 'reviewer', {
      x: 100 + STAGE_WIDTH + 96,
      y: 100,
    });
  });

  it('hides the flow-control section when onAddNodeFromPort is not supplied', () => {
    render(
      <GraphView
        {...defaultProps()}
        personas={insertPersonas as never}
        connectingFromId="stage-1"
        connectingFromLabel="code.changed"
        onAddStageWithPersona={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('insert-from-port-flow-gate')).not.toBeInTheDocument();
  });

  it('offers flow-control candidates and wires the edge via addNodeFromPort on pick', () => {
    const onAddNodeFromPort = vi.fn();
    const onCancelConnect = vi.fn();
    render(
      <GraphView
        {...defaultProps()}
        personas={insertPersonas as never}
        connectingFromId="stage-1"
        connectingFromLabel="code.changed"
        onAddStageWithPersona={vi.fn()}
        onAddNodeFromPort={onAddNodeFromPort}
        onCancelConnect={onCancelConnect}
      />,
    );

    expect(screen.getByTestId('insert-from-port-flow-gate')).toBeInTheDocument();
    expect(screen.getByTestId('insert-from-port-flow-cond')).toBeInTheDocument();
    expect(screen.getByTestId('insert-from-port-flow-wait')).toBeInTheDocument();
    expect(screen.getByTestId('insert-from-port-flow-end')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('insert-from-port-flow-gate'));

    expect(onAddNodeFromPort).toHaveBeenCalledWith('gate', 'stage-1', 'code.changed', {
      x: 100 + STAGE_WIDTH + 96,
      y: 100,
    });
    expect(onCancelConnect).toHaveBeenCalledTimes(1);
  });

  it('uses the same shared endpoint coordinates for visible sockets and edge paths', () => {
    const typedStage: WorkflowNode = {
      ...stageNode,
      personaIds: ['ravn-alpha'],
      stageMembers: [{ personaId: 'ravn-alpha', model: 'gpt-5', budget: 40 }],
    };
    const typedEdge: WorkflowEdge = { ...edge, label: 'notes -> brief' };
    render(
      <GraphView
        {...defaultProps()}
        nodes={[typedStage, gateNode]}
        edges={[typedEdge]}
        personas={personaFixtures}
      />,
    );

    const source = document.querySelector(
      '[data-workflow-socket="true"][data-node-id="stage-1"][data-direction="output"][data-event-type="notes"]',
    );
    const target = document.querySelector(
      '[data-workflow-socket="true"][data-node-id="gate-1"][data-direction="input"][data-event-type="brief"]',
    );
    const path = screen.getByTestId('workflow-edge-e1').querySelectorAll('path')[1];
    const coordinates =
      path
        ?.getAttribute('d')
        ?.match(/-?\d+(?:\.\d+)?/g)
        ?.map(Number) ?? [];

    expect(source).not.toBeNull();
    expect(target).not.toBeNull();
    expect(coordinates.slice(0, 2)).toEqual([
      Number(source?.getAttribute('cx')),
      Number(source?.getAttribute('cy')),
    ]);
    expect(coordinates.slice(-2)).toEqual([
      Number(target?.getAttribute('cx')),
      Number(target?.getAttribute('cy')),
    ]);
  });

  it('keeps unselected cards compact and reveals their full typed contract on selection', () => {
    const typedStage: WorkflowNode = {
      ...stageNode,
      personaIds: ['ravn-alpha', 'ravn-beta'],
      stageMembers: [
        { personaId: 'ravn-alpha', model: 'gpt-5', budget: 40 },
        { personaId: 'ravn-beta', model: 'gpt-5', budget: 40 },
      ],
    };
    const connected: WorkflowEdge = { ...edge, label: 'notes -> approval.requested' };
    const props = {
      ...defaultProps(),
      nodes: [typedStage, gateNode],
      edges: [connected],
      personas: personaFixtures,
    };
    const { rerender } = render(<GraphView {...props} />);

    expect(
      document.querySelectorAll(
        '[data-workflow-socket="true"][data-node-id="stage-1"][data-direction="output"]',
      ),
    ).toHaveLength(1);
    expect(screen.queryByText('summary')).not.toBeInTheDocument();
    expect(screen.getByText('+1 outcomes')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('hidden-outcomes-stage-1'));
    expect(props.onSelectNode).toHaveBeenCalledWith('stage-1');

    rerender(<GraphView {...props} selectedNodeId="stage-1" />);
    expect(
      document.querySelectorAll(
        '[data-workflow-socket="true"][data-node-id="stage-1"][data-direction="output"]',
      ),
    ).toHaveLength(2);
    expect(screen.getAllByText('summary')).toHaveLength(2);
    const socketRows = document.querySelectorAll(
      '[data-workflow-socket="true"][data-node-id="stage-1"]',
    );
    for (const socket of socketRows) {
      expect(Number(socket.getAttribute('cy'))).toBeGreaterThan(
        typedStage.position.y + stageNodeHeight(typedStage as never),
      );
    }
  });

  it('routes feedback edges through a lane below every card', () => {
    const feedback: WorkflowEdge = {
      ...edge,
      id: 'feedback',
      label: 'review.changes_requested -> brief',
    };
    render(<GraphView {...defaultProps()} edges={[feedback]} />);
    const group = screen.getByTestId('workflow-edge-feedback');
    const path = group.querySelectorAll('path')[1]?.getAttribute('d') ?? '';
    const points = path.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];

    expect(group).toHaveAttribute('data-feedback', 'true');
    expect(Math.max(...points.filter((_, index) => index % 2 === 1))).toBeGreaterThan(210);
    expect(path).toContain(' L ');
  });

  it('uses short horizontal tangents for close forward connections', () => {
    const closeGate = { ...gateNode, position: { x: 350, y: 100 } };
    const typedStage: WorkflowNode = {
      ...stageNode,
      personaIds: ['ravn-alpha'],
      stageMembers: [{ personaId: 'ravn-alpha', model: 'gpt-5', budget: 40 }],
    };
    const forward: WorkflowEdge = {
      ...edge,
      label: 'notes -> approval.requested',
      cp1: { x: 400, y: 300 },
      cp2: { x: -400, y: -300 },
    };
    render(
      <GraphView
        {...defaultProps()}
        nodes={[typedStage, closeGate]}
        edges={[forward]}
        personas={personaFixtures}
      />,
    );
    const path = screen.getByTestId('workflow-edge-e1').querySelectorAll('path')[1];
    const numbers =
      path
        ?.getAttribute('d')
        ?.match(/-?\d+(?:\.\d+)?/g)
        ?.map(Number) ?? [];
    expect(numbers[3]).toBe(numbers[1]);
    expect(numbers[5]).toBe(numbers[7]);
    expect(Math.abs((numbers[2] ?? 0) - (numbers[0] ?? 0))).toBeLessThanOrEqual(80);
  });

  it('provides working zoom, fit, and one-to-one controls', () => {
    render(<GraphView {...defaultProps()} />);
    const canvas = screen.getByTestId('graph-canvas');
    Object.defineProperty(canvas, 'getBoundingClientRect', {
      value: () => ({ left: 0, top: 0, width: 900, height: 600 }),
    });

    fireEvent.click(screen.getByTestId('zoom-in'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('115%');
    fireEvent.click(screen.getByTestId('zoom-fit'));
    expect(screen.getByTestId('zoom-level')).not.toHaveTextContent('115%');
    fireEvent.click(screen.getByTestId('zoom-one-to-one'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
  });

  it('opens the add-step menu from a canvas-scoped Tab shortcut', () => {
    render(<GraphView {...defaultProps()} />);
    screen.getByTestId('graph-view').focus();
    fireEvent.keyDown(screen.getByTestId('graph-view'), { key: 'Tab' });
    expect(screen.getByTestId('canvas-node-picker')).toBeInTheDocument();
  });

  it('keeps immutable workflows inspectable while disabling canvas mutations', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1', readOnly: true };
    render(<GraphView {...props} />);
    const stage = screen.getByTestId('workflow-node-stage-1');

    expect(screen.queryByTestId('open-add-step')).not.toBeInTheDocument();
    expect(screen.queryByTestId('delete-selected')).not.toBeInTheDocument();
    fireEvent.mouseDown(stage, { clientX: 100, clientY: 100 });
    fireEvent.mouseMove(stage, { clientX: 140, clientY: 140 });
    fireEvent.mouseUp(stage);
    expect(props.onMoveNode).not.toHaveBeenCalled();
    fireEvent.contextMenu(stage);
    expect(props.onInspectNode).toHaveBeenCalledWith('stage-1');
  });

  it('does not intercept delete keys from editable targets inside the canvas', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    const input = document.createElement('input');
    screen.getByTestId('graph-view').append(input);
    input.focus();
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
    expect(props.onDeleteNode).not.toHaveBeenCalled();
  });

  it('supports keyboard selection and inspection on a focused node', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    const stage = screen.getByTestId('workflow-node-stage-1');
    fireEvent.keyDown(stage, { key: ' ' });
    fireEvent.keyDown(stage, { key: 'Enter' });
    expect(props.onSelectNode).toHaveBeenCalledWith('stage-1');
    expect(props.onInspectNode).toHaveBeenCalledWith('stage-1');
  });

  it('resets fit safely when the workflow has no nodes', () => {
    render(<GraphView {...defaultProps()} nodes={[]} edges={[]} />);
    fireEvent.click(screen.getByTestId('zoom-in'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('115%');
    fireEvent.click(screen.getByTestId('zoom-fit'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
  });
});

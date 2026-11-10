import { describe, it, expect, vi } from 'vitest';
import { act, render, screen, fireEvent, createEvent } from '@testing-library/react';
import {
  GraphView,
  reworkLimitLabel,
  workflowCardContent,
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
import {
  CARD_HEADER_HEIGHT,
  CARD_PORT_PAD,
  CARD_PORT_ROW,
  CARD_WIDTH,
} from '../../domain/workflowGeometry';

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

const includeNode: WorkflowNode = {
  id: 'include-1',
  kind: 'include',
  label: 'Plan the delivery',
  workflow: 'planning',
  nodes: {
    'planning-analysis': 'delivery-plan-author',
    'planning-reviews': 'delivery-plan-reviews',
  },
  position: { x: 400, y: 320 },
};

const subworkflowNode: WorkflowNode = {
  id: 'subworkflow-1',
  kind: 'subworkflow',
  label: 'Research thread',
  maxChildren: 3,
  templates: { research: 'Research' },
  position: { x: 860, y: 80 },
} as WorkflowNode;

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
    onStartConnect: vi.fn(),
    onCancelConnect: vi.fn(),
    onCompleteConnect: vi.fn(),
  };
}

function openAddMenu() {
  fireEvent.click(screen.getByTestId('open-add-step'));
}

/** A node's port socket for one event, on one side. */
function socketFor(nodeId: string, direction: 'input' | 'output', eventType: string) {
  const socket = screen
    .getByTestId(`workflow-sockets-${nodeId}`)
    .querySelector(`circle[data-direction="${direction}"][data-event-type="${eventType}"]`);
  if (!socket) throw new Error(`no ${direction} socket for ${eventType} on ${nodeId}`);
  return socket;
}

/** Where the canvas drew a node's card. */
function cardBox(nodeId: string) {
  const card = screen.getByTestId(`workflow-node-${nodeId}`).querySelector('foreignObject')!;
  return {
    x: Number(card.getAttribute('x')),
    y: Number(card.getAttribute('y')),
    width: Number(card.getAttribute('width')),
    height: Number(card.getAttribute('height')),
  };
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

  it('opens all nine node kinds on background right-click and inserts at the transformed point', () => {
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
      'include',
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

  it('lays the workflow out left to right itself, whatever positions were saved', () => {
    const wired: WorkflowEdge = { ...edge, label: 'stage.completed -> stage.completed' };
    // Saved the other way round: the gate left of the stage that feeds it.
    const props = {
      ...defaultProps(),
      nodes: [
        { ...stageNode, position: { x: 900, y: 500 } },
        { ...gateNode, position: { x: 10, y: 10 } },
      ],
      edges: [wired],
    };
    render(<GraphView {...props} />);

    expect(cardBox('stage-1').x).toBeLessThan(cardBox('gate-1').x);
    expect(screen.queryByTestId('auto-layout')).toBeNull();
  });

  it('selects a card on mouse down and never moves it', () => {
    const props = defaultProps();
    render(<GraphView {...props} />);
    const before = cardBox('stage-1');
    const card = screen.getByTestId('workflow-node-stage-1');

    fireEvent.mouseDown(card, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.mouseMove(card, { clientX: 300, clientY: 260 });
    fireEvent.mouseUp(card);

    expect(props.onSelectNode).toHaveBeenCalledWith('stage-1');
    expect(cardBox('stage-1')).toEqual(before);
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
    expect(screen.getByTestId('workflow-node-wait-1')).toHaveTextContent(
      'Waits for an outside event',
    );
    // A configured wait has ports like any card: no "configure" sockets.
    expect(screen.queryByTestId('wait-output-wait-1')).toBeNull();
    fireEvent.click(socketFor('wait-1', 'output', 'custom.build.finished'));
    expect(props.onStartConnect).toHaveBeenCalledWith('wait-1', 'custom.build.finished');
    fireEvent.click(socketFor('wait-1', 'output', 'custom.merge.finished'));
    expect(props.onStartConnect).toHaveBeenCalledWith('wait-1', 'custom.merge.finished');

    rerender(
      <GraphView {...props} connectingFromId="stage-1" connectingFromLabel="custom.build.queued" />,
    );
    fireEvent.click(socketFor('wait-1', 'input', 'custom.build.waiting'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('wait-1', 'custom.build.waiting');
    fireEvent.click(socketFor('wait-1', 'input', 'custom.review.waiting'));
    expect(props.onCompleteConnect).toHaveBeenCalledWith('wait-1', 'custom.review.waiting');
  });

  it('prompts for event types when connecting an unconfigured wait', () => {
    const prompt = vi
      .spyOn(window, 'prompt')
      .mockReturnValueOnce('custom.build.finished')
      .mockReturnValueOnce('custom.build.waiting');
    const props = { ...defaultProps(), nodes: [waitNode], edges: [] };
    const { rerender } = render(<GraphView {...props} />);

    expect(screen.getByTestId('workflow-node-wait-1')).toHaveTextContent(
      'Waits for an outside event',
    );
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

  it("puts the selected step's toolbar above its card and no other", () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getAllByTestId('step-toolbar')).toHaveLength(1);
    expect(screen.getByRole('toolbar', { name: 'Stage 1 actions' })).toBeInTheDocument();
  });

  it('says what to do while a wire is drawn, and hides the step toolbar', () => {
    const props = { ...defaultProps(), connectingFromId: 'stage-1', selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    expect(screen.getByText(/pick the step it goes to/i)).toBeInTheDocument();
    expect(screen.queryByTestId('step-toolbar')).toBeNull();
  });

  it('renders with no nodes', () => {
    const props = { ...defaultProps(), nodes: [], edges: [] };
    render(<GraphView {...props} />);
    expect(screen.getByTestId('graph-canvas')).toBeInTheDocument();
  });

  it('removes the selected step from its toolbar', () => {
    const props = { ...defaultProps(), selectedNodeId: 'stage-1' };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Stage 1' }));
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
    expect(screen.getByTestId('delete-selected')).toBeInTheDocument();
    // A step with a problem is marked on its card: its border and a badge, by severity.
    expect(screen.getByTestId('workflow-node-stage-warning')).toHaveAttribute(
      'data-issue',
      'warning',
    );
    expect(screen.getByTestId('workflow-node-stage-error')).toHaveAttribute('data-issue', 'error');
    expect(screen.getByTestId('workflow-node-resource-1')).toHaveAttribute('data-issue', 'warning');

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

  describe('include node', () => {
    it('renders its label, pinned workflow alias, and mapped node count', () => {
      render(<GraphView {...defaultProps()} nodes={[includeNode]} edges={[]} />);
      const card = screen.getByTestId('workflow-node-include-1');
      expect(card).toHaveAttribute('data-kind', 'include');
      expect(card).toHaveTextContent('Plan the delivery');
      expect(card).toHaveTextContent('planning · 2 nodes');
    });

    it('shows a fallback subtitle before a workflow is chosen', () => {
      const unconfigured: WorkflowNode = {
        ...includeNode,
        id: 'include-2',
        workflow: '',
        nodes: {},
      } as WorkflowNode;
      render(<GraphView {...defaultProps()} nodes={[unconfigured]} edges={[]} />);
      expect(screen.getByTestId('workflow-node-include-2')).toHaveTextContent(
        'choose a workflow · 0 nodes',
      );
    });

    it('derives an output socket from an edge whose source is a provided local id', () => {
      const edges: WorkflowEdge[] = [
        {
          id: 'e1',
          source: 'delivery-plan-author',
          target: 'gate-1',
          label: 'plan.ready -> plan.ready',
          cp1: { x: 80, y: 0 },
          cp2: { x: -80, y: 0 },
        },
      ];
      render(<GraphView {...defaultProps()} nodes={[includeNode, gateNode]} edges={edges} />);
      const socket = screen.getByTestId('workflow-socket-include-1-output-0');
      expect(socket).toHaveAttribute('data-event-type', 'plan.ready');
    });

    it('completes a connection onto the provided id an existing edge already uses', () => {
      const edges: WorkflowEdge[] = [
        {
          id: 'e1',
          source: 'stage-1',
          target: 'delivery-plan-author',
          label: 'plan.requested -> plan.requested',
          cp1: { x: 80, y: 0 },
          cp2: { x: -80, y: 0 },
        },
      ];
      const props = {
        ...defaultProps(),
        nodes: [stageNode, includeNode],
        edges,
        connectingFromId: 'stage-1',
        connectingFromLabel: 'plan.requested',
        selectedNodeId: 'stage-1',
      };
      render(<GraphView {...props} />);
      fireEvent.click(screen.getByTestId('workflow-socket-include-1-input-0'));
      expect(props.onCompleteConnect).toHaveBeenCalledWith(
        'delivery-plan-author',
        'plan.requested',
      );
    });

    it('deletes an include node like any other node', () => {
      const props = {
        ...defaultProps(),
        nodes: [includeNode],
        edges: [],
        selectedNodeId: 'include-1',
      };
      render(<GraphView {...props} />);
      fireEvent.click(screen.getByTestId('delete-selected'));
      expect(props.onDeleteNode).toHaveBeenCalledWith('include-1');
    });
  });

  it('draws every kind as one card recipe, toned by kind, with non-positioned roots', () => {
    const { container } = render(
      <GraphView
        {...defaultProps()}
        nodes={[triggerNode, stageNode, gateNode, condNode, waitNode, endNode, resourceNode]}
        edges={[]}
      />,
    );

    const tones = [...container.querySelectorAll<HTMLElement>('.workflow-card')].map(
      (card) => card.dataset.tone,
    );
    expect(tones).toEqual(['trigger', 'agent', 'gate', 'cond', 'wait', 'end', 'resource']);
    // WebKit transforms a foreignObject's positioned roots wrongly inside a scaled SVG.
    for (const card of container.querySelectorAll('.workflow-card')) {
      expect(card).not.toHaveClass('niuu:relative');
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
    const graphLayer = svg.querySelector('g')!;
    const transform = () => {
      const [x, y, scale] = (
        graphLayer.getAttribute('transform')?.match(/-?\d+(?:\.\d+)?/g) ?? []
      ).map(Number);
      return { x: x!, y: y!, scale: scale! };
    };
    const before = transform();

    // Zooms about the pointer: at the canvas origin, the origin stays put.
    fireEvent.wheel(svg, { deltaY: -100 });
    expect(transform().scale).toBeCloseTo(before.scale * 1.1);

    const zoomed = transform();
    fireEvent.mouseDown(svg, { clientX: 10, clientY: 20 });
    expect(svg).toHaveStyle({ cursor: 'grabbing' });
    expect(props.onSelectNode).toHaveBeenCalledWith(null);

    fireEvent.mouseMove(svg, { clientX: 30, clientY: 55 });
    expect(transform().x).toBeCloseTo(zoomed.x + 20);
    expect(transform().y).toBeCloseTo(zoomed.y + 35);

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

    // Onto the card where the canvas laid it out.
    const box = cardBox('stage-1');
    const drop = createEvent.drop(svg);
    Object.defineProperty(drop, 'clientX', { value: box.x + box.width / 2 });
    Object.defineProperty(drop, 'clientY', { value: box.y + box.height / 2 });
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
    // While a wire is drawn, a card shows every input it starts on, beside its outcomes.
    fireEvent.click(socketFor('stage-1', 'input', 'brief'));
    fireEvent.click(socketFor('stage-1', 'output', 'summary'));
    fireEvent.mouseDown(screen.getByTestId('workflow-node-gate-1'), { clientX: 300, clientY: 100 });

    expect(connectProps.onCompleteConnect).toHaveBeenCalledWith('stage-1', 'brief');
    expect(connectProps.onStartConnect).toHaveBeenCalledWith('stage-1', 'summary');
    expect(connectProps.onCompleteConnect).toHaveBeenCalledWith('gate-1');

    const idleProps = {
      ...defaultProps(),
      nodes: [stageWithPorts],
      edges: [],
      personas: personaFixtures,
      onDeleteEdge: vi.fn(),
    };

    rerender(<GraphView {...idleProps} />);
    // Idle and unselected: every outcome shows, unwired; no input does.
    const sockets = screen.getByTestId('workflow-sockets-stage-1');
    expect(
      [...sockets.querySelectorAll('circle[data-direction="output"]')].map((socket) =>
        socket.getAttribute('data-wiring'),
      ),
    ).toEqual(['open', 'open']);
    expect(sockets.querySelectorAll('circle[data-direction="input"]')).toHaveLength(0);

    fireEvent.click(socketFor('stage-1', 'output', 'summary'));
    expect(idleProps.onStartConnect).toHaveBeenCalledWith('stage-1', 'summary');
  });

  it('wires a card dropped on while connecting when its step starts on the event', () => {
    const stageWithPorts: WorkflowNode = { ...stageNode, personaIds: ['ravn-beta'] };
    const props = {
      ...defaultProps(),
      nodes: [stageWithPorts, gateNode],
      edges: [],
      personas: personaFixtures,
      connectingFromId: 'gate-1',
      connectingFromLabel: 'notes',
    };
    const { rerender } = render(<GraphView {...props} />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'), { button: 0 });
    expect(props.onCompleteConnect).toHaveBeenCalledWith('stage-1', 'notes');

    // A stage that does not start on the event needs its port picked instead.
    rerender(<GraphView {...props} connectingFromLabel="unheard.event" />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'), { button: 0 });
    expect(props.onCompleteConnect).toHaveBeenCalledTimes(1);
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

    // Ports sit on the card's edges, in rows under its head: brief/notes in, notes/summary out.
    expect(
      edgeAnchor(stageWithMembers as never, 'source', 'summary', personaFixtures as never),
    ).toEqual({
      x: stageWithMembers.position.x + CARD_WIDTH,
      y: stageWithMembers.position.y + CARD_HEADER_HEIGHT + CARD_PORT_PAD + CARD_PORT_ROW * 1.5,
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

    // Ports are listed by event, so review.waiting and merged take the first rows.
    const firstRow = waitNode.position.y + CARD_HEADER_HEIGHT + CARD_PORT_PAD + CARD_PORT_ROW / 2;
    expect(edgeAnchor(waitNode, 'target', 'review.waiting', [], edges)).toEqual({
      x: waitNode.position.x,
      y: firstRow,
    });
    expect(edgeAnchor(waitNode, 'source', 'merged', [], edges)).toEqual({
      x: waitNode.position.x + CARD_WIDTH,
      y: firstRow,
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
    // The event a wire carries is written on it, until the Events toggle is turned off.
    expect(screen.getByTestId('workflow-edge-event-labeled-edge')).toHaveTextContent(
      'notes → brief',
    );
    fireEvent.click(screen.getByTestId('toggle-events'));
    expect(screen.getByTestId('toggle-events')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByTestId('workflow-edge-event-labeled-edge')).toBeNull();

    // A selected wire says its event whatever the toggle.
    fireEvent.click(paths[1]!);
    expect(screen.getByTestId('workflow-edge-event-labeled-edge')).toHaveTextContent(
      'notes → brief',
    );
    expect(screen.getByTestId('workflow-edge-labeled-edge')).toHaveAttribute(
      'data-selected',
      'true',
    );
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
    expect(screen.queryByTestId('step-toolbar')).toBeNull();
    // A card names its step in full (the card ellipsises it) and says who runs it.
    expect(
      screen.getByText('Stage label that is definitely longer than eighteen'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-stage-long')).toHaveTextContent(
      'ravn-alpha + ravn-beta · together',
    );
    expect(screen.getByTestId('workflow-node-gate-long')).toHaveTextContent('You decide');
    expect(screen.getByTestId('workflow-node-cond-long')).toHaveTextContent('x > 0');

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

    expect(screen.getByTestId('workflow-node-resource-ephemeral')).toHaveTextContent('ephemeral');
    expect(screen.getByTestId('workflow-node-trigger-fallback')).toHaveTextContent(
      'When manual dispatch',
    );
    expect(screen.getByTestId('workflow-node-end-short')).toHaveTextContent('Finish flow now');
    expect(screen.getByTestId('delete-selected')).toBeInTheDocument();
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

    // The inserted stage is placed to the right of the card it follows.
    const stage = cardBox('stage-1');
    expect(onAddStageWithPersona).toHaveBeenCalledWith('reviewer', undefined, {
      x: stage.x + CARD_WIDTH + 120,
      y: stage.y,
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
    const stage = cardBox('stage-1');
    expect(onAddStageFromPort).toHaveBeenCalledWith('stage-1', 'code.changed', 'reviewer', {
      x: stage.x + CARD_WIDTH + 120,
      y: stage.y,
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

    const stage = cardBox('stage-1');
    expect(onAddNodeFromPort).toHaveBeenCalledWith('gate', 'stage-1', 'code.changed', {
      x: stage.x + CARD_WIDTH + 120,
      y: stage.y,
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

  it("shows every outcome, and a step's incoming events only where wired or selected", () => {
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
    const sockets = (direction: string) =>
      [
        ...document.querySelectorAll(
          `[data-workflow-socket="true"][data-node-id="stage-1"][data-direction="${direction}"]`,
        ),
      ].map((socket) => [
        socket.getAttribute('data-event-type'),
        socket.getAttribute('data-wiring'),
      ]);

    // Both outcomes, the wired one marked so; no inputs, since nothing arrives at the stage.
    expect(sockets('output')).toEqual([
      ['notes', 'forward'],
      ['summary', 'open'],
    ]);
    expect(sockets('input')).toEqual([]);
    expect(screen.queryByText('+1 outcomes')).toBeNull();

    rerender(<GraphView {...props} selectedNodeId="stage-1" />);
    expect(sockets('input').map(([event]) => event)).toEqual(['brief', 'notes']);
    // Every port row sits under the card's head.
    const stage = cardBox('stage-1');
    for (const socket of document.querySelectorAll(
      '[data-workflow-socket="true"][data-node-id="stage-1"]',
    )) {
      expect(Number(socket.getAttribute('cy'))).toBeGreaterThan(stage.y + CARD_HEADER_HEIGHT);
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

  it('frames the workflow once its canvas has a size, and offers zoom, fit and 1:1', () => {
    const size = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 2400,
      height: 1600,
    } as DOMRect);
    render(<GraphView {...defaultProps()} />);
    // Framed on mount, centred and never zoomed in past 100%.
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
    expect(screen.getByTestId('graph-canvas').querySelector('g')).not.toHaveAttribute(
      'transform',
      'translate(0,0) scale(1)',
    );

    fireEvent.click(screen.getByTestId('zoom-out'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('87%');
    // Fit may zoom in to fill a large canvas.
    fireEvent.click(screen.getByTestId('zoom-fit'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('300%');
    fireEvent.click(screen.getByTestId('zoom-one-to-one'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
    size.mockRestore();
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

  it('resets the one-to-one zoom safely when the workflow has no nodes', () => {
    render(<GraphView {...defaultProps()} nodes={[]} edges={[]} />);
    fireEvent.click(screen.getByTestId('zoom-in'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('115%');
    fireEvent.click(screen.getByTestId('zoom-one-to-one'));
    expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
  });

  it('supports keyboard selection, inspection, and context-menu on gate, cond, wait, end, include, and subworkflow cards', () => {
    const props = {
      ...defaultProps(),
      nodes: [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode],
      edges: [],
    };
    render(<GraphView {...props} />);
    for (const node of [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode]) {
      const el = screen.getByTestId(`workflow-node-${node.id}`);
      fireEvent.keyDown(el, { key: ' ' });
      expect(props.onSelectNode).toHaveBeenCalledWith(node.id);
      fireEvent.keyDown(el, { key: 'Enter' });
      expect(props.onInspectNode).toHaveBeenCalledWith(node.id);
      // A key that is neither Enter nor Space is a no-op — exercises the
      // early-return branch of each card's onKeyDown handler.
      props.onSelectNode.mockClear();
      props.onInspectNode.mockClear();
      fireEvent.keyDown(el, { key: 'a' });
      expect(props.onSelectNode).not.toHaveBeenCalled();
      expect(props.onInspectNode).not.toHaveBeenCalled();

      fireEvent.contextMenu(el);
      expect(props.onInspectNode).toHaveBeenCalledWith(node.id);
      props.onInspectNode.mockClear();
    }
  });

  it('selects every card kind with its toolbar, and hides the toolbar while connecting or read-only', () => {
    const props = {
      ...defaultProps(),
      nodes: [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode],
      edges: [],
    };
    for (const node of [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode]) {
      const { unmount } = render(<GraphView {...props} selectedNodeId={node.id} />);
      expect(screen.getByTestId(`workflow-node-${node.id}`)).toHaveAttribute(
        'data-selected',
        'true',
      );
      expect(screen.getByRole('toolbar', { name: `${node.label} actions` })).toBeInTheDocument();
      unmount();
    }

    const { rerender } = render(
      <GraphView {...props} selectedNodeId="gate-1" connectingFromId="stage-1" />,
    );
    expect(screen.queryByTestId('step-toolbar')).not.toBeInTheDocument();

    rerender(<GraphView {...props} selectedNodeId="gate-1" readOnly />);
    expect(screen.queryByTestId('step-toolbar')).not.toBeInTheDocument();
  });

  it('shows a crosshair cursor on gate/cond/wait/end/include/subworkflow cards while connecting', () => {
    const props = {
      ...defaultProps(),
      nodes: [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode],
      edges: [],
    };
    const { rerender } = render(<GraphView {...props} />);
    for (const node of [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode]) {
      expect(screen.getByTestId(`workflow-node-${node.id}`)).toHaveStyle({ cursor: 'pointer' });
    }
    rerender(<GraphView {...props} connectingFromId="stage-1" />);
    for (const node of [gateNode, condNode, waitNode, endNode, includeNode, subworkflowNode]) {
      expect(screen.getByTestId(`workflow-node-${node.id}`)).toHaveStyle({ cursor: 'crosshair' });
    }
  });

  it('leaves an unconfigured wait node untouched when its incoming ports are already configured', () => {
    const configuredWait: WorkflowEdge = {
      id: 'wait-configured-in',
      source: 'stage-1',
      target: 'wait-1',
      label: 'notes->waiting.for.notes',
      cp1: { x: 80, y: 0 },
      cp2: { x: -80, y: 0 },
    };
    const prompt = vi.spyOn(window, 'prompt');
    const props = {
      ...defaultProps(),
      nodes: [stageNode, waitNode],
      edges: [configuredWait],
      connectingFromId: 'stage-1',
      connectingFromLabel: 'notes',
    };
    render(<GraphView {...props} />);
    // Mouse-down-to-complete-connect on the card body (not a specific port)
    // routes through `completeUnconfiguredConnection`, which no-ops once an
    // incoming port already exists instead of prompting again.
    fireEvent.mouseDown(screen.getByTestId('workflow-node-wait-1'), { button: 0 });
    expect(prompt).not.toHaveBeenCalled();
    prompt.mockRestore();
  });

  it('does not start an outgoing wait connection when the continuation prompt is dismissed', () => {
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(null);
    const props = { ...defaultProps(), nodes: [waitNode], edges: [] };
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('wait-output-wait-1'));
    expect(props.onStartConnect).not.toHaveBeenCalled();
    prompt.mockRestore();
  });

  it('activates the incoming/outgoing wait circle via keyboard and ignores non-activation keys', () => {
    const prompt = vi
      .spyOn(window, 'prompt')
      .mockReturnValueOnce('custom.in')
      .mockReturnValueOnce('custom.out');
    const props = { ...defaultProps(), nodes: [waitNode], edges: [] };
    const { rerender } = render(<GraphView {...props} />);
    const outputCircle = screen.getByTestId('wait-output-wait-1');
    fireEvent.keyDown(outputCircle, { key: 'a' });
    expect(props.onStartConnect).not.toHaveBeenCalled();
    fireEvent.keyDown(outputCircle, { key: 'Enter' });
    expect(props.onStartConnect).toHaveBeenCalledWith('wait-1', 'custom.in');

    rerender(<GraphView {...props} connectingFromId="stage-1" connectingFromLabel="x" />);
    const inputCircle = screen.getByTestId('wait-input-wait-1');
    fireEvent.keyDown(inputCircle, { key: 'a' });
    expect(props.onCompleteConnect).not.toHaveBeenCalled();
    fireEvent.keyDown(inputCircle, { key: ' ' });
    expect(props.onCompleteConnect).toHaveBeenCalledWith('wait-1', 'custom.out');
    prompt.mockRestore();
  });

  it('derives an empty port list when a stage member references a persona that is not in the catalog', () => {
    const stageWithMissingPersona: WorkflowNode = {
      ...stageNode,
      personaIds: ['unknown-persona'],
    };
    expect(stagePortLists(stageWithMissingPersona as never, personaFixtures as never)).toEqual({
      knownInputs: [],
      knownOutputs: [],
    });
  });

  it('returns null from edgeAnchor when no port label is given', () => {
    expect(edgeAnchor(stageNode, 'source', null, [])).toBeNull();
  });

  it('anchors a wait node with configured ports above its port-row footer', () => {
    const waitEdges: WorkflowEdge[] = [
      {
        id: 'wait-in',
        source: 'stage-1',
        target: 'wait-1',
        label: 'notes->waiting.for.notes',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ];
    const anchor = edgeAnchor(waitNode, 'target', 'waiting.for.notes', [], waitEdges);
    expect(anchor).not.toBeNull();
    expect(anchor!.y).toBeGreaterThan(waitNode.position.y);
  });

  it('ignores an ambiguous socket click on an include node when the prompt is dismissed', () => {
    const promptedIncludeNode: WorkflowNode = {
      ...includeNode,
      id: 'include-ambiguous',
      nodes: { a: 'child-a', b: 'child-b' },
    };
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(null);
    const props = {
      ...defaultProps(),
      nodes: [promptedIncludeNode],
      edges: [],
      selectedNodeId: 'include-ambiguous',
    };
    render(<GraphView {...props} />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-include-ambiguous'), { button: 0 });
    expect(props.onStartConnect).not.toHaveBeenCalled();
    prompt.mockRestore();
  });

  // ---------------------------------------------------------------------------
  // The step toolbar, wires and cards of the left-to-right canvas
  // ---------------------------------------------------------------------------

  const flowPersonas = [
    {
      id: 'coder',
      label: 'Coder',
      role: 'build',
      consumes: ['code.requested'],
      produces: ['code.changed', 'code.blocked'],
    },
    {
      id: 'reviewer',
      label: 'Reviewer',
      role: 'review',
      consumes: ['code.changed'],
      produces: ['review.passed', 'review.changes_requested'],
    },
  ];
  const coderStage: WorkflowNode = {
    ...stageNode,
    id: 'code',
    label: 'Implement',
    personaIds: ['coder'],
    stageMembers: [{ personaId: 'coder', model: 'claude-opus-5-5', budget: 40 }],
  };
  const reviewStage: WorkflowNode = {
    ...stageNode,
    id: 'review',
    label: 'Review',
    personaIds: ['reviewer'],
    stageMembers: [{ personaId: 'reviewer', model: '', budget: 40 }],
  };
  const flowEdges: WorkflowEdge[] = [
    {
      ...edge,
      id: 'start',
      source: 'trigger-1',
      target: 'code',
      label: 'code.requested -> code.requested',
    },
    {
      ...edge,
      id: 'review-it',
      source: 'code',
      target: 'review',
      label: 'code.changed -> code.changed',
    },
    {
      ...edge,
      id: 'send-back',
      source: 'review',
      target: 'code',
      label: 'review.changes_requested -> code.requested',
      rework: true,
      maxIterations: 3,
    },
  ];
  function flowProps(overrides: Record<string, unknown> = {}) {
    return {
      ...defaultProps(),
      nodes: [triggerNode, coderStage, reviewStage],
      edges: flowEdges,
      personas: flowPersonas as never,
      ...overrides,
    };
  }

  it("lays a flow out left to right and writes each wire's event on it", () => {
    render(<GraphView {...flowProps()} />);
    expect(cardBox('trigger-1').x).toBeLessThan(cardBox('code').x);
    expect(cardBox('code').x).toBeLessThan(cardBox('review').x);
    expect(screen.getByTestId('workflow-edge-event-review-it')).toHaveTextContent('code.changed');
    expect(screen.getByTestId('workflow-edge-event-start')).toHaveTextContent('code.requested');
  });

  it('routes work sent back under the cards with its limit on a chip', () => {
    render(<GraphView {...flowProps()} />);
    const wire = screen.getByTestId('workflow-edge-send-back');
    expect(wire).toHaveAttribute('data-feedback', 'true');
    expect(screen.getByTestId('workflow-edge-limit-send-back')).toHaveTextContent(
      '↩ up to 3× · review.changes_requested',
    );
    const lowest = Math.max(
      cardBox('code').y + cardBox('code').height,
      cardBox('review').y + cardBox('review').height,
    );
    const ys = (
      wire
        .querySelectorAll('path')[1]
        ?.getAttribute('d')
        ?.match(/-?\d+(?:\.\d+)?/g) ?? []
    )
      .map(Number)
      .filter((_, index) => index % 2 === 1);
    expect(Math.max(...ys)).toBeGreaterThan(lowest);
  });

  it('says a rework wire with no limit has none', () => {
    expect(reworkLimitLabel({})).toBe('↩ no limit');
    expect(reworkLimitLabel({ maxIterations: 2 })).toBe('↩ up to 2×');
  });

  it('marks each outcome by where it goes: on, back, or nowhere yet', () => {
    render(<GraphView {...flowProps()} />);
    expect(socketFor('code', 'output', 'code.changed')).toHaveAttribute('data-wiring', 'forward');
    expect(socketFor('code', 'output', 'code.blocked')).toHaveAttribute('data-wiring', 'open');
    expect(socketFor('review', 'output', 'review.changes_requested')).toHaveAttribute(
      'data-wiring',
      'rework',
    );
    expect(socketFor('review', 'output', 'review.passed')).toHaveAttribute('data-wiring', 'open');
  });

  it('adds the next step on the outcome that goes nowhere yet, from the toolbar and Tab', () => {
    const props = flowProps({ selectedNodeId: 'code' });
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('step-toolbar-next'));
    expect(props.onStartConnect).toHaveBeenCalledWith('code', 'code.blocked');

    props.onStartConnect.mockClear();
    const card = screen.getByTestId('workflow-node-code');
    card.focus();
    fireEvent.keyDown(card, { key: 'Tab' });
    expect(props.onStartConnect).toHaveBeenCalledWith('code', 'code.blocked');
  });

  it('adds the next step on the forward outcome when every outcome is wired', () => {
    // The review passes on to the end and sends changes back: Next follows the pass.
    const wiredBoth = [
      ...flowEdges,
      {
        ...edge,
        id: 'finish',
        source: 'review',
        target: 'end-1',
        label: 'review.passed -> review.passed',
      },
    ];
    const props = flowProps({
      nodes: [triggerNode, coderStage, reviewStage, endNode],
      selectedNodeId: 'review',
      edges: wiredBoth,
    });
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('step-toolbar-next'));
    expect(props.onStartConnect).toHaveBeenCalledWith('review', 'review.passed');
  });

  it('adds a step alongside: from the same step, on the same event', () => {
    const props = flowProps({ selectedNodeId: 'review' });
    render(<GraphView {...props} />);
    fireEvent.click(screen.getByTestId('step-toolbar-alongside'));
    expect(props.onStartConnect).toHaveBeenCalledWith('code', 'code.changed');

    props.onStartConnect.mockClear();
    const card = screen.getByTestId('workflow-node-review');
    card.focus();
    fireEvent.keyDown(card, { key: 'Tab', shiftKey: true });
    expect(props.onStartConnect).toHaveBeenCalledWith('code', 'code.changed');
  });

  it('offers no Alongside for a step nothing feeds, and Next opens the picker without outcomes', () => {
    const props = flowProps({ nodes: [endNode], edges: [], selectedNodeId: 'end-1' });
    render(<GraphView {...props} />);
    expect(screen.queryByTestId('step-toolbar-alongside')).toBeNull();
    expect(screen.queryByTestId('step-toolbar-next')).toBeNull();
    const card = screen.getByTestId('workflow-node-end-1');
    card.focus();
    fireEvent.keyDown(card, { key: 'Tab' });
    expect(screen.getByTestId('canvas-node-picker')).toBeInTheDocument();
  });

  it('writes an outcome name two members share as its event', () => {
    const shared = [
      { id: 'a', label: 'A', role: 'review', consumes: ['x'], produces: ['a.done'] },
      { id: 'b', label: 'B', role: 'review', consumes: ['x'], produces: ['b.done'] },
    ];
    const pair: WorkflowNode = { ...stageNode, personaIds: ['a', 'b'] };
    render(<GraphView {...defaultProps()} nodes={[pair]} edges={[]} personas={shared as never} />);
    const labels = [...screen.getByTestId('workflow-sockets-stage-1').querySelectorAll('text')].map(
      (label) => label.textContent,
    );
    expect(labels).toEqual(['a.done', 'b.done']);
  });

  describe('workflowCardContent', () => {
    it('says who runs a stage, one member or several', () => {
      expect(workflowCardContent(coderStage, flowPersonas)).toMatchObject({
        tone: 'agent',
        icon: 'code',
        subtitle: 'Coder · claude-opus-5-5',
        kindLabel: 'Stage step',
      });
      expect(workflowCardContent(reviewStage, flowPersonas).subtitle).toBe(
        'Reviewer · default model',
      );
      const inTurn: WorkflowNode = {
        ...stageNode,
        personaIds: ['coder', 'reviewer'],
        executionMode: 'sequential',
      };
      expect(workflowCardContent(inTurn, flowPersonas).subtitle).toBe('Coder + Reviewer · in turn');
      expect(workflowCardContent(stageNode, []).subtitle).toBe('No persona yet');
    });

    it("names a persona's icon from its role in its id", () => {
      const icon = (personaId: string) =>
        workflowCardContent({ ...stageNode, personaIds: [personaId] }, []).icon;
      expect(icon('security-auditor')).toBe('shield');
      expect(icon('qa-tester')).toBe('flask');
      expect(icon('planner')).toBe('list');
      expect(icon('docs-writer')).toBe('pen');
      expect(icon('merger')).toBe('code');
      expect(icon('helper')).toBe('bot');
    });

    it('says who decides a gate', () => {
      const gate = (mode: string) => workflowCardContent({ ...gateNode, mode } as WorkflowNode, []);
      expect(gate('human_approval')).toMatchObject({ subtitle: 'You decide', icon: 'person' });
      expect(gate('human_review')).toMatchObject({ subtitle: 'You review', icon: 'review' });
      expect(gate('automated_approval')).toMatchObject({
        subtitle: 'An agent decides',
        icon: 'bot',
      });
      expect(gate('evidence').subtitle).toBe('Evidence decides');
    });

    it('describes waits, conditions, subworkflows, includes and the end', () => {
      expect(
        workflowCardContent(
          { ...waitNode, conditions: ['forge.checks', 'timer'] } as WorkflowNode,
          [],
        ).subtitle,
      ).toBe('Waits for forge.checks, timer');
      expect(workflowCardContent({ ...condNode, predicate: '' } as WorkflowNode, []).subtitle).toBe(
        'No condition yet',
      );
      expect(workflowCardContent(subworkflowNode, []).subtitle).toMatch(/^For each · /);
      expect(workflowCardContent(includeNode, []).subtitle).toBe('planning · 2 nodes');
      expect(workflowCardContent(endNode, [])).toMatchObject({ tone: 'end', icon: 'flag' });
      expect(workflowCardContent({ ...triggerNode, source: '' } as WorkflowNode, []).subtitle).toBe(
        'When started',
      );
    });
  });
});

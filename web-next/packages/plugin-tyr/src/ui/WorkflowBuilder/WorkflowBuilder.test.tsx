import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { WorkflowBuilder } from './WorkflowBuilder';
import type { Workflow } from '../../domain/workflow';
import type { WorkflowRegistryMount } from './mimirRegistry';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeWorkflow(): Workflow {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    name: 'Test Workflow',
    nodes: [
      {
        id: 'stage-1',
        kind: 'stage',
        label: 'Stage 1',
        raidId: null,
        personaIds: [],
        position: { x: 100, y: 100 },
      },
      {
        id: 'gate-1',
        kind: 'gate',
        label: 'Gate',
        condition: 'ok',
        position: { x: 300, y: 100 },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'stage-1',
        target: 'gate-1',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ],
  };
}

function makeResourceWorkflow(): Workflow {
  return {
    id: '00000000-0000-0000-0000-000000000099',
    name: 'Knowledge Workflow',
    nodes: [
      {
        id: 'resource-1',
        kind: 'resource',
        label: 'Shared Mimir',
        resourceType: 'mimir',
        bindingMode: 'registry',
        registryEntryId: 'shared-mimir',
        categories: ['decision'],
        position: { x: 120, y: 160 },
      },
    ],
    edges: [],
    resourceBindings: [
      {
        id: 'binding-1',
        resourceNodeId: 'resource-1',
        targetType: 'workflow',
        targetId: '00000000-0000-0000-0000-000000000099',
        access: 'read',
        writePrefixes: [],
        readPriority: 5,
      },
    ],
  };
}

const REGISTRY_MOUNT: WorkflowRegistryMount = {
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
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WorkflowBuilder', () => {
  it('renders the workflow-builder container', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByTestId('workflow-builder')).toBeInTheDocument();
  });

  it('displays the workflow name in the header', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByTestId('builder-title')).toHaveTextContent('Test Workflow');
  });

  it('renders all three view tabs', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByRole('button', { name: 'Graph' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'YAML' })).toBeInTheDocument();
  });

  it('defaults to graph view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  });

  it('switches to pipeline view when tab clicked', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Pipeline' }));
    expect(screen.getByTestId('pipeline-view')).toBeInTheDocument();
    expect(screen.queryByTestId('graph-view')).toBeNull();
  });

  it('switches to yaml view when tab clicked', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }));
    expect(screen.getByTestId('yaml-view')).toBeInTheDocument();
    expect(screen.queryByTestId('graph-view')).toBeNull();
  });

  it('switches back to graph from yaml', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }));
    fireEvent.click(screen.getByRole('button', { name: 'Graph' }));
    expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  });

  it('does not render save button when onSave is not provided', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.queryByTestId('save-workflow')).toBeNull();
  });

  it('renders save button when onSave is provided', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onSave={vi.fn()} />);
    expect(screen.getByTestId('save-workflow')).toBeInTheDocument();
  });

  it('calls onSave with current workflow when save button clicked', () => {
    const onSave = vi.fn();
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onSave={onSave} />);
    fireEvent.click(screen.getByTestId('save-workflow'));
    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = onSave.mock.calls[0]![0] as Workflow;
    expect(saved.id).toBe('00000000-0000-0000-0000-000000000001');
  });

  it('always renders the ValidationPanel', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByTestId('validation-panel')).toBeInTheDocument();
  });

  it('shows library panel in graph view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} registryMounts={[REGISTRY_MOUNT]} />);
    expect(screen.getByTestId('library-panel')).toBeInTheDocument();
    expect(screen.getByTestId('mimir-mount-shared-mimir')).toBeInTheDocument();
  });

  it('shows the resource inspector when a Mimir resource node is selected', () => {
    render(
      <WorkflowBuilder initialWorkflow={makeResourceWorkflow()} registryMounts={[REGISTRY_MOUNT]} />,
    );
    fireEvent.mouseDown(screen.getByTestId('workflow-node-resource-1'));
    expect(screen.getByText('Registry mount')).toBeInTheDocument();
    expect(screen.getByText('Bindings')).toBeInTheDocument();
  });

  it('shows the explicit ephemeral local guidance for ephemeral Mimir resources', () => {
    render(
      <WorkflowBuilder
        initialWorkflow={{
          ...makeResourceWorkflow(),
          nodes: [
            {
              ...makeResourceWorkflow().nodes[0]!,
              bindingMode: 'ephemeral_local',
              registryEntryId: null,
              role: 'local',
            },
          ],
        }}
        registryMounts={[REGISTRY_MOUNT]}
      />,
    );
    fireEvent.mouseDown(screen.getByTestId('workflow-node-resource-1'));
    expect(screen.getByText('Ephemeral local Mimir')).toBeInTheDocument();
    expect(screen.getByText(/path is fixed by the runtime/i)).toBeInTheDocument();
  });

  it('hides library panel in pipeline view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Pipeline' }));
    expect(screen.queryByTestId('library-panel')).toBeNull();
  });

  it('adds a new stage node when add-stage clicked in graph view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    const countBefore = screen.getAllByTestId(/^workflow-node-/).length;
    fireEvent.click(screen.getByTestId('add-stage'));
    expect(screen.getAllByTestId(/^workflow-node-/)).toHaveLength(countBefore + 1);
  });

  it('tab-pipeline is active after switching to pipeline', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Pipeline' }));
    expect(screen.getByRole('button', { name: 'Pipeline' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'false');
  });
});

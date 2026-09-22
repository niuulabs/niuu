import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
        runId: null,
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

  it('exposes only implemented editor actions', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.queryByTestId('btn-diff')).not.toBeInTheDocument();
    expect(screen.queryByTestId('btn-test')).not.toBeInTheDocument();
    expect(screen.queryByTestId('btn-dispatch')).not.toBeInTheDocument();
    expect(screen.queryByText('History')).not.toBeInTheDocument();
  });

  it('offers all three visualizations in one compact control', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    const visualization = screen.getByRole('combobox', { name: 'Visualization' });
    expect(visualization).toHaveValue('graph');
    expect(screen.getByRole('option', { name: 'Graph' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'YAML' })).toBeInTheDocument();
  });

  it('defaults to graph view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByTestId('workflow-visualization')).toHaveValue('graph');
    expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  });

  it('switches to pipeline view when selected', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'pipeline' },
    });
    expect(screen.getByTestId('pipeline-view')).toBeInTheDocument();
    expect(screen.queryByTestId('graph-view')).toBeNull();
  });

  it('switches to yaml view when selected', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'yaml' },
    });
    expect(screen.getByTestId('yaml-view')).toBeInTheDocument();
    expect(screen.queryByTestId('graph-view')).toBeNull();
  });

  it('switches back to graph from yaml', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'yaml' },
    });
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'graph' },
    });
    expect(screen.getByTestId('graph-view')).toBeInTheDocument();
  });

  it('does not render save button when onSave is not provided', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.queryByTestId('save-workflow')).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Floating detail panel
  // -------------------------------------------------------------------------

  it('does not render the detail panel until something is selected', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.queryByTestId('workflow-detail-panel')).not.toBeInTheDocument();
  });

  it('opens the detail panel with the node inspector when a node is selected', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'));
    expect(screen.getByTestId('workflow-detail-panel')).toHaveTextContent('Stage 1');
    expect(screen.getByTestId('workflow-detail-panel')).toHaveTextContent('stage · stage-1');
  });

  it('opens the workflow summary in the detail panel when the header title is clicked, and toggles it closed on a second click', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByTestId('open-workflow-meta'));
    expect(screen.getByTestId('workflow-detail-panel')).toHaveTextContent(
      'Inspector and release summary',
    );

    fireEvent.click(screen.getByTestId('open-workflow-meta'));
    expect(screen.queryByTestId('workflow-detail-panel')).not.toBeInTheDocument();
  });

  it('switches the panel from workflow summary to a node inspector when a node is selected', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.click(screen.getByTestId('open-workflow-meta'));
    fireEvent.mouseDown(screen.getByTestId('workflow-node-gate-1'));
    expect(screen.getByTestId('workflow-detail-panel')).toHaveTextContent('gate · gate-1');
  });

  it('closes the panel and deselects the node via the close button', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'));
    expect(screen.getByTestId('workflow-node-stage-1')).toHaveAttribute('data-selected', 'true');

    fireEvent.click(screen.getByTestId('workflow-detail-panel-close'));
    expect(screen.queryByTestId('workflow-detail-panel')).not.toBeInTheDocument();
    expect(screen.getByTestId('workflow-node-stage-1')).not.toHaveAttribute('data-selected');
  });

  it('renders save button when onSave is provided', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onSave={vi.fn()} />);
    expect(screen.getByTestId('save-workflow')).toBeInTheDocument();
  });

  it('renders launch button when onLaunch is provided', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onLaunch={vi.fn()} />);
    expect(screen.getByTestId('launch-workflow')).toBeInTheDocument();
  });

  it('switches immutable versions from view mode and exposes editing explicitly', () => {
    const onSelectVersion = vi.fn();
    const onEdit = vi.fn();
    const workflow = {
      ...makeWorkflow(),
      version: '1.1.0',
      documentRevision: 'sha256:11',
      canEdit: true,
    };
    render(
      <WorkflowBuilder
        initialWorkflow={workflow}
        mode="view"
        onEdit={onEdit}
        versions={[
          {
            version: '1.1.0',
            documentRevision: 'sha256:11',
            createdAt: '2026-09-20T12:00:00Z',
            isHead: true,
          },
          {
            version: '1.0.0',
            documentRevision: 'sha256:10',
            createdAt: '2026-09-19T12:00:00Z',
            isHead: false,
          },
        ]}
        onSelectVersion={onSelectVersion}
      />,
    );

    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');
    fireEvent.change(screen.getByTestId('workflow-version-picker'), {
      target: { value: '1.0.0' },
    });
    fireEvent.click(screen.getByTestId('edit-workflow'));

    expect(onSelectVersion).toHaveBeenCalledWith('1.0.0');
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: workflow.id }));
  });

  it('offers same-identity editing and export actions for permitted bundled workflows', () => {
    const onEdit = vi.fn();
    const onExport = vi.fn();
    const workflow = {
      ...makeWorkflow(),
      readOnly: true,
      origin: 'bundled' as const,
      canEdit: true,
    };
    render(
      <WorkflowBuilder
        initialWorkflow={workflow}
        mode="view"
        onEdit={onEdit}
        onExport={onExport}
      />,
    );

    fireEvent.click(screen.getByTestId('workflow-catalog-status'));
    expect(screen.getByText(/edit creates a new version/i)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('edit-workflow'));
    fireEvent.click(screen.getByTestId('export-workflow-yaml'));
    fireEvent.click(screen.getByTestId('export-workflow-bundle'));

    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: workflow.id }));
    expect(onExport).toHaveBeenNthCalledWith(1, 'yaml');
    expect(onExport).toHaveBeenNthCalledWith(2, 'bundle');
  });

  it('shows pinned persona revisions and blocks launch while dependencies are unresolved', () => {
    const onLaunch = vi.fn();
    render(
      <WorkflowBuilder
        initialWorkflow={{
          ...makeWorkflow(),
          personaDependencies: {
            reviewer: {
              id: 'persona-reviewer',
              revision: '9',
              digest: 'sha256:abc',
              resolved: true,
            },
            coder: {
              id: 'persona-coder',
              revision: '3',
              digest: 'sha256:def',
              resolved: false,
              message: 'Install or map this revision',
            },
          },
          requirements: [
            { id: 'mimir-1', kind: 'mimir', message: 'Choose a local Mimir.', resolved: false },
          ],
        }}
        onLaunch={onLaunch}
      />,
    );

    expect(screen.getByTestId('persona-dependency-reviewer')).toHaveTextContent(
      'persona-reviewer@9',
    );
    expect(screen.getByTestId('unresolved-persona-coder')).toHaveTextContent(
      'Install or map this revision',
    );
    expect(screen.getByTestId('unresolved-requirement-mimir-1')).toHaveTextContent(
      'Choose a local Mimir.',
    );
    expect(screen.getByTestId('launch-workflow')).toBeDisabled();
  });

  it('calls onSave with current workflow when save button clicked', () => {
    const onSave = vi.fn();
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onSave={onSave} />);
    fireEvent.click(screen.getByTestId('open-add-step'));
    fireEvent.click(screen.getByTestId('library-add-stage'));
    fireEvent.click(screen.getByTestId('save-workflow'));
    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = onSave.mock.calls[0]![0] as Workflow;
    expect(saved.id).toBe('00000000-0000-0000-0000-000000000001');
  });

  it('shows a dirty state and clears it only after save completes', async () => {
    let finishSave: (() => void) | undefined;
    const onSave = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishSave = resolve;
        }),
    );
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onSave={onSave} />);
    expect(screen.getByTestId('save-workflow')).toBeDisabled();

    fireEvent.click(screen.getByTestId('open-add-step'));
    fireEvent.click(screen.getByTestId('library-add-stage'));
    expect(screen.getByTestId('workflow-dirty-state')).toBeInTheDocument();
    expect(screen.getByTestId('save-workflow')).toBeEnabled();

    fireEvent.click(screen.getByTestId('save-workflow'));
    expect(onSave).toHaveBeenCalledTimes(1);
    finishSave?.();
    await waitFor(() => expect(screen.queryByTestId('workflow-dirty-state')).toBeNull());
  });

  it('shows save failures without producing an unhandled action', async () => {
    render(
      <WorkflowBuilder
        initialWorkflow={makeWorkflow()}
        onSave={vi.fn().mockRejectedValue(new Error('version conflict'))}
      />,
    );
    fireEvent.click(screen.getByTestId('open-add-step'));
    fireEvent.click(screen.getByTestId('library-add-stage'));
    fireEvent.click(screen.getByTestId('save-workflow'));

    expect(await screen.findByRole('alert')).toHaveTextContent('version conflict');
    expect(screen.getByTestId('workflow-dirty-state')).toBeInTheDocument();
  });

  it('does not expose delete when the workflow cannot be edited', () => {
    render(
      <WorkflowBuilder
        initialWorkflow={{
          ...makeWorkflow(),
          origin: 'authored',
          canEdit: false,
        }}
        mode="view"
        onDeleteWorkflow={vi.fn()}
      />,
    );

    expect(
      screen.queryByTestId('delete-workflow-00000000-0000-0000-0000-000000000001'),
    ).not.toBeInTheDocument();
  });

  it('protects a dirty draft when switching workflows', () => {
    const next = { ...makeWorkflow(), id: '00000000-0000-0000-0000-000000000002', name: 'Next' };
    const onSelectWorkflow = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(
      <WorkflowBuilder
        initialWorkflow={makeWorkflow()}
        workflowCatalog={[makeWorkflow(), next]}
        onSelectWorkflow={onSelectWorkflow}
      />,
    );
    fireEvent.click(screen.getByTestId('open-add-step'));
    fireEvent.click(screen.getByTestId('library-add-stage'));
    fireEvent.click(screen.getByTestId('workflow-picker-trigger'));
    fireEvent.click(screen.getByTestId(`workflow-tab-${next.id}`));
    expect(onSelectWorkflow).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('renders readonly ancestry navigation and reports the exact current location', () => {
    const rootWorkflow = {
      ...makeWorkflow(),
      id: '00000000-0000-0000-0000-000000000004',
      name: 'Root workflow',
      version: '2.0.0',
      documentRevision: 'sha256:root-20',
    };
    const savedParent = {
      ...makeWorkflow(),
      name: 'Historical parent',
      version: '0.9.0',
      documentRevision: 'sha256:parent-09',
    };
    const draftParent = { ...savedParent, description: 'Unsaved parent draft' };
    const child = {
      ...makeWorkflow(),
      id: '00000000-0000-0000-0000-000000000003',
      name: 'Child workflow',
      version: '1.2.0',
      documentRevision: 'sha256:child-12',
      canEdit: true,
    };
    const onNavigateAncestor = vi.fn();

    render(
      <WorkflowBuilder
        initialWorkflow={child}
        initialSavedWorkflow={child}
        mode="view"
        ancestry={[
          { workflow: rootWorkflow, savedWorkflow: rootWorkflow, mode: 'view' },
          { workflow: draftParent, savedWorkflow: savedParent, mode: 'edit' },
        ]}
        onNavigateAncestor={onNavigateAncestor}
      />,
    );

    expect(screen.getByRole('navigation', { name: 'Workflow path' })).toBeInTheDocument();
    expect(screen.getByTestId('workflow-parent-0')).toHaveAccessibleName('Root workflow');
    expect(screen.getByTestId('workflow-parent-1')).toHaveAccessibleName('Historical parent');
    expect(screen.getByTestId('workflow-parent-back')).toHaveAccessibleName(
      'Back to Historical parent',
    );
    expect(screen.getByTestId('workflow-parent-back')).toBeEnabled();
    expect(screen.queryByTestId('btn-undo')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('workflow-parent-0'));
    expect(onNavigateAncestor).toHaveBeenCalledWith(
      0,
      expect.objectContaining({
        workflow: expect.objectContaining({ id: child.id }),
        mode: 'view',
      }),
    );

    fireEvent.click(screen.getByTestId('workflow-parent-back'));
    expect(onNavigateAncestor).toHaveBeenLastCalledWith(
      1,
      expect.objectContaining({
        workflow: expect.objectContaining({ id: child.id }),
        mode: 'view',
      }),
    );
  });

  it('calls onLaunch with current workflow when launch button clicked', () => {
    const onLaunch = vi.fn();
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} onLaunch={onLaunch} />);
    fireEvent.click(screen.getByTestId('launch-workflow'));
    expect(onLaunch).toHaveBeenCalledTimes(1);
    const launched = onLaunch.mock.calls[0]![0] as Workflow;
    expect(launched.id).toBe('00000000-0000-0000-0000-000000000001');
  });

  it('always renders the ValidationPanel', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByTestId('validation-panel')).toBeInTheDocument();
  });

  it('keeps the library palette closed until opened — a command palette, not a permanent sidebar', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} registryMounts={[REGISTRY_MOUNT]} />);
    expect(screen.queryByTestId('library-panel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('open-add-step'));
    expect(screen.getByTestId('library-panel')).toBeInTheDocument();
    expect(screen.getByTestId('mimir-mount-shared-mimir')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('library-panel-close'));
    expect(screen.queryByTestId('library-panel')).not.toBeInTheDocument();
  });

  it('does not claim the app-wide ⌘K shortcut', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    expect(screen.queryByTestId('library-panel')).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Undo / redo
  // -------------------------------------------------------------------------

  it('starts with undo and redo disabled', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    expect(screen.getByTestId('btn-undo')).toBeDisabled();
    expect(screen.getByTestId('btn-redo')).toBeDisabled();
  });

  it('undoes and redoes a node move via the toolbar buttons', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'), {
      clientX: 100,
      clientY: 100,
    });
    fireEvent.mouseMove(screen.getByTestId('workflow-node-stage-1'), {
      clientX: 150,
      clientY: 140,
    });
    fireEvent.mouseUp(screen.getByTestId('workflow-node-stage-1'));
    expect(screen.getByTestId('btn-undo')).toBeEnabled();

    fireEvent.click(screen.getByTestId('btn-undo'));
    expect(screen.getByTestId('btn-undo')).toBeDisabled();
    expect(screen.getByTestId('btn-redo')).toBeEnabled();

    fireEvent.click(screen.getByTestId('btn-redo'));
    expect(screen.getByTestId('btn-undo')).toBeEnabled();
    expect(screen.getByTestId('btn-redo')).toBeDisabled();
  });

  it('leaves ⌘Z to the focused surface instead of installing a window shortcut', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.mouseDown(screen.getByTestId('workflow-node-stage-1'), {
      clientX: 100,
      clientY: 100,
    });
    fireEvent.mouseMove(screen.getByTestId('workflow-node-stage-1'), {
      clientX: 150,
      clientY: 140,
    });
    fireEvent.mouseUp(screen.getByTestId('workflow-node-stage-1'));

    fireEvent.keyDown(window, { key: 'z', metaKey: true });
    expect(screen.getByTestId('btn-undo')).toBeEnabled();
  });

  it('does not show the library toggle outside graph view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'pipeline' },
    });
    expect(screen.queryByTestId('open-add-step')).not.toBeInTheDocument();
  });

  it('shows the resource inspector when a Mimir resource node is selected', () => {
    render(
      <WorkflowBuilder
        initialWorkflow={makeResourceWorkflow()}
        registryMounts={[REGISTRY_MOUNT]}
      />,
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
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'pipeline' },
    });
    expect(screen.queryByTestId('library-panel')).toBeNull();
  });

  it('adds a new stage node when add-stage clicked in graph view', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    const countBefore = screen.getAllByTestId(/^workflow-node-/).length;
    fireEvent.click(screen.getByTestId('open-add-step'));
    fireEvent.click(screen.getByTestId('library-add-stage'));
    expect(screen.getAllByTestId(/^workflow-node-/)).toHaveLength(countBefore + 1);
  });

  it('shows pipeline as the selected visualization after switching', () => {
    render(<WorkflowBuilder initialWorkflow={makeWorkflow()} />);
    fireEvent.change(screen.getByTestId('workflow-visualization'), {
      target: { value: 'pipeline' },
    });
    expect(screen.getByTestId('workflow-visualization')).toHaveValue('pipeline');
  });
});

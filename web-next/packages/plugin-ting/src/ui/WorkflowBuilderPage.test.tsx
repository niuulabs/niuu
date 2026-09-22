import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';
import type { Workflow } from '../domain/workflow';

const mockSearch = vi.hoisted(() => ({ current: {} as { id?: string } }));

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => mockSearch.current,
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const wf1: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Auth Rewrite',
  nodes: [
    {
      id: 'stage-1',
      kind: 'stage',
      label: 'Setup',
      runId: null,
      personaIds: [],
      position: { x: 100, y: 100 },
    },
  ],
  edges: [],
};

const wf2: Workflow = {
  id: '00000000-0000-0000-0000-000000000002',
  name: 'Plugin Ravn',
  nodes: [
    {
      id: 'stage-a',
      kind: 'stage',
      label: 'Init',
      runId: null,
      personaIds: [],
      position: { x: 100, y: 100 },
    },
  ],
  edges: [],
};

function wrap(service: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const personaService = {
    listPersonas: vi.fn().mockResolvedValue([]),
    getPersonaYaml: vi.fn().mockResolvedValue(''),
  };
  const mimirService = {
    mounts: {
      listRegistryMounts: vi.fn().mockResolvedValue([]),
    },
  };
  const volundrService = {
    getModels: vi.fn().mockResolvedValue({}),
  };
  const repoCatalog = {
    getRepos: vi.fn().mockResolvedValue([]),
  };
  const workflowService = {
    listWorkflows: vi.fn().mockResolvedValue([]),
    listWorkflowVersions: vi.fn().mockResolvedValue([]),
    getWorkflowVersion: vi.fn().mockResolvedValue(null),
    ...(service['ting.workflows'] as Record<string, unknown> | undefined),
  };
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            bifrost: createMockBifrostService(),
            'ravn.personas': personaService,
            mimir: mimirService,
            volundr: volundrService,
            'niuu.repos': repoCatalog,
            ...service,
            'ting.workflows': workflowService,
          }}
        >
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WorkflowBuilderPage', () => {
  beforeEach(() => {
    mockSearch.current = {};
  });

  it('opens the workflow named in ?id=', async () => {
    mockSearch.current = { id: wf2.id };
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('builder-title')).toHaveTextContent(wf2.name));
    expect(screen.getByTestId(`delete-workflow-${wf2.id}`)).toBeInTheDocument();
  });

  it('renders the workflow-builder-page container', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    expect(screen.getByTestId('workflow-builder-page')).toBeInTheDocument();
  });

  it('shows loading state initially', () => {
    const svc = { listWorkflows: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('renders workflow tabs after loading', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('workflow-picker-trigger')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('workflow-picker-trigger'));
    expect(screen.getByTestId(`workflow-tab-${wf1.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`workflow-tab-${wf2.id}`)).toBeInTheDocument();
  });

  it('shows the first workflow by default', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('workflow-builder')).toBeInTheDocument());
    // The workflow name appears in both the tab and the builder header
    const nameEls = screen.getAllByText('Auth Rewrite');
    expect(nameEls.length).toBeGreaterThanOrEqual(1);
  });

  it('views and edits one identity while loading exact historical versions', async () => {
    const head: Workflow = {
      ...wf1,
      version: '1.1.0',
      revision: 'head-cas-2',
      documentRevision: 'sha256:11',
      isHead: true,
      canEdit: true,
    };
    const historical: Workflow = {
      ...head,
      version: '1.0.0',
      documentRevision: 'sha256:10',
      isHead: false,
    };
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([head]),
      listWorkflowVersions: vi.fn().mockResolvedValue([
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
      ]),
      getWorkflowVersion: vi.fn().mockResolvedValue(historical),
    };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });

    await waitFor(() => expect(screen.getByTestId('workflow-version-picker')).toBeInTheDocument());
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');
    fireEvent.click(screen.getByTestId('edit-workflow'));
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Editing');
    fireEvent.click(screen.getByTestId('cancel-workflow-edit'));
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');

    fireEvent.change(screen.getByTestId('workflow-version-picker'), {
      target: { value: '1.0.0' },
    });
    await waitFor(() => expect(svc.getWorkflowVersion).toHaveBeenCalledWith(head.id, '1.0.0'));
    await waitFor(() => expect(screen.getByTestId('workflow-version-picker')).toHaveValue('1.0.0'));
  });

  it('blocks fast Edit until the selected historical document has loaded', async () => {
    const head: Workflow = {
      ...wf1,
      version: '1.1.0',
      revision: 'head-cas-2',
      documentRevision: 'sha256:11',
      isHead: true,
      canEdit: true,
    };
    const historical: Workflow = {
      ...head,
      version: '1.0.0',
      documentRevision: 'sha256:10',
      isHead: false,
    };
    let releaseVersion!: (workflow: Workflow) => void;
    const pendingVersion = new Promise<Workflow>((resolve) => {
      releaseVersion = resolve;
    });
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([head]),
      listWorkflowVersions: vi.fn().mockResolvedValue([
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
      ]),
      getWorkflowVersion: vi.fn().mockReturnValue(pendingVersion),
    };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });

    await waitFor(() => expect(screen.getByTestId('workflow-version-picker')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('workflow-version-picker'), {
      target: { value: '1.0.0' },
    });

    await waitFor(() => expect(screen.getByTestId('edit-workflow')).toBeDisabled());
    expect(screen.getByTestId('launch-workflow')).toBeDisabled();
    expect(screen.getByTestId('workflow-versions-loading')).toBeInTheDocument();

    releaseVersion(historical);
    await waitFor(() => expect(screen.getByTestId('workflow-version-picker')).toHaveValue('1.0.0'));
    expect(screen.getByTestId('edit-workflow')).toBeEnabled();
  });

  it('switches workflow when tab is clicked', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('workflow-picker-trigger')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('workflow-picker-trigger'));
    fireEvent.click(screen.getByTestId(`workflow-tab-${wf2.id}`));
    // The workflow name appears in both the tab and the builder header
    const nameEls = screen.getAllByText('Plugin Ravn');
    expect(nameEls.length).toBeGreaterThanOrEqual(1);
  });

  it('restores an exact editing parent draft with undo after exploring its child', async () => {
    const child: Workflow = {
      ...wf2,
      version: '1.2.0',
      documentRevision: 'sha256:child-12',
      canEdit: true,
    };
    const parent: Workflow = {
      ...wf1,
      name: 'Historical parent',
      version: '0.9.0',
      documentRevision: 'sha256:parent-09',
      isHead: false,
      canEdit: true,
      workflowDependencies: {
        child: {
          id: child.id,
          revision: child.documentRevision!,
          digest: child.documentRevision!,
        },
      },
      nodes: [
        {
          id: 'child-node',
          kind: 'subworkflow',
          label: 'Original child label',
          templates: { default: 'child' },
          allowedCoordinator: '',
          inputSchema: { type: 'object', properties: {} },
          resultSchema: { type: 'object', properties: {} },
          maxChildren: 10,
          maxAttempts: 3,
          maxActiveChildren: 4,
          joinMode: 'all',
          blockedEvent: 'children.blocked',
          position: { x: 100, y: 100 },
        },
      ],
    };
    const svc = { listWorkflows: vi.fn().mockResolvedValue([parent, child]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });

    await waitFor(() => expect(screen.getByTestId('builder-title')).toHaveTextContent(parent.name));
    fireEvent.click(screen.getByTestId('edit-workflow'));
    fireEvent.mouseDown(screen.getByTestId('workflow-node-child-node'));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft child label' } });
    expect(screen.getByTestId('workflow-dirty-state')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('open-child-workflow'));
    expect(screen.getByTestId('builder-title')).toHaveTextContent(child.name);
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');
    expect(screen.getByTestId('workflow-parent-0')).toHaveTextContent(parent.name);

    fireEvent.click(screen.getByTestId('workflow-parent-back'));
    expect(screen.getByTestId('builder-title')).toHaveTextContent(parent.name);
    expect(screen.getByTestId('builder-version')).toHaveTextContent('v0.9.0');
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Editing');
    expect(screen.getByTestId('workflow-dirty-state')).toBeInTheDocument();
    expect(screen.getByTestId('btn-undo')).toBeEnabled();

    fireEvent.mouseDown(screen.getByTestId('workflow-node-child-node'));
    expect(screen.getByLabelText('Name')).toHaveValue('Draft child label');
  });

  it('shows error state when service fails', async () => {
    const svc = { listWorkflows: vi.fn().mockRejectedValue(new Error('service down')) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByText('service down')).toBeInTheDocument());
  });

  it('shows no-workflows message when list is empty', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByText(/no workflows found/i)).toBeInTheDocument());
  });

  it('renders WorkflowBuilder when workflow is selected', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('workflow-builder')).toBeInTheDocument());
  });

  it('renders the new workflow button', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('workflow-picker-trigger')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('workflow-picker-trigger'));
    expect(screen.getByTestId('new-workflow')).toBeInTheDocument();
  });

  it('calls saveWorkflow when new workflow button is clicked', async () => {
    const newWf: Workflow = { id: 'new-id', name: 'New Workflow', nodes: [], edges: [] };
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      saveWorkflow: vi.fn().mockResolvedValue(newWf),
    };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('workflow-picker-trigger')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('workflow-picker-trigger'));
    fireEvent.click(screen.getByTestId('new-workflow'));
    await waitFor(() => expect(svc.saveWorkflow).toHaveBeenCalledTimes(1));
    const saved = svc.saveWorkflow.mock.calls[0]![0] as Workflow;
    expect(saved.name).toBe('New Workflow');
    expect(saved.nodes).toHaveLength(0);
    expect(saved.edges).toHaveLength(0);
  });

  it('shows delete button on the active workflow tab', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() =>
      expect(screen.getByTestId(`delete-workflow-${wf1.id}`)).toBeInTheDocument(),
    );
    // First workflow is active by default — its delete button should be visible
    expect(screen.getByTestId(`delete-workflow-${wf1.id}`)).toBeInTheDocument();
    // Second workflow is not active — no delete button
    expect(screen.queryByTestId(`delete-workflow-${wf2.id}`)).not.toBeInTheDocument();
  });

  it('calls deleteWorkflow when delete button is clicked', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      deleteWorkflow: vi.fn().mockResolvedValue(undefined),
    };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() =>
      expect(screen.getByTestId(`delete-workflow-${wf1.id}`)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId(`delete-workflow-${wf1.id}`));
    await waitFor(() => expect(svc.deleteWorkflow).toHaveBeenCalledWith(wf1.id));
  });

  it('opens the launch modal and submits a workflow launch', async () => {
    const assign = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { assign },
      writable: true,
    });

    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      launchWorkflow: vi.fn().mockResolvedValue({
        workflowId: wf1.id,
        workflowName: wf1.name,
        slug: 'test-workflow',
        sessionId: 'sess-123',
        sessionName: 'test-workflow',
        status: 'starting',
        clusterName: 'valhalla',
      }),
    };

    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId('launch-workflow')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('launch-workflow'));
    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Run this workflow on a fresh prompt.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    await waitFor(() =>
      expect(svc.launchWorkflow).toHaveBeenCalledWith(
        wf1.id,
        expect.objectContaining({ prompt: 'Run this workflow on a fresh prompt.' }),
      ),
    );
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/volundr/sessions/sess-123'));
  });
});

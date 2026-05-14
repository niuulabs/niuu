import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { WorkflowBuilderPage } from './WorkflowBuilderPage';
import type { Workflow } from '../domain/workflow';

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
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            bifrost: createMockBifrostService(),
            'ravn.personas': personaService,
            mimir: mimirService,
            volundr: volundrService,
            ...service,
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
    await waitFor(() => expect(screen.getByTestId(`workflow-tab-${wf1.id}`)).toBeInTheDocument());
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

  it('switches workflow when tab is clicked', async () => {
    const svc = { listWorkflows: vi.fn().mockResolvedValue([wf1, wf2]) };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
    await waitFor(() => expect(screen.getByTestId(`workflow-tab-${wf2.id}`)).toBeInTheDocument());
    fireEvent.click(screen.getByTestId(`workflow-tab-${wf2.id}`));
    // The workflow name appears in both the tab and the builder header
    const nameEls = screen.getAllByText('Plugin Ravn');
    expect(nameEls.length).toBeGreaterThanOrEqual(1);
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
    expect(screen.getByTestId('new-workflow')).toBeInTheDocument();
  });

  it('calls saveWorkflow when new workflow button is clicked', async () => {
    const newWf: Workflow = { id: 'new-id', name: 'New Workflow', nodes: [], edges: [] };
    const svc = {
      listWorkflows: vi.fn().mockResolvedValue([wf1]),
      saveWorkflow: vi.fn().mockResolvedValue(newWf),
    };
    render(<WorkflowBuilderPage />, { wrapper: wrap({ 'ting.workflows': svc }) });
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
    await waitFor(() => expect(screen.getByTestId(`workflow-tab-${wf1.id}`)).toBeInTheDocument());
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
});

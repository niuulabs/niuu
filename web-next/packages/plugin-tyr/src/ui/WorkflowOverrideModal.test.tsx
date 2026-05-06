import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { WorkflowOverrideModal } from './WorkflowOverrideModal';
import { createMockWorkflowService } from '../adapters/mock';
import type { IWorkflowService } from '../ports';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: '00000000-0000-0000-0000-000000000a01',
    name: 'Ship Workflow',
    nodes: [
      {
        id: 'stage-1',
        kind: 'stage',
        label: 'Stage 1',
        raidId: null,
        personaIds: [],
        position: { x: 0, y: 0 },
      },
      {
        id: 'stage-2',
        kind: 'stage',
        label: 'Stage 2',
        raidId: null,
        personaIds: [],
        position: { x: 100, y: 0 },
      },
      {
        id: 'gate-1',
        kind: 'gate',
        label: 'QA Gate',
        condition: 'QA approved',
        position: { x: 200, y: 0 },
      },
    ],
    edges: [],
    ...overrides,
  };
}

function wrap(workflows: Workflow[] | null = null, isError = false) {
  const workflowService: IWorkflowService = isError
    ? {
        listWorkflows: async () => {
          throw new Error('service error');
        },
        getWorkflow: async () => null,
        saveWorkflow: async (w) => w,
        deleteWorkflow: async () => {},
      }
    : workflows !== null
      ? {
          ...createMockWorkflowService(),
          listWorkflows: async () => workflows,
        }
      : createMockWorkflowService();

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'tyr.workflows': workflowService }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WorkflowOverrideModal', () => {
  it('renders nothing when closed', () => {
    const onApply = vi.fn();
    render(
      <WorkflowOverrideModal
        open={false}
        onOpenChange={vi.fn()}
        selectedCount={2}
        onApply={onApply}
      />,
      { wrapper: wrap() },
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders dialog when open', async () => {
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={2}
        onApply={vi.fn()}
      />,
      { wrapper: wrap() },
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Apply workflow override')).toBeInTheDocument();
  });

  it('shows description with selected count', () => {
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={3}
        onApply={vi.fn()}
      />,
      { wrapper: wrap() },
    );
    expect(screen.getByText(/3 selected raids/i)).toBeInTheDocument();
  });

  it('shows singular "raid" for count=1', () => {
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: wrap([makeWorkflow()]) },
    );
    expect(screen.getByText(/1 selected raid[^s]/i)).toBeInTheDocument();
  });

  it('shows loading state', () => {
    // Use a never-resolving service to keep loading state
    const neverService: IWorkflowService = {
      listWorkflows: () => new Promise(() => {}),
      getWorkflow: async () => null,
      saveWorkflow: async (w) => w,
      deleteWorkflow: async () => {},
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'tyr.workflows': neverService }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );

    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: Wrapper },
    );
    expect(screen.getByText(/loading workflows/i)).toBeInTheDocument();
  });

  it('shows error state when service fails', async () => {
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: wrap(null, true) },
    );
    await waitFor(() => expect(screen.getByText(/failed to load workflows/i)).toBeInTheDocument());
  });

  it('shows empty state when no workflows', async () => {
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: wrap([]) },
    );
    await waitFor(() => expect(screen.getByText(/no workflows available/i)).toBeInTheDocument());
  });

  it('renders workflow list with stage counts', async () => {
    const wf = makeWorkflow({ name: 'My Workflow' });
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: wrap([wf]) },
    );
    await waitFor(() => expect(screen.getByText('My Workflow')).toBeInTheDocument());
    // 2 stage nodes in makeWorkflow
    expect(screen.getByText('2 stages')).toBeInTheDocument();
  });

  it('shows "1 stage" singular when workflow has one stage', async () => {
    const wf = makeWorkflow({
      name: 'Single Stage',
      nodes: [
        {
          id: 's1',
          kind: 'stage',
          label: 'Stage',
          raidId: null,
          personaIds: [],
          position: { x: 0, y: 0 },
        },
      ],
    });
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={vi.fn()}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: wrap([wf]) },
    );
    await waitFor(() => expect(screen.getByText('1 stage')).toBeInTheDocument());
  });

  it('calls onApply and closes when workflow selected', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const onOpenChange = vi.fn();
    const wf = makeWorkflow({ name: 'Ship It' });
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={onOpenChange}
        selectedCount={2}
        onApply={onApply}
      />,
      { wrapper: wrap([wf]) },
    );
    await waitFor(() => screen.getByText('Ship It'));
    await user.click(screen.getByText('Ship It'));
    expect(onApply).toHaveBeenCalledWith(wf);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('closes when Cancel is clicked', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <WorkflowOverrideModal
        open={true}
        onOpenChange={onOpenChange}
        selectedCount={1}
        onApply={vi.fn()}
      />,
      { wrapper: wrap([makeWorkflow()]) },
    );
    await waitFor(() => screen.getByText('Cancel'));
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

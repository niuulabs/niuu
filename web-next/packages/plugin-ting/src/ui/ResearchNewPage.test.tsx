import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { RepoRecord } from '@niuulabs/ui';
import { describe, expect, it, vi } from 'vitest';
import type { CreateResearchCampaignRequest, IResearchService, IWorkflowService } from '../ports';
import { createMockDispatchBus, createMockResearchService } from '../adapters/mock';
import type { Workflow } from '../domain/workflow';
import { ResearchNewPage } from './ResearchNewPage';

const mockNavigate = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}));

const repoCatalog = {
  async getRepos(): Promise<RepoRecord[]> {
    return [
      {
        name: 'niuu',
        owner: 'niuulabs',
        cloneUrl: 'https://github.com/niuulabs/niuu.git',
        defaultBranch: 'dev',
      },
    ];
  },
};

const workflowService: IWorkflowService = {
  async listWorkflows(): Promise<Workflow[]> {
    return [
      {
        id: '00000000-0000-4000-8000-000000000001',
        name: 'Research Campaign',
        version: '1.0.0',
        description: 'Structured research workflow',
        tags: ['research'],
        nodes: [],
        edges: [],
        resourceBindings: [],
      },
      {
        id: '00000000-0000-4000-8000-000000000002',
        name: 'Incident triage',
        version: '0.3.0',
        description: 'Operational workflow',
        tags: ['ops'],
        nodes: [],
        edges: [],
        resourceBindings: [],
      },
    ];
  },
  async getWorkflow() {
    return null;
  },
  async saveWorkflow(workflow) {
    return workflow;
  },
  async deleteWorkflow() {},
  async launchWorkflow() {
    return { sessionId: 'session', sessionName: 'session' };
  },
};

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('ResearchNewPage', () => {
  it('shows tagged workflows and launches a campaign', async () => {
    mockNavigate.mockClear();
    render(<ResearchNewPage />, {
      wrapper: wrap({
        'ting.research': createMockResearchService(),
        'ting.workflows': workflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': repoCatalog,
      }),
    });

    await waitFor(() => expect(screen.getByText(/Research intake/i)).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByText(/Showing 1 workflow tagged research/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole('option', { name: /Research Campaign/i })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Incident triage/i })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText('Execution target')).toHaveValue('cluster-mini'),
    );
    expect(screen.getByRole('option', { name: /MacBook Pro/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /All workflows/i }));
    await waitFor(() => expect(screen.getByText(/Showing all 2 workflows/i)).toBeInTheDocument());
    expect(screen.getByRole('option', { name: /Incident triage/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Research-tagged/i }));
    await waitFor(() =>
      expect(screen.queryByRole('option', { name: /Incident triage/i })).not.toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'Should research use workflow tags?' },
    });
    fireEvent.change(screen.getByLabelText('Campaign title'), {
      target: { value: 'Workflow tag research' },
    });
    fireEvent.change(screen.getByLabelText('Mode'), { target: { value: 'monitoring' } });
    fireEvent.change(screen.getByLabelText('Audience'), { target: { value: 'Platform ops' } });
    fireEvent.change(screen.getByLabelText('Deliverable'), {
      target: { value: 'Decision memo + rollout plan' },
    });
    fireEvent.change(screen.getByLabelText('Success criteria'), {
      target: { value: 'Actionable path forward' },
    });
    fireEvent.change(screen.getByLabelText('Constraints'), {
      target: { value: 'Use current hardware\nNo new GPUs' },
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Launch campaign/i })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: /Launch campaign/i }));

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith({
        to: '/ting/research/$slug',
        params: { slug: 'workflow-tag-research' },
      }),
    );
  });

  it('falls back to manual repo and branch inputs when no repo catalog or research tags exist', async () => {
    mockNavigate.mockClear();
    const requests: CreateResearchCampaignRequest[] = [];
    const researchService: IResearchService = {
      async listCampaigns() {
        return [];
      },
      async getCampaign() {
        return null;
      },
      async createCampaign(request) {
        requests.push(request);
        return {
          id: 'campaign-2',
          slug: 'manual-fallback-campaign',
          name: request.question,
          ownerId: 'dev-user',
          workflowId: request.workflowId ?? 'workflow-ops',
          workflowVersion: '0.1.0',
          workflowName: 'Ops review',
          sessionId: 'session-2',
          sessionName: request.question,
          status: 'running',
          activeStageId: 'frame',
          stageState: [],
          metadata: {
            question: request.question,
            mode: request.mode ?? 'exploratory',
            audience: request.audience ?? '',
            deliverable: request.deliverable ?? '',
            success: request.success ?? '',
            constraints: request.constraints ?? [],
            repo: request.repo ?? '',
            branch: request.branch ?? '',
          },
          createdAt: '2026-05-25T12:00:00.000Z',
          updatedAt: '2026-05-25T12:00:00.000Z',
          lastActivityAt: '2026-05-25T12:00:00.000Z',
          completedAt: null,
          artifacts: [],
          canonicalArtifacts: {},
        };
      },
      async updateCampaign() {
        throw new Error('not needed');
      },
      async deleteCampaign() {},
      async getArtifact() {
        return null;
      },
    };
    const noTagWorkflowService: IWorkflowService = {
      async listWorkflows() {
        return [
          {
            id: 'workflow-ops',
            name: 'Ops review',
            version: '0.1.0',
            description: 'General operator workflow',
            tags: ['ops'],
            nodes: [],
            edges: [],
            resourceBindings: [],
          },
        ];
      },
      async getWorkflow() {
        return null;
      },
      async saveWorkflow(workflow) {
        return workflow;
      },
      async deleteWorkflow() {},
      async launchWorkflow() {
        return { sessionId: 'session-2', sessionName: 'session-2' };
      },
    };
    const emptyRepoCatalog = {
      async getRepos(): Promise<RepoRecord[]> {
        return [];
      },
    };

    render(<ResearchNewPage />, {
      wrapper: wrap({
        'ting.research': researchService,
        'ting.workflows': noTagWorkflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': emptyRepoCatalog,
      }),
    });

    await waitFor(() =>
      expect(
        screen.getByText(/No research-tagged workflows found, so all workflows are shown/i),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByRole('button', { name: /Research-tagged/i })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'Should we keep a generic workflow escape hatch?' },
    });
    fireEvent.change(screen.getByLabelText('Campaign title'), {
      target: { value: 'Generic workflow escape hatch' },
    });
    fireEvent.change(screen.getByLabelText('Repo'), {
      target: { value: 'https://github.com/niuulabs/niuu.git' },
    });
    fireEvent.change(screen.getByLabelText('Branch'), { target: { value: 'feature/research' } });
    await waitFor(() => expect(screen.getByLabelText('Execution target')).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText('Execution target'), {
      target: { value: 'cluster-macbook' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Launch campaign/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({
      name: 'Generic workflow escape hatch',
      workflowId: 'workflow-ops',
      repo: 'https://github.com/niuulabs/niuu.git',
      branch: 'feature/research',
      connectionId: 'cluster-macbook',
    });

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/research' });
  });

  it('falls back to the backend default workflow when the workflow catalog is unavailable', async () => {
    mockNavigate.mockClear();
    const requests: CreateResearchCampaignRequest[] = [];
    const researchService: IResearchService = {
      async listCampaigns() {
        return [];
      },
      async getCampaign() {
        return null;
      },
      async createCampaign(request) {
        requests.push(request);
        return {
          id: 'campaign-3',
          slug: 'default-workflow-campaign',
          name: request.question,
          ownerId: 'dev-user',
          workflowId: 'default-research-workflow',
          workflowVersion: '1.0.0',
          workflowName: 'Research Campaign',
          sessionId: 'session-3',
          sessionName: request.question,
          status: 'running',
          activeStageId: 'frame',
          stageState: [],
          metadata: { question: request.question },
          createdAt: '2026-05-25T12:00:00.000Z',
          updatedAt: '2026-05-25T12:00:00.000Z',
          lastActivityAt: '2026-05-25T12:00:00.000Z',
          completedAt: null,
          artifacts: [],
          canonicalArtifacts: {},
        };
      },
      async updateCampaign() {
        throw new Error('not needed');
      },
      async deleteCampaign() {},
      async getArtifact() {
        return null;
      },
    };
    const emptyWorkflowService: IWorkflowService = {
      async listWorkflows() {
        return [];
      },
      async getWorkflow() {
        return null;
      },
      async saveWorkflow(workflow) {
        return workflow;
      },
      async deleteWorkflow() {},
      async launchWorkflow() {
        return { sessionId: 'session-3', sessionName: 'session-3' };
      },
    };

    render(<ResearchNewPage />, {
      wrapper: wrap({
        'ting.research': researchService,
        'ting.workflows': emptyWorkflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': repoCatalog,
      }),
    });

    await waitFor(() =>
      expect(screen.getAllByText(/Default research workflow/i).length).toBeGreaterThan(1),
    );
    expect(screen.getByRole('combobox', { name: 'Workflow' })).toHaveValue('');

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'Should we allow workflowless research launches?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Launch campaign/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]?.workflowId).toBeUndefined();
  });

  it('updates the branch context when a repo is selected from the shared dropdown', async () => {
    const branchAwareRepos = {
      async getRepos(): Promise<RepoRecord[]> {
        return [
          {
            name: 'niuu',
            owner: 'niuulabs',
            cloneUrl: 'https://github.com/niuulabs/niuu.git',
            defaultBranch: 'release',
            branches: ['release', 'dev'],
          },
        ];
      },
    };

    render(<ResearchNewPage />, {
      wrapper: wrap({
        'ting.research': createMockResearchService(),
        'ting.workflows': workflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': branchAwareRepos,
      }),
    });

    await waitFor(() =>
      expect(screen.getByTestId('research-launch-repo-select')).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId('research-launch-repo-select'), {
      target: { value: 'https://github.com/niuulabs/niuu.git' },
    });

    await waitFor(() => expect(screen.getByDisplayValue('release')).toBeInTheDocument());
  });

  it('lets an operator pick a different workflow and clears branch when the repo is unknown', async () => {
    const mixedWorkflowService: IWorkflowService = {
      async listWorkflows() {
        return [
          {
            id: 'workflow-research-primary',
            name: 'Research Campaign',
            version: '1.0.0',
            description: 'Structured research workflow',
            tags: ['research'],
            nodes: [],
            edges: [],
            resourceBindings: [],
          },
          {
            id: 'workflow-research-fallback',
            name: 'Rapid scan',
            tags: ['research'],
            nodes: [],
            edges: [],
            resourceBindings: [],
          },
          {
            id: 'workflow-untagged',
            name: 'Ad hoc review',
            nodes: [],
            edges: [],
            resourceBindings: [],
          },
        ];
      },
      async getWorkflow() {
        return null;
      },
      async saveWorkflow(workflow) {
        return workflow;
      },
      async deleteWorkflow() {},
      async launchWorkflow() {
        return { sessionId: 'session-4', sessionName: 'session-4' };
      },
    };

    render(<ResearchNewPage />, {
      wrapper: wrap({
        'ting.research': createMockResearchService(),
        'ting.workflows': mixedWorkflowService,
        'ting.dispatch': createMockDispatchBus(),
        'niuu.repos': repoCatalog,
      }),
    });

    await waitFor(() =>
      expect(screen.getByText(/Showing 2 workflows tagged research/i)).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole('combobox', { name: 'Workflow' }), {
      target: { value: 'workflow-research-fallback' },
    });

    expect(screen.getAllByText('Rapid scan').length).toBeGreaterThan(1);
    expect(screen.queryByText('v1.0.0')).not.toBeInTheDocument();
    expect(screen.queryByText(/Structured research workflow/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /All workflows/i }));
    await waitFor(() => expect(screen.getByText(/Showing all 3 workflows/i)).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('research-launch-repo-select'), {
      target: { value: '' },
    });

    await waitFor(() =>
      expect(
        within(screen.getByLabelText('Branch').parentElement!).getByDisplayValue(''),
      ).toBeInTheDocument(),
    );
  });
});

import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { RepoRecord } from '@niuulabs/ui';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ResearchCampaign, SpecCampaign, TrackerIssue, TrackerProject } from '../ports';
import type { Workflow } from '../domain/workflow';
import { SimpleWorkflowsPage } from './SimpleWorkflowsPage';

const mockNavigate = vi.fn();
const mockSearch = vi.hoisted(() => ({
  current: {} as { workflow?: string; repo?: string; branch?: string },
}));
const mockOpenEventStream = vi.hoisted(() => vi.fn());

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => mockSearch.current,
  Link: ({
    to,
    children,
    search: _search,
    params: _params,
    ...rest
  }: {
    to: string;
    children: ReactNode;
    search?: unknown;
    params?: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('@niuulabs/query', () => ({
  openEventStream: mockOpenEventStream,
}));

const now = '2026-09-01T10:00:00.000Z';

const buildWorkflow: Workflow = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Build and ship',
  description: 'Plan it, build it, ship it.',
  scope: 'user',
  nodes: [
    {
      id: 'plan',
      kind: 'stage',
      label: 'Plan',
      runId: null,
      personaIds: ['planner'],
      position: { x: 0, y: 0 },
    },
    {
      id: 'plan-gate',
      kind: 'gate',
      label: 'the plan',
      condition: '',
      mode: 'human_approval',
      position: { x: 0, y: 0 },
    },
    {
      id: 'build',
      kind: 'stage',
      label: 'Build',
      runId: null,
      personaIds: ['coder'],
      position: { x: 0, y: 0 },
    },
    {
      id: 'qa-gate',
      kind: 'gate',
      label: 'Tests pass',
      condition: '',
      mode: 'automated_approval',
      position: { x: 0, y: 0 },
    },
  ],
  edges: [
    { id: 'e1', source: 'plan', target: 'plan-gate', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
    { id: 'e2', source: 'plan-gate', target: 'build', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
    { id: 'e3', source: 'build', target: 'qa-gate', cp1: { x: 0, y: 0 }, cp2: { x: 0, y: 0 } },
  ],
};

const researchWorkflow: Workflow = {
  id: '22222222-2222-4222-8222-222222222222',
  name: 'Research sweep',
  scope: 'system',
  nodes: [],
  edges: [],
};

const specCampaign: SpecCampaign = {
  id: '33333333-3333-4333-8333-333333333333',
  slug: 'ship-the-thing',
  name: 'Ship the thing',
  ownerId: 'jozef',
  workflowId: buildWorkflow.id,
  workflowVersion: '1.0.0',
  workflowName: buildWorkflow.name,
  sessionId: 'session-1',
  sessionName: 'Ship the thing',
  status: 'blocked',
  activeStageId: 'spec-prd-gate',
  stageState: [
    { stageId: 'spec-brief', label: 'Brief', status: 'complete' },
    { stageId: 'spec-prd-gate', label: 'Review PRD', status: 'blocked' },
  ],
  metadata: {
    pending_workflow_gates: [
      { id: 'gate-prd', node_id: 'spec-prd-gate', summary: 'PRD review required' },
    ],
  },
  createdAt: now,
  updatedAt: now,
};

const otherCampaign: ResearchCampaign = {
  ...specCampaign,
  id: '44444444-4444-4444-8444-444444444444',
  slug: 'not-this-workflow',
  name: 'Different workflow run',
  workflowId: researchWorkflow.id,
  status: 'running',
  stageState: [],
  metadata: {},
};

const repos: RepoRecord[] = [
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'volundr',
    url: 'https://github.com/niuulabs/volundr',
    cloneUrl: 'https://github.com/niuulabs/volundr.git',
    defaultBranch: 'main',
    branches: ['main', 'feat/x'],
  },
];

const board: TrackerProject = {
  id: 'board-1',
  name: 'Platform',
  description: '',
  status: 'started',
  url: 'https://linear.app/board-1',
  milestoneCount: 0,
  issueCount: 1,
  slug: 'platform',
};

const issue: TrackerIssue = {
  id: 'issue-1',
  identifier: 'NIU-1',
  title: 'Fix the gate',
  description: '',
  status: 'todo',
  assignee: null,
  labels: [],
  priority: 2,
  url: 'https://linear.app/issue/NIU-1',
  milestoneId: null,
};

function makeServices(overrides: Record<string, unknown> = {}) {
  return {
    'ting.workflows': {
      listWorkflows: vi.fn().mockResolvedValue([buildWorkflow, researchWorkflow]),
      saveWorkflow: vi.fn(async (workflow: Workflow) => workflow),
      launchWorkflow: vi.fn().mockResolvedValue({
        workflowId: buildWorkflow.id,
        workflowName: buildWorkflow.name,
        slug: 'build-and-ship',
        sessionId: 'sess-9',
        sessionName: 'build-and-ship',
        status: 'starting',
        clusterName: 'valhalla',
      }),
    },
    'ting.specs': {
      listCampaigns: vi.fn().mockResolvedValue([specCampaign]),
      reviewCampaign: vi.fn().mockResolvedValue(specCampaign),
    },
    'ting.research': {
      listCampaigns: vi.fn().mockResolvedValue([otherCampaign]),
    },
    'niuu.repos': { getRepos: vi.fn().mockResolvedValue(repos) },
    'ravn.personas': {
      listPersonas: vi.fn().mockResolvedValue([]),
      getPersonaYaml: vi.fn().mockResolvedValue(''),
    },
    mimir: {
      mounts: { listRegistryMounts: vi.fn().mockResolvedValue([]) },
    },
    'ting.tracker': {
      listProjects: vi.fn().mockResolvedValue([board]),
      listIssues: vi.fn().mockResolvedValue([issue]),
    },
    ...overrides,
  };
}

function renderPage(services: Record<string, unknown> = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <SimpleWorkflowsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return services;
}

describe('SimpleWorkflowsPage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockSearch.current = {};
    mockOpenEventStream.mockReset();
    mockOpenEventStream.mockReturnValue({ close: vi.fn() });
  });

  it('lists workflows with gate and stage counts and selects the first', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId(`simple-workflow-${buildWorkflow.id}`)).toBeInTheDocument(),
    );
    // Only the human gate counts — the automated one does not stop for anyone.
    expect(screen.getByTestId(`simple-workflow-${buildWorkflow.id}`)).toHaveTextContent(
      '1 gates · 2 stages · ran 1×',
    );
    expect(screen.getByTestId(`simple-workflow-${buildWorkflow.id}`)).toHaveAttribute(
      'data-selected',
      'true',
    );
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Build and ship');
    expect(screen.getByText('yours')).toBeInTheDocument();
    expect(screen.getByText('Stops for you at 1 gate')).toBeInTheDocument();
  });

  it('opens workflow import from the simple catalog', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('simple-workflow-import'));
    expect(screen.getByRole('dialog', { name: 'Import workflow' })).toBeInTheDocument();
  });

  it('falls back to the stage names and counts every human gate', async () => {
    const twoGates: Workflow = {
      ...buildWorkflow,
      description: undefined,
      nodes: [
        ...buildWorkflow.nodes,
        {
          id: 'ship-gate',
          kind: 'gate',
          label: 'the release',
          condition: '',
          mode: 'human_review',
          position: { x: 0, y: 0 },
        },
      ],
    };
    renderPage(
      makeServices({
        'ting.workflows': { listWorkflows: vi.fn().mockResolvedValue([twoGates]) },
      }),
    );

    await waitFor(() =>
      expect(screen.getByTestId(`simple-workflow-${twoGates.id}`)).toBeInTheDocument(),
    );
    expect(screen.getAllByText('Plan · Build').length).toBeGreaterThan(0);
    expect(screen.getByText('Stops for you at 2 gates')).toBeInTheDocument();
  });

  it('honours ?workflow= and puts a click into the url', async () => {
    mockSearch.current = { workflow: researchWorkflow.id };
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Research sweep'),
    );
    expect(screen.getByText('shared')).toBeInTheDocument();
    expect(
      screen.getByText('It runs start to finish without stopping for you.'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`simple-workflow-${buildWorkflow.id}`));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/workflows',
      search: { workflow: buildWorkflow.id },
    });
  });

  it('creates a workflow and opens it in the builder', async () => {
    const services = makeServices();
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('simple-workflow-new')).toBeEnabled());

    fireEvent.click(screen.getByTestId('simple-workflow-new'));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    const call = mockNavigate.mock.calls[0]?.[0] as { to: string; search: { id: string } };
    expect(call.to).toBe('/ting/workflows/build');
    expect(call.search.id).toBeTruthy();
  });

  it('prefills the prompt from a tracker issue', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('simple-workflow-pick-issue')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('simple-workflow-pick-issue'));
    await waitFor(() =>
      expect(screen.getByTestId(`workflow-issue-${issue.id}`)).toBeInTheDocument(),
    );
    expect(
      screen.getByTestId(`workflow-issue-project-default-${board.id}`),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`workflow-issue-${issue.id}`));

    await waitFor(() =>
      expect(screen.getByTestId('workflow-launch-prompt')).toHaveValue(
        'NIU-1 · Fix the gate\nhttps://linear.app/issue/NIU-1',
      ),
    );
  });

  it('hides the issue panel when no tracker service is registered', async () => {
    const services = makeServices();
    delete (services as Record<string, unknown>)['ting.tracker'];
    renderPage(services);

    await waitFor(() =>
      expect(screen.getByTestId(`simple-workflow-${buildWorkflow.id}`)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('simple-workflow-pick-issue')).not.toBeInTheDocument();
  });

  it('launches with repo and branch from the url and opens the session', async () => {
    mockSearch.current = { repo: repos[0]!.cloneUrl, branch: 'feat/x' };
    const services = makeServices();
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('simple-workflow-launch')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: '  Ship the gate fix.  ' },
    });
    fireEvent.click(screen.getByTestId('simple-workflow-launch'));

    const workflows = services['ting.workflows'] as { launchWorkflow: ReturnType<typeof vi.fn> };
    await waitFor(() =>
      expect(workflows.launchWorkflow).toHaveBeenCalledWith(buildWorkflow.id, {
        prompt: 'Ship the gate fix.',
        repo: repos[0]!.cloneUrl,
        branch: 'feat/x',
      }),
    );
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: 'sess-9' },
      }),
    );
  });

  it('surfaces a launch failure instead of navigating', async () => {
    const services = makeServices({
      'ting.workflows': {
        listWorkflows: vi.fn().mockResolvedValue([buildWorkflow]),
        launchWorkflow: vi.fn().mockRejectedValue(new Error('cluster is full')),
      },
    });
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('simple-workflow-launch')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: 'Ship it.' },
    });
    fireEvent.click(screen.getByTestId('simple-workflow-launch'));

    expect(await screen.findByRole('alert')).toHaveTextContent('cluster is full');
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('lists only the runs of the selected workflow and approves its gate', async () => {
    // The review never settles, so what clears the gate here is the optimistic
    // cache write rather than a refetch.
    const services = makeServices({
      'ting.specs': {
        listCampaigns: vi.fn().mockResolvedValue([specCampaign]),
        reviewCampaign: vi.fn().mockReturnValue(new Promise(() => undefined)),
      },
    });
    renderPage(services);

    await waitFor(() =>
      expect(screen.getByTestId(`workflow-run-${specCampaign.slug}`)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId(`workflow-run-${otherCampaign.slug}`)).not.toBeInTheDocument();
    expect(screen.getByTestId(`workflow-run-${specCampaign.slug}`)).toHaveTextContent(
      'waiting on you · Review PRD',
    );

    fireEvent.click(screen.getByTestId(`workflow-run-approve-${specCampaign.slug}`));

    const specs = services['ting.specs'] as { reviewCampaign: ReturnType<typeof vi.fn> };
    await waitFor(() =>
      expect(specs.reviewCampaign).toHaveBeenCalledWith(specCampaign.slug, {
        decision: 'approve',
        notes: '',
        gateId: 'gate-prd',
        nodeId: 'spec-prd-gate',
      }),
    );
    // The pending gate is cleared optimistically, so the buttons go away at once.
    await waitFor(() =>
      expect(
        screen.queryByTestId(`workflow-run-approve-${specCampaign.slug}`),
      ).not.toBeInTheDocument(),
    );
  });

  it('requires notes before requesting changes', async () => {
    const services = makeServices();
    renderPage(services);
    await waitFor(() =>
      expect(screen.getByTestId(`workflow-run-changes-${specCampaign.slug}`)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId(`workflow-run-changes-${specCampaign.slug}`));
    expect(screen.getByTestId(`workflow-run-send-${specCampaign.slug}`)).toBeDisabled();

    fireEvent.change(screen.getByTestId(`workflow-run-notes-${specCampaign.slug}`), {
      target: { value: 'Tighten the scope.' },
    });
    fireEvent.click(screen.getByTestId(`workflow-run-send-${specCampaign.slug}`));

    const specs = services['ting.specs'] as { reviewCampaign: ReturnType<typeof vi.fn> };
    await waitFor(() =>
      expect(specs.reviewCampaign).toHaveBeenCalledWith(specCampaign.slug, {
        decision: 'changes_requested',
        notes: 'Tighten the scope.',
        gateId: 'gate-prd',
        nodeId: 'spec-prd-gate',
      }),
    );
  });

  it('refreshes runs when the campaign stream fires', async () => {
    const services = makeServices();
    renderPage(services);
    await waitFor(() =>
      expect(screen.getByTestId(`workflow-run-${specCampaign.slug}`)).toBeInTheDocument(),
    );

    const specs = services['ting.specs'] as { listCampaigns: ReturnType<typeof vi.fn> };
    const callsBefore = specs.listCampaigns.mock.calls.length;
    const options = mockOpenEventStream.mock.calls[0]?.[1] as {
      onEvent: (payload: { event: string; data: string }) => void;
    };
    options.onEvent({ event: 'workflow.campaign.updated', data: '{}' });
    options.onEvent({ event: 'saga.updated', data: '{}' });

    await waitFor(() => expect(specs.listCampaigns.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it('says so when the workflow list is empty', async () => {
    const services = makeServices({
      'ting.workflows': { listWorkflows: vi.fn().mockResolvedValue([]) },
    });
    renderPage(services);

    await waitFor(() =>
      expect(screen.getByText('No workflows yet. New builds your first one.')).toBeInTheDocument(),
    );
    expect(screen.getByText('No workflow selected yet.')).toBeInTheDocument();
  });

  it('surfaces a workflow list failure', async () => {
    const services = makeServices({
      'ting.workflows': { listWorkflows: vi.fn().mockRejectedValue(new Error('ting is down')) },
    });
    renderPage(services);

    expect(await screen.findByRole('alert')).toHaveTextContent('ting is down');
  });
});

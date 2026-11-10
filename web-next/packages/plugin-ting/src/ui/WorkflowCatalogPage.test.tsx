import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import type { RepoRecord } from '@niuulabs/ui';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ResearchCampaign, SpecCampaign, TrackerIssue, TrackerProject } from '../ports';
import type { Workflow } from '../domain/workflow';
import { WorkflowCatalogPage } from './WorkflowCatalogPage';

const mockNavigate = vi.fn();
const mockSearch = vi.hoisted(() => ({
  current: {} as { workflow?: string; id?: string; repo?: string; branch?: string },
}));
const mockDownload = vi.hoisted(() => vi.fn());
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

vi.mock('./workflowFiles', () => ({
  downloadWorkflowFile: mockDownload,
  readWorkflowFile: vi.fn(),
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

const editable: Workflow = { ...buildWorkflow, canEdit: true };

const versionSummaries = [
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
];

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
    bifrost: createMockBifrostService(),
    'ting.workflows': {
      listWorkflows: vi.fn().mockResolvedValue([buildWorkflow, researchWorkflow]),
      listWorkflowVersions: vi.fn().mockResolvedValue([]),
      getWorkflowVersion: vi.fn().mockResolvedValue(null),
      deleteWorkflow: vi.fn().mockResolvedValue(undefined),
      exportWorkflow: vi.fn().mockResolvedValue({
        filename: 'build-and-ship.yaml',
        data: new Blob(['name: Build and ship']),
      }),
      saveWorkflow: vi.fn(async (workflow: Workflow) => workflow),
      launchWorkflow: vi.fn().mockResolvedValue({
        workflowId: buildWorkflow.id,
        workflowName: buildWorkflow.name,
        slug: 'build-and-ship',
        campaignId: 'camp-9',
        campaignSlug: 'build-and-ship-0123456789ab',
        status: 'pending',
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

/** Services whose workflow port keeps the defaults apart from `workflows`. */
function withWorkflows(workflows: Record<string, unknown>) {
  const base = makeServices();
  return {
    ...base,
    'ting.workflows': { ...(base['ting.workflows'] as Record<string, unknown>), ...workflows },
  };
}

function renderPage(services: Record<string, unknown> = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <WorkflowCatalogPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return services;
}

describe('WorkflowCatalogPage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockDownload.mockReset();
    mockSearch.current = {};
    mockOpenEventStream.mockReset();
    mockOpenEventStream.mockReturnValue({ close: vi.fn() });
  });

  it('lists workflows with gate and stage counts and selects the first', async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId(`catalog-workflow-${buildWorkflow.id}`)).toBeInTheDocument(),
    );
    // Only the human gate counts — the automated one does not stop for anyone.
    expect(screen.getByTestId(`catalog-workflow-${buildWorkflow.id}`)).toHaveTextContent(
      '1 gates · 2 stages · ran 1×',
    );
    expect(screen.getByTestId(`catalog-workflow-${buildWorkflow.id}`)).toHaveAttribute(
      'data-selected',
      'true',
    );
    await waitFor(() =>
      expect(screen.getByTestId('builder-title')).toHaveTextContent('Build and ship'),
    );
    expect(screen.getByTestId('workflow-builder')).toBeInTheDocument();
    expect(screen.getByText('yours')).toBeInTheDocument();
    expect(screen.getByText('Stops for you at 1 gate')).toBeInTheDocument();
  });

  it('opens workflow import from the list', () => {
    renderPage();
    fireEvent.click(screen.getByTestId('catalog-workflow-import'));
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
    renderPage(withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([twoGates]) }));

    await waitFor(() =>
      expect(screen.getByTestId(`catalog-workflow-${twoGates.id}`)).toBeInTheDocument(),
    );
    expect(screen.getAllByText('Plan · Build').length).toBeGreaterThan(0);
    expect(screen.getByText('Stops for you at 2 gates')).toBeInTheDocument();
  });

  it('honours ?workflow= and puts a click into the url', async () => {
    mockSearch.current = { workflow: researchWorkflow.id };
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('builder-title')).toHaveTextContent('Research sweep'),
    );
    expect(screen.getByText('shared')).toBeInTheDocument();
    expect(
      screen.getByText('It runs start to finish without stopping for you.'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId(`catalog-workflow-${buildWorkflow.id}`));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/workflows',
      search: { id: undefined, workflow: buildWorkflow.id },
    });
  });

  it('opens the workflow an older builder link names in ?id=', async () => {
    mockSearch.current = { id: researchWorkflow.id };
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId('builder-title')).toHaveTextContent('Research sweep'),
    );
    expect(screen.getByTestId(`catalog-workflow-${researchWorkflow.id}`)).toHaveAttribute(
      'data-selected',
      'true',
    );
  });

  it('creates a workflow and opens it for editing in place', async () => {
    const created: Workflow[] = [];
    const services = withWorkflows({
      listWorkflows: vi.fn(async () => [buildWorkflow, researchWorkflow, ...created]),
      saveWorkflow: vi.fn(async (workflow: Workflow) => {
        created.push(workflow);
        return workflow;
      }),
    });
    mockNavigate.mockImplementation(({ search }: { search?: typeof mockSearch.current }) => {
      if (search) mockSearch.current = search;
    });
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('catalog-workflow-new')).toBeEnabled());

    fireEvent.click(screen.getByTestId('catalog-workflow-new'));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    const call = mockNavigate.mock.calls[0]?.[0] as { to: string; search: { workflow: string } };
    expect(call.to).toBe('/ting/workflows');
    expect(call.search.workflow).toBe(created[0]?.id);
    await waitFor(() =>
      expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Editing'),
    );
    expect(screen.getByTestId('builder-title')).toHaveTextContent('New Workflow');
    expect(screen.queryByLabelText('Workflow catalog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('workflow-run-panel')).not.toBeInTheDocument();
  });

  it('says when a new workflow cannot be created', async () => {
    renderPage(withWorkflows({ saveWorkflow: vi.fn().mockRejectedValue(new Error('no room')) }));
    await waitFor(() => expect(screen.getByTestId('catalog-workflow-new')).toBeEnabled());

    fireEvent.click(screen.getByTestId('catalog-workflow-new'));

    expect(await screen.findByText('Could not create a workflow: no room')).toBeInTheDocument();
  });

  it('prefills the prompt from a tracker issue', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId('catalog-workflow-pick-issue')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('catalog-workflow-pick-issue'));
    await waitFor(() =>
      expect(screen.getByTestId(`workflow-issue-${issue.id}`)).toBeInTheDocument(),
    );
    expect(screen.getByTestId(`workflow-issue-project-default-${board.id}`)).toBeInTheDocument();

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
      expect(screen.getByTestId(`catalog-workflow-${buildWorkflow.id}`)).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('catalog-workflow-pick-issue')).not.toBeInTheDocument();
  });

  it('launches on the Forge target picked under Run on', async () => {
    const services = makeServices({
      'ting.dispatch': {
        getClusters: vi.fn().mockResolvedValue([
          { connectionId: 'ymir', name: 'Ymir', url: '', enabled: true },
          { connectionId: 'valhalla', name: 'Valhalla', url: '', enabled: true },
          { connectionId: 'off', name: 'Off', url: '', enabled: false },
        ]),
      },
    });
    renderPage(services);
    const target = await screen.findByRole('combobox', { name: 'Run on' });
    await waitFor(() => expect(within(target).getAllByRole('option')).toHaveLength(3));

    fireEvent.change(target, { target: { value: 'valhalla' } });
    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: 'Plan it.' },
    });
    fireEvent.click(screen.getByTestId('catalog-workflow-launch'));

    const workflows = services['ting.workflows'] as { launchWorkflow: ReturnType<typeof vi.fn> };
    await waitFor(() =>
      expect(workflows.launchWorkflow).toHaveBeenCalledWith(
        buildWorkflow.id,
        expect.objectContaining({ prompt: 'Plan it.', connectionId: 'valhalla' }),
      ),
    );
  });

  it('says when the Forge targets cannot be read', async () => {
    renderPage(
      makeServices({
        'ting.dispatch': { getClusters: vi.fn().mockRejectedValue(new Error('Ting is down')) },
      }),
    );
    expect(
      await screen.findByText('Could not load Forge targets: Ting is down'),
    ).toBeInTheDocument();
  });

  it('launches with repo and branch from the url and opens the session', async () => {
    mockSearch.current = { repo: repos[0]!.cloneUrl, branch: 'feat/x' };
    const services = makeServices();
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('catalog-workflow-launch')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: '  Ship the gate fix.  ' },
    });
    fireEvent.click(screen.getByTestId('catalog-workflow-launch'));

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
        to: '/ting/review/run/$slug',
        params: { slug: 'build-and-ship-0123456789ab' },
      }),
    );
  });

  it('surfaces a launch failure instead of navigating', async () => {
    const services = withWorkflows({
      listWorkflows: vi.fn().mockResolvedValue([buildWorkflow]),
      launchWorkflow: vi.fn().mockRejectedValue(new Error('cluster is full')),
    });
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('catalog-workflow-launch')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: 'Ship it.' },
    });
    fireEvent.click(screen.getByTestId('catalog-workflow-launch'));

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
    const services = withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([]) });
    renderPage(services);

    await waitFor(() =>
      expect(screen.getByText('No workflows yet. New builds your first one.')).toBeInTheDocument(),
    );
    expect(screen.getByText('No workflow selected yet.')).toBeInTheDocument();
  });

  it('surfaces a workflow list failure', async () => {
    const services = withWorkflows({
      listWorkflows: vi.fn().mockRejectedValue(new Error('ting is down')),
    });
    renderPage(services);

    expect(await screen.findByRole('alert')).toHaveTextContent('ting is down');
  });

  it('caches the model catalog apart from the model list the AI models page reads', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const modelList = [{ id: 'claude-opus' }];
    client.setQueryData(['bifrost', 'models'], modelList);

    render(
      <QueryClientProvider client={client}>
        <ServicesProvider services={makeServices()}>
          <WorkflowCatalogPage />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(client.getQueryData(['bifrost', 'model-catalog'])).toEqual(expect.any(Object)),
    );
    expect(Array.isArray(client.getQueryData(['bifrost', 'model-catalog']))).toBe(false);
    expect(client.getQueryData(['bifrost', 'models'])).toBe(modelList);
  });

  it('keeps the canvas free of a second workflow picker and launch button', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('workflow-builder')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('workflow-picker-trigger'));
    expect(screen.queryByTestId('workflow-picker')).not.toBeInTheDocument();
    expect(screen.queryByTestId('launch-workflow')).not.toBeInTheDocument();
  });

  it('edits in place: the list and run panel step aside until cancel', async () => {
    renderPage(withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([editable]) }));
    await waitFor(() => expect(screen.getByTestId('edit-workflow')).toBeInTheDocument());
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');
    expect(screen.getByLabelText('Workflow catalog')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-run-panel')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('edit-workflow'));

    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Editing');
    expect(screen.getByTestId('workflow-catalog-page')).toHaveAttribute('data-editing', 'true');
    expect(screen.queryByLabelText('Workflow catalog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('workflow-run-panel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('cancel-workflow-edit'));

    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');
    expect(screen.getByLabelText('Workflow catalog')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-run-panel')).toBeInTheDocument();
  });

  it('saves an edit and returns to viewing the saved workflow', async () => {
    const services = withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([editable]) });
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('edit-workflow')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('edit-workflow'));
    fireEvent.click(screen.getByTestId('open-workflow-meta'));
    fireEvent.change(screen.getByDisplayValue('Build and ship'), {
      target: { value: 'Build and ship well' },
    });
    fireEvent.click(screen.getByTestId('save-workflow'));

    const workflows = services['ting.workflows'] as { saveWorkflow: ReturnType<typeof vi.fn> };
    await waitFor(() => expect(workflows.saveWorkflow).toHaveBeenCalledTimes(1));
    const saved = workflows.saveWorkflow.mock.calls[0]![0] as Workflow;
    expect(saved.name).toBe('Build and ship well');
    await waitFor(() =>
      expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing'),
    );
    expect(screen.getByTestId('workflow-run-panel')).toBeInTheDocument();
  });

  it('loads an exact historical version and launches that version', async () => {
    const head: Workflow = {
      ...editable,
      version: '1.1.0',
      revision: 'head-cas-2',
      documentRevision: 'sha256:11',
      isHead: true,
    };
    const historical: Workflow = {
      ...head,
      version: '1.0.0',
      documentRevision: 'sha256:10',
      isHead: false,
    };
    let releaseVersion!: (workflow: Workflow) => void;
    const services = withWorkflows({
      listWorkflows: vi.fn().mockResolvedValue([head]),
      listWorkflowVersions: vi.fn().mockResolvedValue(versionSummaries),
      getWorkflowVersion: vi.fn().mockReturnValue(
        new Promise<Workflow>((resolve) => {
          releaseVersion = resolve;
        }),
      ),
    });
    renderPage(services);

    await waitFor(() => expect(screen.getByTestId('workflow-version-picker')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('workflow-version-picker'), {
      target: { value: '1.0.0' },
    });

    const workflows = services['ting.workflows'] as {
      getWorkflowVersion: ReturnType<typeof vi.fn>;
      launchWorkflow: ReturnType<typeof vi.fn>;
    };
    await waitFor(() => expect(screen.getByTestId('edit-workflow')).toBeDisabled());
    expect(screen.getByTestId('workflow-versions-loading')).toBeInTheDocument();
    expect(workflows.getWorkflowVersion).toHaveBeenCalledWith(head.id, '1.0.0');

    releaseVersion(historical);
    await waitFor(() => expect(screen.getByTestId('workflow-version-picker')).toHaveValue('1.0.0'));
    expect(screen.getByTestId('edit-workflow')).toBeEnabled();

    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: 'Replay the old flow.' },
    });
    fireEvent.click(screen.getByTestId('catalog-workflow-launch'));
    await waitFor(() =>
      expect(workflows.launchWorkflow).toHaveBeenCalledWith(
        head.id,
        expect.objectContaining({ prompt: 'Replay the old flow.', workflowVersion: '1.0.0' }),
      ),
    );
  });

  it('walks into a child workflow and back to the exact parent draft', async () => {
    const child: Workflow = {
      ...researchWorkflow,
      version: '1.2.0',
      documentRevision: 'sha256:child-12',
      canEdit: true,
    };
    const parent: Workflow = {
      ...buildWorkflow,
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
      edges: [],
    };
    renderPage(withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([parent, child]) }));

    await waitFor(() => expect(screen.getByTestId('builder-title')).toHaveTextContent(parent.name));
    fireEvent.click(screen.getByTestId('edit-workflow'));
    fireEvent.mouseDown(screen.getByTestId('workflow-node-child-node'));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Draft child label' } });

    fireEvent.click(screen.getByTestId('open-child-workflow'));
    expect(screen.getByTestId('builder-title')).toHaveTextContent(child.name);
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Viewing');
    expect(screen.getByTestId('workflow-parent-0')).toHaveTextContent(parent.name);

    fireEvent.click(screen.getByTestId('workflow-parent-back'));
    expect(screen.getByTestId('builder-title')).toHaveTextContent(parent.name);
    expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Editing');
    expect(screen.getByTestId('workflow-dirty-state')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId('workflow-node-child-node'));
    expect(screen.getByLabelText('Name')).toHaveValue('Draft child label');
  });

  it('repins a persona dependency while editing', async () => {
    const bundled: Workflow = {
      ...editable,
      origin: 'bundled',
      personaDependencies: { coder: { id: 'coder', revision: 'r1', digest: 'sha256:r1' } },
    };
    const services = withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([bundled]) });
    renderPage(services);
    await waitFor(() => expect(screen.getByTestId('edit-workflow')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('edit-workflow'));
    fireEvent.click(screen.getByRole('button', { name: 'Use current local persona' }));

    const workflows = services['ting.workflows'] as { saveWorkflow: ReturnType<typeof vi.fn> };
    await waitFor(() =>
      expect(workflows.saveWorkflow).toHaveBeenCalledWith(
        expect.objectContaining({ id: bundled.id, refreshPersonas: ['coder'] }),
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId('workflow-editor-mode')).toHaveTextContent('Editing'),
    );
  });

  it('exports the version on the canvas', async () => {
    const services = renderPage(
      withWorkflows({
        listWorkflows: vi.fn().mockResolvedValue([{ ...editable, version: '2.0.0' }]),
      }),
    );
    await waitFor(() => expect(screen.getByTestId('export-workflow-yaml')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('export-workflow-yaml'));

    const workflows = services['ting.workflows'] as { exportWorkflow: ReturnType<typeof vi.fn> };
    await waitFor(() =>
      expect(workflows.exportWorkflow).toHaveBeenCalledWith(editable.id, 'yaml', '2.0.0'),
    );
    await waitFor(() => expect(mockDownload).toHaveBeenCalledTimes(1));
  });

  it('deletes the workflow on the canvas and goes back to the list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const services = renderPage(
      withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([editable, researchWorkflow]) }),
    );
    await waitFor(() =>
      expect(screen.getByTestId(`delete-workflow-${editable.id}`)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId(`delete-workflow-${editable.id}`));

    const workflows = services['ting.workflows'] as { deleteWorkflow: ReturnType<typeof vi.fn> };
    await waitFor(() => expect(workflows.deleteWorkflow).toHaveBeenCalledWith(editable.id));
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith({
        to: '/ting/workflows',
        search: { id: undefined, workflow: undefined },
      }),
    );
  });

  it('keeps the workflow when the delete is not confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const services = renderPage(
      withWorkflows({ listWorkflows: vi.fn().mockResolvedValue([editable]) }),
    );
    await waitFor(() =>
      expect(screen.getByTestId(`delete-workflow-${editable.id}`)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId(`delete-workflow-${editable.id}`));

    const workflows = services['ting.workflows'] as { deleteWorkflow: ReturnType<typeof vi.fn> };
    expect(workflows.deleteWorkflow).not.toHaveBeenCalled();
  });

  it('surfaces a failed export under the canvas', async () => {
    renderPage(
      withWorkflows({
        listWorkflows: vi.fn().mockResolvedValue([editable]),
        exportWorkflow: vi.fn().mockRejectedValue(new Error('export broke')),
      }),
    );
    await waitFor(() => expect(screen.getByTestId('export-workflow-yaml')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('export-workflow-yaml'));

    expect(await screen.findByText('export broke')).toBeInTheDocument();
  });

  it('tells the person to edit before launching a workflow with unresolved bindings', async () => {
    renderPage(
      withWorkflows({
        listWorkflows: vi.fn().mockResolvedValue([
          {
            ...buildWorkflow,
            requirements: [{ id: 'repo', kind: 'repository', resolved: false, message: '' }],
          },
        ]),
      }),
    );

    expect(await screen.findByTestId('catalog-workflow-unresolved')).toHaveTextContent(
      'choose Edit',
    );
    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), {
      target: { value: 'Ship it.' },
    });
    expect(screen.getByTestId('catalog-workflow-launch')).toBeDisabled();
  });
});

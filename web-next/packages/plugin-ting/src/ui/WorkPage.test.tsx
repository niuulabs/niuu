import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IDispatchBus, IWorkService } from '../ports';
import type { WorkCollection, WorkDetail, WorkSummary, WorkTask } from '../domain/work';
import { parseWorkRouteId, WorkPage, workRouteId } from './WorkPage';

const mockNavigate = vi.fn();
const routerState = vi.hoisted(() => ({
  params: {} as { workId?: string },
  search: {} as { filter?: string; q?: string },
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => routerState.params,
  useSearch: () => routerState.search,
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

vi.mock('./WorkCampaignResults', () => ({
  WorkCampaignResults: () => <section>Campaign results</section>,
}));

vi.mock('./TrackerWorkImportDialog', () => ({
  TrackerWorkImportDialog: ({
    open,
    onImported,
  }: {
    open: boolean;
    onImported: (saga: { id: string }) => void;
  }) =>
    open ? (
      <section>
        Tracker import dialog
        <button type="button" onClick={() => onImported({ id: 'imported-1' })}>
          Simulate import
        </button>
      </section>
    ) : null,
}));

function summary(
  id: WorkSummary['id'],
  kind: WorkSummary['kind'],
  condition: WorkSummary['condition'],
  title: string,
): WorkSummary {
  return {
    id,
    kind,
    title,
    condition,
    rawState: { source: kind, status: condition },
    currentStep: condition === 'needsAttention' ? 'Review the requested change' : 'Working',
    attention:
      condition === 'needsAttention'
        ? { kind: 'decision', message: 'Review the proposed acceptance criteria' }
        : null,
    createdAt: '2026-09-20T12:00:00Z',
    updatedAt: '2026-09-20T12:05:00Z',
    source:
      kind === 'project'
        ? {
            connectionId: 'linear-main',
            provider: 'Linear',
            projectId: 'NIU',
            url: 'https://tracker.example/niu',
            status: 'started',
          }
        : null,
    workflow: { id: 'workflow-1', version: '1.2.0', documentRevision: 'revision-1' },
    session: null,
    links: {
      self: `/api/v1/ting/work/${id.replace(':', '/')}`,
      specialist: kind === 'specification' ? '/ting/specs/billing' : '/ting/sagas/project-1',
      ...(kind === 'project' ? { source: 'https://tracker.example/niu' } : {}),
    },
    actions: kind === 'specification' ? ['open', 'review'] : ['open', 'openDefinition'],
  };
}

const project = summary('project:project-1', 'project', 'active', 'Niuu Platform');
const specification = summary(
  'campaign:spec-1',
  'specification',
  'needsAttention',
  'Define usage-based billing',
);
const execution = summary(
  'execution:execution-1',
  'workflowExecution',
  'completed',
  'Ship workspace invitations',
);

const collection: WorkCollection = {
  projects: [project],
  campaigns: [specification],
  executions: [execution],
  executionNextCursor: null,
  coverage: [
    {
      source: 'tracker',
      connectionId: 'github-secondary',
      status: 'unavailable',
      error: { code: 'offline', message: 'GitHub is offline' },
    },
  ],
};

const task: WorkTask = {
  source: {
    connectionId: 'linear-main',
    provider: 'Linear',
    projectId: 'NIU',
    url: 'https://tracker.example/NIU-142',
    status: 'In progress',
    issueId: 'issue-142',
    identifier: 'NIU-142',
    statusType: 'started',
  },
  title: 'Ship workspace invitations',
  operationalRun: null,
  dispatch: {
    sagaId: 'project-1',
    issueId: 'issue-142',
    repo: 'niuulabs/volundr',
    connectionId: 'forge-local',
    workflowId: 'workflow-1',
  },
  actions: ['dispatch'],
};

const projectDetail: WorkDetail = {
  item: project,
  tasks: [task],
  coverage: [{ source: 'operationalRuns', connectionId: null, status: 'complete', error: null }],
};

function makeWorkService(overrides: Partial<IWorkService> = {}): IWorkService {
  return {
    list: vi.fn().mockResolvedValue(collection),
    get: vi.fn().mockResolvedValue(projectDetail),
    ...overrides,
  };
}

function makeDispatchService(overrides: Partial<IDispatchBus> = {}): IDispatchBus {
  return {
    getQueue: vi.fn().mockResolvedValue([]),
    getClusters: vi.fn().mockResolvedValue([]),
    approve: vi.fn().mockResolvedValue([
      {
        issueId: 'issue-142',
        sessionId: 'session-1',
        sessionName: 'NIU-142',
        status: 'spawned',
        clusterName: 'local',
      },
    ]),
    dispatch: vi.fn().mockResolvedValue(undefined),
    dispatchBatch: vi.fn().mockResolvedValue({ dispatched: [], failed: [] }),
    ...overrides,
  };
}

function renderPage(work = makeWorkService(), dispatch = makeDispatchService()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ 'ting.work': work, 'ting.dispatch': dispatch }}>
        <WorkPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('WorkPage route identity', () => {
  it('uses source-qualified opaque route ids', () => {
    expect(workRouteId(project)).toBe('project:project-1');
    expect(parseWorkRouteId('campaign:abc:def')).toEqual({ kind: 'campaign', id: 'abc:def' });
    expect(parseWorkRouteId('wrong:abc')).toBeNull();
    expect(parseWorkRouteId(undefined)).toBeNull();
  });
});

describe('WorkPage', () => {
  beforeEach(() => {
    routerState.params = {};
    routerState.search = {};
    mockNavigate.mockReset();
  });

  it('shows project, attention, active and completed work with a scoped source error', async () => {
    const list = vi.fn().mockResolvedValue(collection);
    renderPage(makeWorkService({ list }));

    expect(await screen.findByText('Define usage-based billing')).toBeInTheDocument();
    expect(screen.getAllByText('Niuu Platform').length).toBeGreaterThan(0);
    expect(screen.getByText('Ship workspace invitations')).toBeInTheDocument();
    expect(screen.getByText(/GitHub is offline/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));

    fireEvent.click(
      within(screen.getByRole('navigation', { name: 'Work views' })).getByRole('button', {
        name: 'Active',
      }),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/ting/work', search: { filter: 'active' } }),
    );
  });

  it('offers the real tracker import and specialist brief flows from New work', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.click(screen.getByRole('button', { name: /new work/i }));
    expect(screen.getByRole('dialog', { name: 'Start new work' })).toBeInTheDocument();
    expect(screen.getByTestId('new-work-from-tracker')).toHaveAccessibleName(/from tracker/i);
    expect(screen.getByRole('link', { name: 'Start research' })).toHaveAttribute(
      'href',
      '/ting/research/new',
    );
    expect(screen.getByRole('link', { name: 'Create specification' })).toHaveAttribute(
      'href',
      '/ting/specs/new',
    );
  });

  it('closes New work when a brief flow link is followed', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.click(screen.getByRole('button', { name: /new work/i }));
    fireEvent.click(screen.getByRole('link', { name: 'Plan delivery' }));
    expect(screen.queryByRole('dialog', { name: 'Start new work' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /new work/i }));
    fireEvent.click(screen.getByRole('link', { name: 'Start research' }));
    expect(screen.queryByRole('dialog', { name: 'Start new work' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /new work/i }));
    fireEvent.click(screen.getByRole('link', { name: 'Create specification' }));
    expect(screen.queryByRole('dialog', { name: 'Start new work' })).not.toBeInTheDocument();
  });

  it('keeps filter and search context when opening source-qualified work', async () => {
    routerState.search = { filter: 'attention', q: 'billing' };
    renderPage();
    fireEvent.click(await screen.findByText('Define usage-based billing'));

    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/work/$workId',
      params: { workId: 'campaign:spec-1' },
      search: { filter: 'attention', q: 'billing' },
    });
  });

  it('keeps tracker status separate from Ting state and starts an eligible ticket once', async () => {
    routerState.params = { workId: 'project:project-1' };
    const approve = vi.fn().mockResolvedValue([
      {
        issueId: 'issue-142',
        sessionId: 'session-1',
        sessionName: 'NIU-142',
        status: 'spawned',
        clusterName: 'local',
      },
    ]);
    renderPage(makeWorkService(), makeDispatchService({ approve }));

    expect(await screen.findByText('NIU-142')).toBeInTheDocument();
    const ticket = screen.getByText('NIU-142').closest('article');
    expect(ticket).not.toBeNull();
    expect(within(ticket as HTMLElement).getByText('In progress')).toBeInTheDocument();
    expect(within(ticket as HTMLElement).getByText('Not started')).toBeInTheDocument();

    const start = screen.getByRole('button', { name: 'Start eligible work' });
    fireEvent.click(start);
    fireEvent.click(start);
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    expect(approve).toHaveBeenCalledWith(
      [
        {
          sagaId: 'project-1',
          issueId: 'issue-142',
          repo: 'niuulabs/volundr',
          connectionId: 'forge-local',
          workflowId: 'workflow-1',
        },
      ],
      { connectionId: 'forge-local' },
    );
  });

  it('does not describe an unlinked run as not started without complete run coverage', async () => {
    routerState.params = { workId: 'project:project-1' };
    const unavailableDetail: WorkDetail = {
      ...projectDetail,
      coverage: [
        {
          source: 'operationalRuns',
          connectionId: null,
          status: 'partial',
          error: { code: 'legacy_owner_unknown', message: 'Some run links are unavailable' },
        },
      ],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(unavailableDetail) }));

    const ticket = (await screen.findByText('NIU-142')).closest('article');
    expect(ticket).not.toBeNull();
    expect(within(ticket as HTMLElement).getByText('Unknown · linkage unavailable')).toBeVisible();
    expect(within(ticket as HTMLElement).queryByText('Not started')).not.toBeInTheDocument();
  });

  it('still reports an empty tracker project when only run linkage coverage is partial', async () => {
    routerState.params = { workId: 'project:project-1' };
    const emptyProject: WorkDetail = {
      ...projectDetail,
      tasks: [],
      coverage: [
        { source: 'tracker', connectionId: 'linear-main', status: 'complete', error: null },
        {
          source: 'operationalRuns',
          connectionId: 'linear-main',
          status: 'partial',
          error: { code: 'legacy_owner_unknown', message: 'Some run links are unavailable' },
        },
      ],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(emptyProject) }));

    expect(await screen.findByText('No tickets in this project')).toBeVisible();
  });

  it('keeps concise specification attention visible while campaign results load', async () => {
    routerState.params = { workId: 'campaign:spec-1' };
    const detail: WorkDetail = {
      item: { ...specification, campaignSlug: 'billing' },
      tasks: [],
      coverage: [],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(detail) }));

    expect(await screen.findByText('Review the proposed acceptance criteria')).toBeVisible();
    expect(screen.getByText('Campaign results')).toBeVisible();
  });

  it('shows a non-spawn dispatch result as a visible failure', async () => {
    routerState.params = { workId: 'project:project-1' };
    const approve = vi.fn().mockResolvedValue([
      {
        issueId: 'issue-142',
        sessionId: '',
        sessionName: '',
        status: 'rejected',
        clusterName: '',
      },
    ]);
    renderPage(makeWorkService(), makeDispatchService({ approve }));

    fireEvent.click(await screen.findByRole('button', { name: 'Start eligible work' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/did not start.*rejected/i);
  });

  it('shows a missing dispatch result as a visible failure', async () => {
    routerState.params = { workId: 'project:project-1' };
    renderPage(makeWorkService(), makeDispatchService({ approve: vi.fn().mockResolvedValue([]) }));

    fireEvent.click(await screen.findByRole('button', { name: 'Start eligible work' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/did not return a dispatch result/i);
  });

  it('loads older execution pages without duplicating records', async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce({
        ...collection,
        executions: [execution],
        executionNextCursor: 'cursor-2',
      })
      .mockResolvedValueOnce({
        ...collection,
        executions: [execution, { ...execution, id: 'execution:execution-2', title: 'Second run' }],
        executionNextCursor: null,
      });
    renderPage(makeWorkService({ list }));

    fireEvent.click(await screen.findByRole('button', { name: 'Load older executions' }));
    expect(await screen.findByText('Second run')).toBeInTheDocument();
    expect(screen.getAllByText('Ship workspace invitations')).toHaveLength(1);
    expect(list).toHaveBeenLastCalledWith({ executionLimit: 50, executionCursor: 'cursor-2' });
  });

  it('rejects an unsupported source-qualified route without querying detail', async () => {
    routerState.params = { workId: 'unknown:record-1' };
    const get = vi.fn();
    renderPage(makeWorkService({ get }));

    expect(await screen.findByText('Work link is invalid')).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it('loads a directly linked detail even when the collection source fails', async () => {
    routerState.params = { workId: 'project:project-1' };
    renderPage(
      makeWorkService({
        list: vi.fn().mockRejectedValue(new Error('collection offline')),
        get: vi.fn().mockResolvedValue(projectDetail),
      }),
    );

    expect(await screen.findByTestId('work-detail')).toHaveTextContent('Niuu Platform');
    expect(screen.getByRole('button', { name: 'Back to work' })).toBeInTheDocument();
  });

  it('shows a loading state until the first page of work arrives', () => {
    renderPage(makeWorkService({ list: vi.fn().mockReturnValue(new Promise(() => {})) }));

    expect(screen.getByText('Loading work…')).toBeInTheDocument();
  });

  it('reports why the work list could not be loaded', async () => {
    renderPage(makeWorkService({ list: vi.fn().mockRejectedValue(new Error('ting is offline')) }));

    expect(await screen.findByText('Work could not be loaded')).toBeInTheDocument();
    expect(screen.getByText('ting is offline')).toBeInTheDocument();
  });

  it('does not invent a reason when the failure carries none', async () => {
    renderPage(makeWorkService({ list: vi.fn().mockRejectedValue('nope') }));

    expect(await screen.findByText('Work could not be loaded')).toBeInTheDocument();
    expect(screen.getByText('Unknown error')).toBeInTheDocument();
  });

  it('invites the first piece of work when nothing exists yet', async () => {
    const empty: WorkCollection = {
      projects: [],
      campaigns: [],
      executions: [],
      executionNextCursor: null,
      coverage: [],
    };
    renderPage(makeWorkService({ list: vi.fn().mockResolvedValue(empty) }));

    expect(await screen.findByText('No work yet')).toBeInTheDocument();
    expect(screen.getByText('Start work from a tracker or brief.')).toBeInTheDocument();
  });

  it('gives a waiting item a quiet condition tone and counts it as active', async () => {
    const waiting = summary('campaign:waiting-1', 'research', 'waiting', 'Waiting on review');
    renderPage(
      makeWorkService({
        list: vi.fn().mockResolvedValue({ ...collection, campaigns: [specification, waiting] }),
      }),
    );

    await screen.findByText('Waiting on review');
    const badge = screen.getByText('Waiting').closest('.ting-work-condition');
    expect(badge).toHaveClass('ting-work-condition--quiet');
  });

  it('narrows the overview list to only completed work under the Completed filter', async () => {
    routerState.search = { filter: 'completed' };
    renderPage();
    await screen.findByText('Ship workspace invitations');

    const main = screen.getByTestId('work-overview');
    expect(within(main).getByRole('heading', { level: 1, name: 'Completed' })).toBeInTheDocument();
    expect(within(main).queryByText('Define usage-based billing')).not.toBeInTheDocument();
  });

  it('narrows the overview list to in-progress work under the Active filter', async () => {
    const waiting = summary('campaign:waiting-1', 'research', 'waiting', 'Waiting on review');
    routerState.search = { filter: 'active' };
    renderPage(
      makeWorkService({
        list: vi.fn().mockResolvedValue({ ...collection, campaigns: [specification, waiting] }),
      }),
    );
    await screen.findByText('Waiting on review');

    const main = screen.getByTestId('work-overview');
    expect(within(main).queryByText('Define usage-based billing')).not.toBeInTheDocument();
    expect(within(main).queryByText('Ship workspace invitations')).not.toBeInTheDocument();
  });

  it('shows no connected projects placeholder in the sidebar when there are none', async () => {
    renderPage(
      makeWorkService({
        list: vi.fn().mockResolvedValue({ ...collection, projects: [] }),
      }),
    );

    await screen.findByText('Define usage-based billing');
    expect(screen.getByText('No connected projects')).toBeInTheDocument();
  });

  it('opens the real tracker import dialog and closes New work when From tracker is used', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.click(screen.getByRole('button', { name: /new work/i }));
    fireEvent.click(screen.getByTestId('new-work-from-tracker'));

    expect(screen.queryByRole('dialog', { name: 'Start new work' })).not.toBeInTheDocument();
    expect(screen.getByText('Tracker import dialog')).toBeInTheDocument();
  });

  it('navigates to the newly imported project once the tracker import finishes', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.click(screen.getByRole('button', { name: /new work/i }));
    fireEvent.click(screen.getByTestId('new-work-from-tracker'));
    fireEvent.click(screen.getByRole('button', { name: 'Simulate import' }));

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/ting/work/$workId',
        params: { workId: 'project:imported-1' },
      }),
    );
  });

  it('opens a connected project from the sidebar list', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.click(
      within(screen.getByLabelText('Work workspace')).getByRole('button', {
        name: /Niuu Platform/i,
      }),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ params: { workId: 'project:project-1' } }),
    );
  });

  it('filters through the overview panel controls as well as the sidebar nav', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.click(
      within(screen.getByLabelText('Work filters')).getByRole('button', { name: 'Active' }),
    );
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/ting/work', search: { filter: 'active' } }),
    );
  });

  it('opens New work from the detail view secondary action', async () => {
    routerState.params = { workId: 'project:project-1' };
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /new work/i }));
    expect(screen.getByRole('dialog', { name: 'Start new work' })).toBeInTheDocument();
  });

  it('navigates back to the overview preserving filter and search from the detail view', async () => {
    routerState.params = { workId: 'project:project-1' };
    routerState.search = { filter: 'active', q: 'niuu' };
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Back to work' }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/work',
      search: { filter: 'active', q: 'niuu' },
    });
  });

  it('filters to needs-attention and completed work through the sidebar nav', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    const nav = within(screen.getByRole('navigation', { name: 'Work views' }));
    fireEvent.click(nav.getByRole('button', { name: 'Needs attention' }));
    expect(mockNavigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ to: '/ting/work', search: { filter: 'attention' } }),
    );

    fireEvent.click(nav.getByRole('button', { name: 'Completed' }));
    expect(mockNavigate).toHaveBeenLastCalledWith(
      expect.objectContaining({ to: '/ting/work', search: { filter: 'completed' } }),
    );
  });

  it('updates the search query as the operator types', async () => {
    renderPage();
    await screen.findByText('Define usage-based billing');

    fireEvent.change(screen.getByPlaceholderText('Search loaded work'), {
      target: { value: 'billing' },
    });
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/ting/work', search: { q: 'billing' } }),
    );
  });

  it('prefixes a bare id with its backend kind before navigating to detail', async () => {
    const bare = summary('research-77', 'research', 'active', 'Bare id research');
    renderPage(
      makeWorkService({
        list: vi.fn().mockResolvedValue({ ...collection, campaigns: [specification, bare] }),
      }),
    );

    fireEvent.click(await screen.findByText('Bare id research'));
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ params: { workId: 'campaign:research-77' } }),
    );
  });

  it('shows an unfilled current step distinct from tracker status and condition label', async () => {
    routerState.params = { workId: 'execution:execution-1' };
    const active = summary(
      'execution:execution-1',
      'workflowExecution',
      'active',
      'Ship workspace invitations',
    );
    const detail: WorkDetail = {
      item: { ...active, attention: null, currentStep: 'Deploying to staging' },
      tasks: [],
      coverage: [],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(detail) }));

    expect(await screen.findByText('Current step')).toBeInTheDocument();
    expect(screen.getByText('Deploying to staging')).toBeInTheDocument();
  });

  it('offers session and workflow-definition links, and full provenance, from the detail header', async () => {
    routerState.params = { workId: 'project:project-1' };
    const withLinks: WorkDetail = {
      ...projectDetail,
      item: {
        ...project,
        session: { id: 'session-9', connectionId: 'forge-local' },
        actions: ['open', 'openDefinition', 'openSession'],
      },
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(withLinks) }));

    await screen.findByTestId('work-detail');
    expect(screen.getByRole('link', { name: 'Open session' })).toHaveAttribute(
      'href',
      '/volundr/session/$sessionId',
    );
    expect(screen.getByRole('link', { name: 'Open definition' })).toHaveAttribute(
      'href',
      '/ting/workflows/build',
    );

    const provenance = screen.getByText('Provenance').closest('details') as HTMLElement;
    expect(provenance.textContent).toContain('Connected project');
    expect(provenance.textContent).toContain('started');
    expect(provenance.textContent).toContain('1.2.0');
    expect(within(provenance).getByRole('link', { name: /open source/i })).toHaveAttribute(
      'href',
      'https://tracker.example/niu',
    );
  });

  it('reports the tracker task open session link and non-not-started run state', async () => {
    routerState.params = { workId: 'project:project-1' };
    const withRun: WorkDetail = {
      ...projectDetail,
      tasks: [
        {
          ...task,
          operationalRun: {
            id: 'run-77',
            status: 'Running',
            sessionId: 'session-77',
            reviewerSessionId: null,
            branch: null,
            prUrl: null,
            updatedAt: null,
          },
        },
      ],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(withRun) }));

    const ticket = (await screen.findByText('NIU-142')).closest('article') as HTMLElement;
    expect(within(ticket).getByText('Running')).toBeInTheDocument();
    expect(within(ticket).getByRole('link', { name: 'Open session' })).toHaveAttribute(
      'href',
      '/volundr/sessions/session-77',
    );
  });

  it('starts a ticket that has no connection or workflow id bound to the dispatch', async () => {
    routerState.params = { workId: 'project:project-1' };
    const bareTask: WorkTask = {
      ...task,
      dispatch: { sagaId: 'project-1', issueId: 'issue-142', repo: 'niuulabs/volundr' },
    };
    const approve = vi.fn().mockResolvedValue([
      {
        issueId: 'issue-142',
        sessionId: 'session-1',
        sessionName: 'NIU-142',
        status: 'spawned',
        clusterName: 'local',
      },
    ]);
    renderPage(
      makeWorkService({ get: vi.fn().mockResolvedValue({ ...projectDetail, tasks: [bareTask] }) }),
      makeDispatchService({ approve }),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Start eligible work' }));
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    expect(approve).toHaveBeenCalledWith(
      [{ sagaId: 'project-1', issueId: 'issue-142', repo: 'niuulabs/volundr' }],
      undefined,
    );
  });

  it('labels the specialist link by work kind', async () => {
    routerState.params = { workId: 'campaign:research-1' };
    const research = summary('campaign:research-1', 'research', 'active', 'Explore billing');
    const detail: WorkDetail = {
      item: { ...research, attention: null, campaignSlug: null },
      tasks: [],
      coverage: [],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(detail) }));

    expect(await screen.findByRole('link', { name: /open research/i })).toBeInTheDocument();
  });

  it('labels the specialist link for a workflow execution', async () => {
    routerState.params = { workId: 'execution:execution-1' };
    const detail: WorkDetail = { item: { ...execution, attention: null }, tasks: [], coverage: [] };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(detail) }));

    expect(await screen.findByRole('link', { name: /inspect execution/i })).toBeInTheDocument();
  });

  it('falls back to a generic details label for an unrecognised work kind', async () => {
    routerState.params = { workId: 'campaign:launch-1' };
    const launch = summary('campaign:launch-1', 'workflowLaunch', 'active', 'Queued launch');
    const detail: WorkDetail = { item: { ...launch, attention: null }, tasks: [], coverage: [] };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(detail) }));

    expect(await screen.findByRole('link', { name: /open details/i })).toBeInTheDocument();
  });

  it('retries the source from the detail view coverage notice', async () => {
    routerState.params = { workId: 'project:project-1' };
    const get = vi
      .fn()
      .mockResolvedValueOnce({
        ...projectDetail,
        coverage: [
          {
            source: 'tracker',
            connectionId: null,
            status: 'unavailable',
            error: { code: 'offline', message: 'Tracker is offline' },
          },
        ],
      })
      .mockResolvedValueOnce(projectDetail);
    renderPage(makeWorkService({ get }));

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it('shows a partial coverage notice without an error message', async () => {
    routerState.params = { workId: 'project:project-1' };
    const partial: WorkDetail = {
      ...projectDetail,
      coverage: [{ source: 'tracker', connectionId: null, status: 'partial', error: null }],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(partial) }));

    expect(await screen.findByText(/returned partial results/i)).toBeInTheDocument();
  });

  it('shows an unavailable coverage notice without an error message', async () => {
    routerState.params = { workId: 'project:project-1' };
    const unavailable: WorkDetail = {
      ...projectDetail,
      coverage: [{ source: 'tracker', connectionId: null, status: 'unavailable', error: null }],
    };
    renderPage(makeWorkService({ get: vi.fn().mockResolvedValue(unavailable) }));

    expect(await screen.findByText(/is currently unavailable/i)).toBeInTheDocument();
  });
});

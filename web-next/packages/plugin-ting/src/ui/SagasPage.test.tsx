import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ToastProvider } from '@niuulabs/ui';
import { SagasPage } from './SagasPage';
import {
  createMockTingService,
  createMockTrackerService,
  createMockWorkflowService,
} from '../adapters/mock';
import type { Saga } from '../domain/saga';
import type { ITrackerBrowserService } from '../ports';

const mockNavigate = vi.fn();
const mockDispatchBus = {
  getClusters: vi.fn(async () => []),
};
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({}),
  useSearch: () => ({}),
}));

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <ToastProvider>
        <QueryClientProvider client={client}>
          <ServicesProvider services={services}>{children}</ServicesProvider>
        </QueryClientProvider>
      </ToastProvider>
    );
  };
}

function withDefaults(services: Record<string, unknown>) {
  const volundrRepos = createMockTingService();
  return {
    ting: volundrRepos,
    'ting.tracker': createMockTrackerService(),
    'ting.workflows': createMockWorkflowService(),
    'niuu.repos': {
      getRepos: async () => [
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'volundr',
          cloneUrl: 'https://github.com/niuulabs/volundr.git',
          url: 'https://github.com/niuulabs/volundr',
          defaultBranch: 'main',
          branches: ['main', 'develop'],
        },
      ],
    },
    'ting.dispatch': mockDispatchBus,
    ...services,
  };
}

function makeSaga(overrides: Partial<Saga> = {}): Saga {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-1',
    trackerType: 'linear',
    slug: 'test-saga',
    name: 'Test Saga',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/test',
    baseBranch: 'main',
    status: 'active',
    confidence: 80,
    createdAt: '2026-01-01T00:00:00Z',
    phaseSummary: { total: 2, completed: 0 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
    ...overrides,
  };
}

describe('SagasPage', () => {
  it('renders the sagas heading', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getByText('Sagas')).toBeInTheDocument());
  });

  it('shows grouped left-rail sections', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getByText('ACTIVE')).toBeInTheDocument());
    expect(screen.getByText('IN REVIEW')).toBeInTheDocument();
    expect(screen.getAllByText('COMPLETE').length).toBeGreaterThan(0);
    expect(screen.getAllByText('FAILED').length).toBeGreaterThan(0);
  });

  it('uses the saga title as the left-rail primary label', async () => {
    const sagaName = 'Readable Saga Title';
    const trackerId = '00000000-0000-0000-0000-000000000123';
    const repoName = 'niuulabs/saga-ui';
    const branchName = 'feat/compact-rail';
    const sagasSvc = {
      ...createMockTingService(),
      getSagas: async (): Promise<Saga[]> => [
        makeSaga({
          id: '00000000-0000-0000-0000-000000000123',
          name: sagaName,
          trackerId,
          repos: [repoName],
          featureBranch: branchName,
          phaseSummary: { total: 4, completed: 2 },
        }),
      ],
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: sagasSvc })) });

    await waitFor(() => expect(screen.getAllByText(sagaName).length).toBeGreaterThan(0));
    const railButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes(sagaName));
    expect(railButton).toBeDefined();
    expect(railButton).toHaveTextContent(sagaName);
    expect(railButton).not.toHaveTextContent(trackerId);
    expect(railButton).toHaveTextContent(repoName);
    expect(railButton).toHaveTextContent(branchName);
    expect(railButton).toHaveTextContent('2/4 runs');
  });

  it('falls back to the default repo label when a saga has no repo listed', async () => {
    const sagasSvc = {
      ...createMockTingService(),
      getSagas: async (): Promise<Saga[]> => [
        makeSaga({
          id: '00000000-0000-0000-0000-000000000124',
          name: 'Fallback Repo Saga',
          repos: [],
        }),
      ],
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: sagasSvc })) });

    await waitFor(() =>
      expect(screen.getAllByText('Fallback Repo Saga').length).toBeGreaterThan(0),
    );
    const railButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Fallback Repo Saga'));
    expect(railButton).toBeDefined();
    expect(railButton).toHaveTextContent('niuulabs/volundr');
  });

  it('shows detail without rendering a duplicate saga list in the main panel', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });

    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    expect(screen.queryByRole('region', { name: /Saga list/i })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole('status', { name: /Loading saga/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByText('feat/flokk-subs → main')).toBeInTheDocument();
  });

  it('filters sagas from the page-head search', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.change(screen.getByRole('searchbox', { name: /Filter sagas/i }), {
      target: { value: 'auth' },
    });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    expect(screen.queryByText('Plugin Ravn Scaffold')).not.toBeInTheDocument();
  });

  it('shows empty state when search matches nothing', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.change(screen.getByRole('searchbox', { name: /Filter sagas/i }), {
      target: { value: 'zzznomatch' },
    });
    await waitFor(() => expect(screen.getByText('No sagas found')).toBeInTheDocument());
  });

  it('clicking a saga row navigates to saga detail', async () => {
    mockNavigate.mockClear();
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Auth Rewrite.*COMPLETE.*NIU-500/i }));
    expect(mockNavigate).toHaveBeenCalled();
  });

  it('shows loading state initially', () => {
    const slowSvc = { getSagas: () => new Promise(() => undefined) };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: slowSvc })) });
    expect(screen.getByText(/Loading sagas/i)).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failingSvc = {
      getSagas: async () => {
        throw new Error('fetch error');
      },
    };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: failingSvc })) });
    await waitFor(() => expect(screen.getByText('fetch error')).toBeInTheDocument());
  });

  it('shows empty state when no sagas exist', async () => {
    const emptySvc = { getSagas: async (): Promise<Saga[]> => [] };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: emptySvc })) });
    await waitFor(() => expect(screen.getByText('No sagas found')).toBeInTheDocument());
  });

  it('shows export toast', async () => {
    const mockCreateObjectURL = vi.fn(() => 'blob:mock');
    const mockRevokeObjectURL = vi.fn();
    const anchorClickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);
    Object.defineProperty(URL, 'createObjectURL', { value: mockCreateObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: mockRevokeObjectURL, writable: true });

    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Export sagas as JSON/i }));
    await waitFor(() => expect(screen.getByText(/Exported \d+ sagas/i)).toBeInTheDocument());
    expect(anchorClickSpy).toHaveBeenCalled();
  });

  it('deletes the selected saga import', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const saga = makeSaga({ name: 'Imported Saga' });
    const deleteSaga = vi.fn().mockResolvedValue(undefined);
    const sagasSvc = {
      ...createMockTingService(),
      getSagas: async (): Promise<Saga[]> => [saga],
      getSaga: async (id: string): Promise<Saga | null> => (id === saga.id ? saga : null),
      deleteSaga,
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: sagasSvc })) });

    await waitFor(() => expect(screen.getAllByText('Imported Saga').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /delete selected saga import/i }));

    await waitFor(() => expect(deleteSaga).toHaveBeenCalledWith(saga.id));
    confirmSpy.mockRestore();
  });

  it('opens new saga modal', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Create new saga/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
  });

  it('sends the operator to Plan from the new saga modal', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Create new saga/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Go to Plan →' }));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/plan' });
  });

  it('adds repositories by typed reference when no repo catalog is available', async () => {
    render(<SagasPage />, {
      wrapper: wrap(withDefaults({ 'niuu.repos': { getRepos: async () => [] } })),
    });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));
    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    fireEvent.click(projectButton!);

    const dialog = screen.getByRole('dialog');
    const candidate = within(dialog).getByPlaceholderText('org/repo or https://host/org/repo.git');
    fireEvent.change(candidate, { target: { value: 'niuulabs/typed-repo' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'add' }));

    expect(within(dialog).getByText('niuulabs/typed-repo')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('branch-select-niuulabs/typed-repo'), {
      target: { value: 'release/1.0' },
    });
    expect(screen.getByTestId('branch-select-niuulabs/typed-repo')).toHaveValue('release/1.0');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove niuulabs/typed-repo' }));
    expect(within(dialog).queryByText('niuulabs/typed-repo')).not.toBeInTheDocument();
  });

  it('adds a typed repository on Enter without duplicating an existing one', async () => {
    render(<SagasPage />, {
      wrapper: wrap(withDefaults({ 'niuu.repos': { getRepos: async () => [] } })),
    });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));
    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    fireEvent.click(projectButton!);

    const dialog = screen.getByRole('dialog');
    const candidate = within(dialog).getByPlaceholderText('org/repo or https://host/org/repo.git');
    fireEvent.change(candidate, { target: { value: 'niuulabs/enter-repo' } });
    fireEvent.keyDown(candidate, { key: 'Enter' });
    expect(within(dialog).getAllByText('niuulabs/enter-repo')).toHaveLength(1);

    fireEvent.change(candidate, { target: { value: '   ' } });
    fireEvent.keyDown(candidate, { key: 'Enter' });
    expect(within(dialog).getAllByText('niuulabs/enter-repo')).toHaveLength(1);
  });

  it('opens tracker import modal', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));
    expect(screen.getByTestId('repo-select')).toBeInTheDocument();
    expect(screen.queryByTestId('branch-select')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import saga' })).toBeDisabled();
  });

  it('imports a tracker project', async () => {
    const tracker = createMockTrackerService();
    const importProject = vi.fn(tracker.importProject.bind(tracker));
    const trackerSvc: ITrackerBrowserService = {
      ...tracker,
      importProject,
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ 'ting.tracker': trackerSvc })) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));

    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    expect(projectButton).toBeDefined();
    fireEvent.click(projectButton!);
    fireEvent.change(screen.getByTestId('repo-select'), {
      target: { value: 'niuulabs/volundr' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import saga' }));

    await waitFor(() =>
      expect(importProject).toHaveBeenCalledWith(
        'proj-niuu-core',
        ['niuulabs/volundr'],
        'main',
        undefined,
        {
          repoRefs: [{ repo: 'niuulabs/volundr', branch: 'main' }],
          target: { mode: 'default' },
        },
      ),
    );
  });

  it('does not treat completed sagas as already imported in the tracker modal', async () => {
    const completedSaga = makeSaga({
      id: '00000000-0000-0000-0000-000000000222',
      name: 'Completed Niuu Core',
      trackerId: 'proj-niuu-core',
      status: 'complete',
      phaseSummary: { total: 3, completed: 3 },
    });
    const sagasSvc = {
      ...createMockTingService(),
      getSagas: async (): Promise<Saga[]> => [completedSaga],
      getSaga: async (id: string): Promise<Saga | null> =>
        id === completedSaga.id ? completedSaga : null,
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: sagasSvc })) });
    await waitFor(() =>
      expect(screen.getAllByText('Completed Niuu Core').length).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));

    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    expect(projectButton).toBeDefined();
    fireEvent.click(projectButton!);

    expect(screen.queryByText('imported')).not.toBeInTheDocument();
    expect(
      screen.queryByText('This tracker project is already imported into Ting.'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/already exists in Ting/i)).not.toBeInTheDocument();
  });

  it('blocks tracker import when a saga with the same slug already exists', async () => {
    const conflictingSaga = makeSaga({
      id: '00000000-0000-0000-0000-000000000333',
      trackerId: 'other-project',
      slug: 'service-boundary-restoration-and-api-consolidation',
      name: 'Existing conflicting saga',
    });
    const sagasSvc = {
      ...createMockTingService(),
      getSagas: async (): Promise<Saga[]> => [conflictingSaga],
      getSaga: async (id: string): Promise<Saga | null> =>
        id === conflictingSaga.id ? conflictingSaga : null,
    };
    const trackerSvc: ITrackerBrowserService = {
      ...createMockTrackerService(),
      listProjects: async () => [
        {
          id: 'proj-conflict',
          name: 'Service Boundary Restoration and API Consolidation',
          description: 'Conflicting slug project',
          status: 'active',
          url: 'https://linear.app/niuu/project/proj-conflict',
          milestoneCount: 2,
          issueCount: 5,
          slug: 'service-boundary-restoration-and-api-consolidation',
        },
      ],
    };

    render(<SagasPage />, {
      wrapper: wrap(withDefaults({ ting: sagasSvc, 'ting.tracker': trackerSvc })),
    });
    await waitFor(() =>
      expect(screen.getAllByText('Existing conflicting saga').length).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    const [projectTitle] = await screen.findAllByText(
      'Service Boundary Restoration and API Consolidation',
    );
    const projectButton = projectTitle.closest('button');
    expect(projectButton).not.toBeNull();
    fireEvent.click(projectButton!);

    expect(screen.getByText(/already exists in Ting/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import saga' })).toBeDisabled();
  });

  it('does not list terminal tracker projects in the import modal', async () => {
    const tracker = createMockTrackerService();
    const trackerSvc: ITrackerBrowserService = {
      ...tracker,
      listProjects: async () => [
        {
          id: 'proj-active',
          name: 'Active Project',
          description: 'Still in progress',
          status: 'active',
          url: 'https://linear.app/niuu/project/active',
          milestoneCount: 1,
          issueCount: 3,
        },
        {
          id: 'proj-done',
          name: 'Done Project',
          description: 'Already complete',
          status: 'completed',
          url: 'https://linear.app/niuu/project/done',
          milestoneCount: 1,
          issueCount: 3,
        },
      ],
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ 'ting.tracker': trackerSvc })) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Active Project').length).toBeGreaterThan(0));
    expect(screen.queryByText('Done Project')).not.toBeInTheDocument();
  });

  it('imports a tracker project with a selected workflow, version and instance target', async () => {
    const tracker = createMockTrackerService();
    const importProject = vi.fn(tracker.importProject.bind(tracker));
    const trackerSvc: ITrackerBrowserService = { ...tracker, importProject };
    const dispatchBus = {
      getClusters: vi.fn(async () => [
        { instanceId: 'inst-1', connectionId: 'conn-1', name: 'Valhalla', tags: ['gpu'] },
      ]),
    };

    render(<SagasPage />, {
      wrapper: wrap(withDefaults({ 'ting.tracker': trackerSvc, 'ting.dispatch': dispatchBus })),
    });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));

    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    fireEvent.click(projectButton!);
    fireEvent.change(screen.getByTestId('repo-select'), {
      target: { value: 'niuulabs/volundr' },
    });

    const workflowSelect = screen.getByDisplayValue('Use project default');
    fireEvent.change(workflowSelect, {
      target: { value: '00000000-0000-0000-0000-000000000a01' },
    });
    const versionSelect = await screen.findByDisplayValue('1.4.2 · current');
    await waitFor(() => expect((versionSelect as HTMLSelectElement).disabled).toBe(false));
    fireEvent.change(versionSelect, { target: { value: '1.4.2' } });

    const targetGroup = screen.getByRole('group', { name: 'Saga target routing mode' });
    fireEvent.click(within(targetGroup).getByRole('button', { name: 'Instance' }));
    await waitFor(() => expect(screen.getByText('Select a Volundr instance')).toBeInTheDocument());
    fireEvent.change(screen.getByDisplayValue('Select a Volundr instance'), {
      target: { value: 'inst-1' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Import saga' }));

    await waitFor(() =>
      expect(importProject).toHaveBeenCalledWith(
        'proj-niuu-core',
        ['niuulabs/volundr'],
        'main',
        'inst-1',
        expect.objectContaining({
          repoRefs: [{ repo: 'niuulabs/volundr', branch: 'main' }],
          workflowId: '00000000-0000-0000-0000-000000000a01',
          workflowVersion: '1.4.2',
          target: { mode: 'instance', instanceId: 'inst-1' },
        }),
      ),
    );
  });

  it('routes an import to matching Volundr tags', async () => {
    const tracker = createMockTrackerService();
    const importProject = vi.fn(tracker.importProject.bind(tracker));
    const trackerSvc: ITrackerBrowserService = { ...tracker, importProject };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ 'ting.tracker': trackerSvc })) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));

    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    fireEvent.click(projectButton!);
    fireEvent.change(screen.getByTestId('repo-select'), {
      target: { value: 'niuulabs/volundr' },
    });

    const targetGroup = screen.getByRole('group', { name: 'Saga target routing mode' });
    fireEvent.click(within(targetGroup).getByRole('button', { name: 'Tags' }));
    fireEvent.change(screen.getByPlaceholderText('gpu, valhalla'), {
      target: { value: 'gpu, valhalla' },
    });
    fireEvent.change(screen.getByLabelText('Target tag match mode'), {
      target: { value: 'any' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Import saga' }));

    await waitFor(() =>
      expect(importProject).toHaveBeenCalledWith(
        'proj-niuu-core',
        ['niuulabs/volundr'],
        'main',
        undefined,
        {
          repoRefs: [{ repo: 'niuulabs/volundr', branch: 'main' }],
          target: { mode: 'tags', tags: ['gpu', 'valhalla'], match: 'any' },
        },
      ),
    );
  });

  it('warns that the tracker source will be suffixed onto an existing saga name', async () => {
    const collidingSaga = makeSaga({
      id: '00000000-0000-0000-0000-000000000444',
      trackerId: 'other-project',
      trackerConnectionId: 'linear-legacy',
      slug: 'niuu-core',
      name: 'Legacy Niuu Core',
    });
    const sagasSvc = {
      ...createMockTingService(),
      getSagas: async (): Promise<Saga[]> => [collidingSaga],
      getSaga: async (id: string): Promise<Saga | null> =>
        id === collidingSaga.id ? collidingSaga : null,
    };
    const trackerSvc: ITrackerBrowserService = {
      ...createMockTrackerService(),
      listProjects: async () => [
        {
          id: 'proj-niuu-core-2',
          trackerConnectionId: 'linear-current',
          name: 'Niuu Core',
          description: 'Second connection',
          status: 'active',
          url: 'https://linear.app/niuu/project/proj-niuu-core-2',
          milestoneCount: 1,
          issueCount: 2,
          slug: 'niuu-core',
        },
      ],
    };

    render(<SagasPage />, {
      wrapper: wrap(withDefaults({ ting: sagasSvc, 'ting.tracker': trackerSvc })),
    });
    await waitFor(() => expect(screen.getAllByText('Legacy Niuu Core').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    const [projectTitle] = await screen.findAllByText('Niuu Core');
    fireEvent.click(projectTitle.closest('button')!);

    expect(
      screen.getByText(/already exists; Ting will add the tracker source/i),
    ).toBeInTheDocument();
  });

  it('adds and removes extra repositories for a multi-repo import', async () => {
    const reposSvc = {
      getRepos: async () => [
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'volundr',
          cloneUrl: 'https://github.com/niuulabs/volundr.git',
          url: 'https://github.com/niuulabs/volundr',
          defaultBranch: 'main',
          branches: ['main'],
        },
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'niuu',
          cloneUrl: 'https://github.com/niuulabs/niuu.git',
          url: 'https://github.com/niuulabs/niuu',
          defaultBranch: 'main',
          branches: ['main', 'develop'],
        },
      ],
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ 'niuu.repos': reposSvc })) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Niuu Core').length).toBeGreaterThan(0));
    const projectButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes('Niuu Core'));
    fireEvent.click(projectButton!);

    const dialog = screen.getByRole('dialog');
    fireEvent.change(screen.getByTestId('repo-select'), {
      target: { value: 'niuulabs/volundr' },
    });
    expect(within(dialog).getByText('niuulabs/volundr')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('repo-select'), {
      target: { value: 'niuulabs/niuu' },
    });
    expect(await within(dialog).findByText('niuulabs/niuu')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('branch-select-niuulabs/niuu'), {
      target: { value: 'develop' },
    });
    expect(screen.getByTestId('branch-select-niuulabs/niuu')).toHaveValue('develop');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove niuulabs/volundr' }));
    expect(
      within(dialog).queryByRole('button', { name: 'Remove niuulabs/volundr' }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: 'Remove niuulabs/niuu' }),
    ).toBeInTheDocument();
  });

  it('renders grouped bucket items from mixed data', async () => {
    const mixedSvc = {
      getSagas: async (): Promise<Saga[]> => [
        makeSaga({
          id: '1',
          name: 'Active',
          createdAt: new Date().toISOString(),
          phaseSummary: { total: 2, completed: 0 },
        }),
        makeSaga({ id: '2', name: 'Review', phaseSummary: { total: 4, completed: 2 } }),
        makeSaga({
          id: '3',
          name: 'Done',
          status: 'complete',
          slug: 'done',
          phaseSummary: { total: 4, completed: 4 },
        }),
        makeSaga({ id: '4', name: 'Broken', status: 'failed', slug: 'broken' }),
      ],
    };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ ting: mixedSvc })) });
    await waitFor(() => expect(screen.getAllByText('Active').length).toBeGreaterThan(0));
    expect(screen.getAllByText('Review').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Done').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Broken').length).toBeGreaterThan(0);
    expect(screen.getAllByText('0/2 runs').length).toBeGreaterThan(0);
    expect(screen.getAllByText('feat/test').length).toBeGreaterThan(0);
  });
});

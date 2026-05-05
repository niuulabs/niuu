import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ToastProvider } from '@niuulabs/ui';
import { SagasPage } from './SagasPage';
import { createMockTyrService, createMockTrackerService } from '../adapters/mock';
import type { Saga } from '../domain/saga';
import type { ITrackerBrowserService } from '../ports';

const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({}),
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
  const volundrRepos = createMockTyrService();
  return {
    tyr: volundrRepos,
    'tyr.tracker': createMockTrackerService(),
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
    const sagasSvc = {
      ...createMockTyrService(),
      getSagas: async (): Promise<Saga[]> => [
        makeSaga({
          id: '00000000-0000-0000-0000-000000000123',
          name: sagaName,
          trackerId,
        }),
      ],
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ tyr: sagasSvc })) });

    await waitFor(() => expect(screen.getAllByText(sagaName).length).toBeGreaterThan(0));
    const railButton = screen
      .getAllByRole('button')
      .find((button) => button.textContent?.includes(sagaName));
    expect(railButton).toBeDefined();
    expect(railButton).toHaveTextContent(sagaName);
    expect(railButton).toHaveTextContent(trackerId);
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
    fireEvent.click(screen.getByRole('button', { pressed: true }));
    expect(mockNavigate).toHaveBeenCalled();
  });

  it('shows loading state initially', () => {
    const slowSvc = { getSagas: () => new Promise(() => undefined) };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ tyr: slowSvc })) });
    expect(screen.getByText(/Loading sagas/i)).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failingSvc = {
      getSagas: async () => {
        throw new Error('fetch error');
      },
    };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ tyr: failingSvc })) });
    await waitFor(() => expect(screen.getByText('fetch error')).toBeInTheDocument());
  });

  it('shows empty state when no sagas exist', async () => {
    const emptySvc = { getSagas: async (): Promise<Saga[]> => [] };
    render(<SagasPage />, { wrapper: wrap(withDefaults({ tyr: emptySvc })) });
    await waitFor(() => expect(screen.getByText('No sagas found')).toBeInTheDocument());
  });

  it('shows export toast', async () => {
    const mockCreateObjectURL = vi.fn(() => 'blob:mock');
    const mockRevokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: mockCreateObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: mockRevokeObjectURL, writable: true });

    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Export sagas as JSON/i }));
    await waitFor(() => expect(screen.getByText(/Exported \d+ sagas/i)).toBeInTheDocument());
  });

  it('opens new saga modal', async () => {
    render(<SagasPage />, { wrapper: wrap(withDefaults({})) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Create new saga/i }));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
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

    render(<SagasPage />, { wrapper: wrap(withDefaults({ 'tyr.tracker': trackerSvc })) });
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
      expect(importProject).toHaveBeenCalledWith('proj-niuu-core', ['niuulabs/volundr'], 'main'),
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
      ...createMockTyrService(),
      getSagas: async (): Promise<Saga[]> => [completedSaga],
      getSaga: async (id: string): Promise<Saga | null> =>
        id === completedSaga.id ? completedSaga : null,
    };

    render(<SagasPage />, { wrapper: wrap(withDefaults({ tyr: sagasSvc })) });
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
      screen.queryByText('This tracker project is already imported into Tyr.'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/already exists in Tyr/i)).not.toBeInTheDocument();
  });

  it('blocks tracker import when a saga with the same slug already exists', async () => {
    const conflictingSaga = makeSaga({
      id: '00000000-0000-0000-0000-000000000333',
      trackerId: 'other-project',
      slug: 'service-boundary-restoration-and-api-consolidation',
      name: 'Existing conflicting saga',
    });
    const sagasSvc = {
      ...createMockTyrService(),
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
      wrapper: wrap(withDefaults({ tyr: sagasSvc, 'tyr.tracker': trackerSvc })),
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

    expect(screen.getByText(/already exists in Tyr/i)).toBeInTheDocument();
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

    render(<SagasPage />, { wrapper: wrap(withDefaults({ 'tyr.tracker': trackerSvc })) });
    await waitFor(() => expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /Import saga from tracker/i }));
    await waitFor(() => expect(screen.getByText('Import From Tracker')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText('Active Project').length).toBeGreaterThan(0));
    expect(screen.queryByText('Done Project')).not.toBeInTheDocument();
  });

  it('renders grouped bucket items from mixed data', async () => {
    const mixedSvc = {
      getSagas: async (): Promise<Saga[]> => [
        makeSaga({ id: '1', name: 'Active', phaseSummary: { total: 2, completed: 0 } }),
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
    render(<SagasPage />, { wrapper: wrap(withDefaults({ tyr: mixedSvc })) });
    await waitFor(() => expect(screen.getAllByText('Active').length).toBeGreaterThan(0));
    expect(screen.getAllByText('Review').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Done').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Broken').length).toBeGreaterThan(0);
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { SagaDetailPage, SagaDetailRoute } from './SagaDetailPage';
import { createMockTingService } from '../adapters/mock';
import type { Saga, Phase, Run } from '../domain/saga';
import type { Workflow } from '../domain/workflow';
import type { RunSessionMessage } from '../ports';

const mockNavigate = vi.fn();
const mockUseParams = vi.fn().mockReturnValue({ sagaId: '00000000-0000-0000-0000-000000000001' });
const mockDispatchBus = {
  getClusters: vi.fn(async () => []),
};

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => mockUseParams(),
}));

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const defaults = {
    'niuu.repos': {
      getRepos: async () => [
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'volundr',
          cloneUrl: 'https://github.com/niuulabs/volundr.git',
          url: 'https://github.com/niuulabs/volundr',
          defaultBranch: 'main',
          branches: ['main', 'dev'],
        },
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'infrastructure',
          cloneUrl: 'https://github.com/niuulabs/infrastructure.git',
          url: 'https://github.com/niuulabs/infrastructure',
          defaultBranch: 'main',
          branches: ['main'],
        },
      ],
    },
    ...services,
  };
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={defaults}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const SAGA_ID = '00000000-0000-0000-0000-000000000001';

function makeSaga(overrides: Partial<Saga> = {}): Saga {
  return {
    id: SAGA_ID,
    trackerId: 'NIU-500',
    trackerType: 'linear',
    slug: 'auth-rewrite',
    name: 'Auth Rewrite',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/auth-rewrite',
    baseBranch: 'main',
    status: 'active',
    confidence: 82,
    createdAt: '2026-01-10T09:00:00Z',
    phaseSummary: { total: 3, completed: 1 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
    ...overrides,
  };
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: '00000000-0000-0000-0000-000000000010',
    phaseId: '00000000-0000-0000-0000-000000000100',
    trackerId: 'NIU-501',
    name: 'Implement OIDC flow',
    description: 'Add OIDC login.',
    acceptanceCriteria: ['Users can log in'],
    declaredFiles: ['src/auth/oidc.ts'],
    estimateHours: 8,
    status: 'merged',
    confidence: 90,
    sessionId: 'sess-001',
    reviewerSessionId: null,
    reviewRound: 1,
    branch: 'feat/auth-rewrite',
    chronicleSummary: 'OIDC flow implemented.',
    retryCount: 0,
    createdAt: '2026-01-10T09:00:00Z',
    updatedAt: '2026-01-12T14:00:00Z',
    ...overrides,
  };
}

function makePhase(runs: Run[] = []): Phase {
  return {
    id: '00000000-0000-0000-0000-000000000100',
    sagaId: SAGA_ID,
    trackerId: 'NIU-M1',
    number: 1,
    name: 'Plan',
    status: 'complete',
    confidence: 90,
    runs,
  };
}

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: '00000000-0000-0000-0000-0000000000aa',
    name: 'Ship Workflow',
    version: '1.4.2',
    nodes: [
      {
        id: 'stage-1',
        kind: 'stage',
        label: 'Build',
        runId: null,
        personaIds: [],
        position: { x: 0, y: 0 },
      },
    ],
    edges: [],
    ...overrides,
  };
}

describe('SagaDetailPage', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    mockDispatchBus.getClusters.mockClear();
  });

  it('shows loading state initially', () => {
    const slowSvc = {
      getSaga: () => new Promise(() => undefined),
      getPhases: () => new Promise(() => undefined),
    };
    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: slowSvc, 'ting.dispatch': mockDispatchBus }),
    });
    expect(screen.getByText(/Loading saga/i)).toBeInTheDocument();
  });

  it('renders the compact saga header', async () => {
    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() => expect(screen.getByText('NIU-500')).toBeInTheDocument());
    expect(screen.getByText('Auth Rewrite')).toBeInTheDocument();
    expect(screen.getByText('feat/auth-rewrite → main')).toBeInTheDocument();
  });

  it('renders phase cards and run rows', async () => {
    const svc = {
      getSaga: async () => makeSaga(),
      getPhases: async () => [makePhase([makeRun()])],
    };
    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: svc, 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() => expect(screen.getByText('Phase 1 · Plan')).toBeInTheDocument());
    expect(screen.getByText('NIU-501')).toBeInTheDocument();
    expect(screen.getByText('Implement OIDC flow')).toBeInTheDocument();
  });

  it('links tracker-backed saga and run labels without showing internal UUIDs', async () => {
    const sagaTrackerId = '4dfa3eab-c00f-46e8-bdc3-c17f8a184f39';
    const runTrackerId = '09436690-32d6-4a44-90e0-a88ca5477281';
    const svc = {
      getSaga: async () =>
        makeSaga({
          trackerId: sagaTrackerId,
          url: 'https://linear.app/niuu/project/ui-proof',
        }),
      getPhases: async () => [
        makePhase([
          makeRun({
            trackerId: runTrackerId,
            identifier: 'NIU-777',
            url: 'https://linear.app/niuu/issue/NIU-777/document-proof',
          }),
        ]),
      ],
    };

    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: svc, 'ting.dispatch': mockDispatchBus }),
    });

    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'Open in Tracker' })).toHaveAttribute(
        'href',
        'https://linear.app/niuu/project/ui-proof',
      ),
    );
    expect(screen.getByRole('link', { name: 'NIU-777' })).toHaveAttribute(
      'href',
      'https://linear.app/niuu/issue/NIU-777/document-proof',
    );
    expect(screen.queryByText(sagaTrackerId)).not.toBeInTheDocument();
    expect(screen.queryByText(runTrackerId)).not.toBeInTheDocument();
  });

  it('renders workflow, stage progress, and confidence cards', async () => {
    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() =>
      expect(screen.getByRole('region', { name: /workflow/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('region', { name: /stage progress/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /confidence signals/i })).toBeInTheDocument();
  });

  it('shows empty state when saga has no phases', async () => {
    const svc = {
      getSaga: async () => makeSaga(),
      getPhases: async (): Promise<Phase[]> => [],
    };
    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: svc, 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() => expect(screen.getByText('No phases yet')).toBeInTheDocument());
  });

  it('shows error when saga not found', async () => {
    const svc = {
      getSaga: async (): Promise<Saga | null> => null,
      getPhases: async (): Promise<Phase[]> => [],
    };
    render(<SagaDetailPage sagaId="nonexistent-id" />, {
      wrapper: wrap({ ting: svc, 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() =>
      expect(screen.getByText(/Saga "nonexistent-id" not found/)).toBeInTheDocument(),
    );
  });

  it('back button navigates to /ting/sagas', async () => {
    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() => expect(screen.getByText('NIU-500')).toBeInTheDocument());
    expect(screen.getByText('Auth Rewrite')).toBeInTheDocument();
    screen.getByRole('button', { name: /Sagas/i }).click();
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/sagas' });
  });

  it('opens the workflow modal and assigns a workflow', async () => {
    const user = userEvent.setup();
    const assignWorkflow = vi.fn(async () => makeSaga({ workflow: 'Ship Workflow' }));
    const tingService = {
      getSaga: async () => makeSaga({ workflowId: undefined, workflow: undefined }),
      getPhases: async () => [makePhase([makeRun()])],
      assignWorkflow,
    };
    const workflowService = {
      listWorkflows: async () => [makeWorkflow()],
      getWorkflow: async () => makeWorkflow(),
      saveWorkflow: async (workflow: Workflow) => workflow,
      deleteWorkflow: async () => {},
    };

    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({
        ting: tingService,
        'ting.workflows': workflowService,
        'ting.dispatch': mockDispatchBus,
      }),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Assign' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Assign' }));
    await waitFor(() => expect(screen.getByText('Assign workflow')).toBeInTheDocument());
    await user.click(screen.getByText('Ship Workflow'));
    await waitFor(() =>
      expect(assignWorkflow).toHaveBeenCalledWith(SAGA_ID, '00000000-0000-0000-0000-0000000000aa'),
    );
  });

  it('edits saga repositories with the shared repo dropdown', async () => {
    const user = userEvent.setup();
    const assignRepos = vi.fn(async () =>
      makeSaga({
        repos: ['niuulabs/volundr', 'niuulabs/infrastructure'],
        repoRefs: [
          { repo: 'niuulabs/volundr', branch: 'dev' },
          { repo: 'niuulabs/infrastructure', branch: 'main' },
        ],
        baseBranch: 'dev',
      }),
    );
    const svc = {
      getSaga: async () =>
        makeSaga({
          repoRefs: [{ repo: 'niuulabs/volundr', branch: 'dev' }],
          baseBranch: 'dev',
        }),
      getPhases: async () => [makePhase([makeRun()])],
      assignRepos,
    };

    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: svc, 'ting.dispatch': mockDispatchBus }),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit repos' })).toBeVisible());
    await user.click(screen.getByRole('button', { name: 'Edit repos' }));
    await user.selectOptions(screen.getByTestId('saga-repo-select'), 'niuulabs/infrastructure');
    await user.click(screen.getByRole('button', { name: 'Save repos' }));

    await waitFor(() =>
      expect(assignRepos).toHaveBeenCalledWith(SAGA_ID, [
        { repo: 'niuulabs/volundr', branch: 'dev' },
        { repo: 'niuulabs/infrastructure', branch: 'main' },
      ]),
    );
  });

  it('edits saga repositories when the repo catalog is empty', async () => {
    const user = userEvent.setup();
    const assignRepos = vi.fn(async () =>
      makeSaga({
        repos: ['custom/device-operator', 'custom/protocol-tests'],
        repoRefs: [
          { repo: 'custom/device-operator', branch: 'main' },
          { repo: 'custom/protocol-tests', branch: 'release' },
        ],
      }),
    );
    const svc = {
      getSaga: async () =>
        makeSaga({
          repos: ['custom/device-operator'],
          repoRefs: undefined,
          baseBranch: '',
        }),
      getPhases: async () => [makePhase([makeRun()])],
      assignRepos,
    };

    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({
        ting: svc,
        'ting.dispatch': mockDispatchBus,
        'niuu.repos': { getRepos: async () => [] },
      }),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit repos' })).toBeVisible());
    await user.click(screen.getByRole('button', { name: 'Edit repos' }));
    await user.clear(screen.getByLabelText('Branch for custom/device-operator'));
    await user.type(screen.getByLabelText('Repository'), 'custom/protocol-tests');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    await user.clear(screen.getByLabelText('Branch for custom/protocol-tests'));
    await user.type(screen.getByLabelText('Branch for custom/protocol-tests'), 'release');
    await user.click(screen.getByRole('button', { name: 'Save repos' }));

    await waitFor(() =>
      expect(assignRepos).toHaveBeenCalledWith(SAGA_ID, [
        { repo: 'custom/device-operator', branch: 'main' },
        { repo: 'custom/protocol-tests', branch: 'release' },
      ]),
    );
  });

  it('shows pending feedback requests and sends a directed reply', async () => {
    const user = userEvent.setup();
    const listRunMessages = vi.fn(async (): Promise<RunSessionMessage[]> => [
      {
        id: 'msg-help-1',
        sessionId: 'session-council-1',
        content: '{"summary":"Need your call","reason":"needs_feedback"}',
        sender: 'help_needed',
        createdAt: '2026-05-11T12:00:00Z',
        kind: 'help_request',
        helpRequest: {
          summary: 'Need your call on the final recommendation.',
          reason: 'needs_feedback',
          attempted: ['Compared the top two options'],
          recommendation: 'Pick the rollout order.',
          context: { slug: 'research/council-human-v1' },
          targetPeerId: 'flock-council-chair',
          persona: 'council-chair',
        },
      },
    ]);
    const sendRunMessage = vi.fn(async (): Promise<RunSessionMessage> => ({
      id: 'msg-user-1',
      sessionId: 'session-council-1',
      content: 'Please prefer the staged rollout option.',
      sender: 'user',
      createdAt: '2026-05-11T12:05:00Z',
      kind: 'message',
      helpRequest: null,
    }));

    const svc = {
      getSaga: async () => makeSaga(),
      getPhases: async () => [
        makePhase([
          makeRun({
            trackerId: 'NIU-777',
            name: 'Research Council',
            status: 'escalated',
            sessionId: 'session-council-1',
          }),
        ]),
      ],
      listRunMessages,
      sendRunMessage,
    };

    render(<SagaDetailPage sagaId={SAGA_ID} />, {
      wrapper: wrap({ ting: svc, 'ting.dispatch': mockDispatchBus }),
    });

    await waitFor(() => expect(screen.getByText('Human input requests')).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByText(/Need your call on the final recommendation/)).toBeInTheDocument(),
    );

    await user.type(
      screen.getByLabelText('Your feedback'),
      'Please prefer the staged rollout option.',
    );
    await user.click(screen.getByRole('button', { name: 'Send feedback' }));

    await waitFor(() =>
      expect(sendRunMessage).toHaveBeenCalledWith(
        '00000000-0000-0000-0000-000000000010',
        'Please prefer the staged rollout option.',
        'flock-council-chair',
      ),
    );
  });
});

describe('SagaDetailRoute', () => {
  it('renders SagaDetailPage with sagaId from URL params', async () => {
    render(<SagaDetailRoute />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatch': mockDispatchBus }),
    });
    await waitFor(() => expect(screen.getByText('NIU-500')).toBeInTheDocument());
    expect(screen.getByText('Auth Rewrite')).toBeInTheDocument();
  });
});

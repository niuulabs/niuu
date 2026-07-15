import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavnDetail } from './RavnDetail';
import {
  createMockRavenStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';
import type { Ravn } from '../domain/ravn';

const SAMPLE_RAVN: Ravn = {
  id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  personaName: 'sindri',
  status: 'active',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:00:00Z',
  role: 'build',
  letter: 'S',
  summary: 'Writes and edits source code across the stack.',
  iterationBudget: 40,
  writeRouting: 'local',
  cascade: 'sequential',
  location: 'eu-west-1',
  deployment: 'production',
  mounts: [
    { name: 'codebase', role: 'primary' },
    { name: 'docs', role: 'ro' },
  ],
  mcpServers: ['filesystem', 'git', 'bash'],
  gatewayChannels: ['slack-dev', 'github-webhook'],
  eventSubscriptions: ['code.requested', 'bug.fix.requested', 'code.changed'],
};

const SAMPLE_RAVN_MINIMAL: Ravn = {
  id: 'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c',
  personaName: 'vör',
  status: 'idle',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-14T18:00:00Z',
};

const RESIDENT_CHAT_ENDPOINT = 'wss://skuld.example/s/resident-1/session';

const SAMPLE_RESIDENT: Ravn = {
  ...SAMPLE_RAVN,
  id: 'aa11bb22-cc33-4d44-8e55-ff6677889900',
  personaName: 'huginn',
  residentName: 'Huginn',
  peerId: 'peer-huginn-01',
  kind: 'resident',
  chatEndpoint: RESIDENT_CHAT_ENDPOINT,
  sessionId: '0f8e7d6c-5b4a-4392-8170-6e5d4c3b2a19',
};

const MANAGED_HERMES: Ravn = {
  ...SAMPLE_RESIDENT,
  managed: true,
  backend: 'openshell',
  engine: 'hermes',
  profileId: 'nemohermes-openshell',
  desiredState: 'running',
  observedState: 'active',
  instanceId: 'target-1',
  instanceName: 'Compute target',
  capabilities: [
    'chat',
    'session.list',
    'session.create',
    'session.delete',
    'runtime.restart',
    'logs',
  ],
  conditions: [
    {
      type: 'Ready',
      status: 'true',
      reason: 'EngineReady',
      message: '',
      lastTransitionAt: '2026-04-15T09:01:00Z',
    },
  ],
};

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.budget': createMockBudgetStream(),
    bifrost: { listModels: vi.fn().mockResolvedValue([]) },
    'ravn.residents': {
      listProfiles: vi.fn().mockResolvedValue([]),
      deploy: vi.fn(),
      applyLifecycle: vi.fn(),
      delete: vi.fn(),
      getLogs: vi.fn(),
      listSessions: vi.fn().mockResolvedValue([]),
      createSession: vi.fn(),
      deleteSession: vi.fn(),
    },
    ...overrides,
  };
}

function wrap(services = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  localStorage.clear();
});

// ── Core rendering ───────────────────────────────────────────────────────────

describe('RavnDetail', () => {
  it('renders the ravn detail pane', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('ravn-detail')).toBeInTheDocument();
  });

  it('shows the persona name in the header', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getAllByText('sindri').length).toBeGreaterThan(0);
  });

  it('renders the tab nav with 5 tabs', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('ravn-sectabs')).toBeInTheDocument();
    for (const id of ['overview', 'triggers', 'activity', 'sessions', 'connectivity']) {
      expect(screen.getByTestId(`sectab-${id}`)).toBeInTheDocument();
    }
  });

  it('shows overview tab active by default', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const overviewTab = screen.getByTestId('sectab-overview');
    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows overview content by default', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('section-body-overview')).toBeInTheDocument();
  });

  it('switches tab when a tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const triggersTab = screen.getByTestId('sectab-triggers');
    fireEvent.click(triggersTab);
    expect(triggersTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('triggers-section-body')).toBeInTheDocument();
  });

  it('moves between detail tabs with arrow keys', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const overview = screen.getByTestId('sectab-overview');

    fireEvent.keyDown(overview, { key: 'ArrowRight' });
    expect(screen.getByTestId('sectab-triggers')).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(screen.getByTestId('sectab-triggers'), { key: 'ArrowLeft' });
    expect(overview).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(overview, { key: 'Enter' });
    expect(overview).toHaveAttribute('aria-selected', 'true');
  });

  it('persists active tab to localStorage', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const sessionsTab = screen.getByTestId('sectab-sessions');
    fireEvent.click(sessionsTab);
    const stored = localStorage.getItem('ravn.detail.tab');
    expect(stored).toBe('"sessions"');
  });

  it('restores active tab from localStorage', () => {
    localStorage.setItem('ravn.detail.tab', '"connectivity"');
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const connectivityTab = screen.getByTestId('sectab-connectivity');
    expect(connectivityTab).toHaveAttribute('aria-selected', 'true');
  });

  it('recovers from a stale persisted tab', () => {
    localStorage.setItem('ravn.detail.tab', '"removed-chat-tab"');
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('sectab-overview')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('section-body-overview')).toBeInTheDocument();
  });

  it('shows close button when onClose is provided', () => {
    const handleClose = vi.fn();
    render(<RavnDetail ravn={SAMPLE_RAVN} onClose={handleClose} />, { wrapper: wrap() });
    const btn = screen.getByTestId('detail-close-btn');
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(handleClose).toHaveBeenCalled();
  });

  it('does not show close button when onClose is not provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.queryByTestId('detail-close-btn')).not.toBeInTheDocument();
  });
});

describe('RavnDetail — resident controls', () => {
  it('shows the managed runtime contract and advertised lifecycle controls', () => {
    render(<RavnDetail ravn={MANAGED_HERMES} />, { wrapper: wrap() });

    expect(screen.getByTestId('resident-contract-panel')).toBeInTheDocument();
    expect(screen.getByText('nemohermes-openshell')).toBeInTheDocument();
    expect(screen.getByText('Compute target')).toBeInTheDocument();
    expect(screen.getByTestId('resident-restart')).toBeInTheDocument();
    expect(screen.queryByTestId('resident-suspend')).not.toBeInTheDocument();
    expect(screen.getByText('EngineReady')).toBeInTheDocument();
  });

  it('uses the lifecycle command port and never renders the old pause control', async () => {
    const applyLifecycle = vi.fn().mockResolvedValue(MANAGED_HERMES);
    render(<RavnDetail ravn={MANAGED_HERMES} />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            applyLifecycle,
          },
        }),
      ),
    });

    fireEvent.click(screen.getByTestId('resident-restart'));
    await waitFor(() => expect(applyLifecycle).toHaveBeenCalledWith(MANAGED_HERMES, 'restart'));
    expect(screen.queryByText(/^pause$/i)).not.toBeInTheDocument();
  });

  it('shows suspend only when the capability is advertised', () => {
    const helmResident: Ravn = {
      ...MANAGED_HERMES,
      backend: 'helmrelease',
      engine: 'ravn',
      capabilities: ['chat', 'runtime.restart', 'runtime.suspend'],
    };
    const { rerender } = render(<RavnDetail ravn={helmResident} />, { wrapper: wrap() });
    expect(screen.getByTestId('resident-suspend')).toBeInTheDocument();

    rerender(
      <RavnDetail
        ravn={{ ...helmResident, backend: 'openshell', capabilities: ['chat', 'runtime.restart'] }}
      />,
    );
    expect(screen.queryByTestId('resident-suspend')).not.toBeInTheDocument();
  });

  it('offers resume, but not restart or suspend, for a suspended resident', async () => {
    const applyLifecycle = vi.fn().mockResolvedValue(MANAGED_HERMES);
    const suspended: Ravn = {
      ...MANAGED_HERMES,
      desiredState: 'suspended',
      observedState: 'suspended',
      capabilities: ['chat', 'runtime.restart', 'runtime.suspend'],
    };
    render(<RavnDetail ravn={suspended} />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            applyLifecycle,
          },
        }),
      ),
    });

    expect(screen.queryByTestId('resident-restart')).not.toBeInTheDocument();
    expect(screen.queryByTestId('resident-suspend')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('resident-resume'));

    await waitFor(() => expect(applyLifecycle).toHaveBeenCalledWith(suspended, 'resume'));
  });

  it('allows failed runtimes to restart and suppresses destructive controls while deleting', () => {
    const failed: Ravn = { ...MANAGED_HERMES, status: 'failed', observedState: 'failed' };
    const { rerender } = render(<RavnDetail ravn={failed} />, { wrapper: wrap() });
    expect(screen.getByTestId('resident-restart')).toBeInTheDocument();

    rerender(<RavnDetail ravn={{ ...failed, observedState: 'deleting' }} />);
    expect(screen.queryByTestId('resident-restart')).not.toBeInTheDocument();
    expect(screen.queryByTestId('resident-delete-open')).not.toBeInTheDocument();
  });

  it('renders a sparse managed identity without exposing unsupported observability', () => {
    const sparse: Ravn = {
      id: '12345678-1234-4234-8234-123456789012',
      personaName: '',
      status: 'idle',
      model: 'qwen3.5',
      createdAt: '2026-04-15T09:00:00Z',
      managed: true,
      observedState: 'active',
      capabilities: ['metrics'],
      endpoints: [{ kind: 'chat', protocol: 'ws', url: 'wss://chat.example/session' }],
    };
    render(<RavnDetail ravn={sparse} />, { wrapper: wrap() });
    expect(screen.getByRole('heading', { name: '12345678' })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.queryByTestId('resident-observability-panel')).not.toBeInTheDocument();
  });

  it('keeps lifecycle and deletion failures visible in their owning surfaces', async () => {
    const applyLifecycle = vi.fn().mockRejectedValue(new Error('restart refused'));
    const deleteResident = vi.fn().mockRejectedValue(new Error('runtime still terminating'));
    render(<RavnDetail ravn={MANAGED_HERMES} />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            applyLifecycle,
            delete: deleteResident,
          },
        }),
      ),
    });

    fireEvent.click(screen.getByTestId('resident-restart'));
    await waitFor(() => expect(screen.getByText('restart refused')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('resident-delete-open'));
    fireEvent.click(screen.getByTestId('resident-delete-confirm'));
    await waitFor(() => expect(screen.getByText('runtime still terminating')).toBeInTheDocument());
    expect(screen.getByTestId('resident-delete-confirm')).toBeInTheDocument();
  });

  it('creates and closes a second native conversation through capabilities', async () => {
    const session = {
      id: '22222222-3333-4444-8555-666666666666',
      ravnId: MANAGED_HERMES.id,
      personaName: MANAGED_HERMES.personaName,
      status: 'running' as const,
      model: MANAGED_HERMES.model,
      createdAt: '2026-04-15T09:02:00Z',
      title: 'Second conversation',
    };
    const createSession = vi.fn().mockResolvedValue(session);
    const deleteSession = vi.fn().mockResolvedValue(undefined);
    render(<RavnDetail ravn={MANAGED_HERMES} />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            listSessions: vi.fn().mockResolvedValue([session]),
            createSession,
            deleteSession,
          },
        }),
      ),
    });

    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => expect(screen.getByText('Second conversation')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('resident-session-create-open'));
    fireEvent.change(screen.getByTestId('resident-session-title'), {
      target: { value: 'Another thread' },
    });
    fireEvent.click(screen.getByTestId('resident-session-create-submit'));
    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith(MANAGED_HERMES, {
        title: 'Another thread',
      }),
    );

    fireEvent.click(screen.getByTestId('resident-session-delete-open'));
    fireEvent.click(screen.getByTestId('resident-session-delete-confirm'));
    await waitFor(() => expect(deleteSession).toHaveBeenCalledWith(MANAGED_HERMES, session.id));
  });

  it('shows native conversation loading failures without falling back to aggregate sessions', async () => {
    const listSessions = vi.fn().mockRejectedValue(new Error('engine session API unavailable'));
    render(<RavnDetail ravn={MANAGED_HERMES} />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            listSessions,
          },
        }),
      ),
    });

    fireEvent.click(screen.getByTestId('sectab-sessions'));

    await waitFor(() =>
      expect(screen.getByText('engine session API unavailable')).toBeInTheDocument(),
    );
    expect(screen.queryByText('Implement login form')).not.toBeInTheDocument();
  });

  it('loads resident logs through the authenticated control port', async () => {
    const getLogs = vi.fn().mockResolvedValue({
      entries: [
        {
          timestampMs: 1_788_000_000_000,
          level: 'info',
          source: 'hermes',
          target: 'runtime',
          message: 'API server ready',
          fields: {},
        },
      ],
      bufferTotal: 1,
    });
    render(<RavnDetail ravn={MANAGED_HERMES} />, {
      wrapper: wrap(
        makeServices({
          'ravn.residents': {
            ...makeServices()['ravn.residents'],
            getLogs,
          },
        }),
      ),
    });

    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    fireEvent.click(screen.getByRole('button', { name: 'Logs' }));

    await waitFor(() => expect(screen.getByText('API server ready')).toBeInTheDocument());
    expect(getLogs).toHaveBeenCalledWith(MANAGED_HERMES);
  });

  it('gates metrics by capability and reports an empty authenticated log result', async () => {
    const metricsEndpoint = {
      kind: 'metrics',
      protocol: 'http',
      url: 'https://metrics.example/resident',
    } as const;
    const getLogs = vi.fn().mockResolvedValue({ entries: [], bufferTotal: 0 });
    const services = makeServices({
      'ravn.residents': {
        ...makeServices()['ravn.residents'],
        getLogs,
      },
    });
    const { rerender } = render(
      <RavnDetail ravn={{ ...MANAGED_HERMES, endpoints: [metricsEndpoint] }} />,
      { wrapper: wrap(services) },
    );

    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.queryByRole('link', { name: 'metrics' })).not.toBeInTheDocument();

    rerender(
      <RavnDetail
        ravn={{
          ...MANAGED_HERMES,
          capabilities: [...(MANAGED_HERMES.capabilities ?? []), 'metrics'],
          endpoints: [metricsEndpoint],
        }}
      />,
    );
    expect(screen.getByRole('link', { name: 'metrics' })).toHaveAttribute(
      'href',
      metricsEndpoint.url,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Logs' }));
    await waitFor(() => expect(screen.getByText('No log entries reported.')).toBeInTheDocument());
  });
});

// ── Overview tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Overview tab', () => {
  it('renders identity panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('identity-panel')).toBeInTheDocument();
  });

  it('renders runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('runtime-panel')).toBeInTheDocument();
  });

  it('shows persona name in identity panel', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getAllByText('sindri').length).toBeGreaterThan(0));
  });

  it('shows role badge in identity panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('build')).toBeInTheDocument();
  });

  it('shows summary text when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('Writes and edits source code across the stack.')).toBeInTheDocument();
  });

  it('renders state with StateDot in runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getAllByText('active').length).toBeGreaterThan(0);
  });

  it('shows model in runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('claude-sonnet-4-6')).toBeInTheDocument();
  });

  it('shows location when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    // Location appears in the hero subtitle (normalised: hyphens become spaces)
    expect(screen.getByText(/eu west 1/)).toBeInTheDocument();
  });

  it('shows deployment when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    // Deployment appears in the hero subtitle
    expect(screen.getByText(/production/)).toBeInTheDocument();
  });

  it('shows cascade when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('sequential')).toBeInTheDocument();
  });

  it('shows last activity in the operational summary', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('Last activity')).toBeInTheDocument();
  });

  it('shows write routing when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getAllByText('local').length).toBeGreaterThan(0);
  });

  it('renders mounts panel when mounts are provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('mounts-panel')).toBeInTheDocument();
  });

  it('does not render mounts panel when no mounts', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    expect(screen.queryByTestId('mounts-panel')).not.toBeInTheDocument();
  });

  it('renders without role/letter gracefully', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    expect(screen.getByTestId('ravn-detail')).toBeInTheDocument();
    expect(screen.queryByTestId('identity-panel')).toBeInTheDocument();
  });
});

// ── Triggers tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Triggers tab', () => {
  it('renders triggers section when triggers tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => expect(screen.getByTestId('triggers-section-body')).toBeInTheDocument());
  });

  it('renders trigger cards for this ravn', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const cards = screen.queryAllByTestId('trigger-card');
      expect(cards.length).toBeGreaterThan(0);
    });
  });

  it('shows trigger kind badge', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const kindBadges = screen.queryAllByTestId('trigger-kind');
      expect(kindBadges.length).toBeGreaterThan(0);
    });
  });

  it('shows trigger spec', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      expect(screen.getAllByText('/hooks/dispatch').length).toBeGreaterThan(0);
    });
  });

  it('shows last fired time when available', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const firedItems = screen.queryAllByTestId('trigger-last-fired');
      expect(firedItems.length).toBeGreaterThan(0);
    });
  });

  it('shows fire count when available', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const countItems = screen.queryAllByTestId('trigger-fire-count');
      expect(countItems.length).toBeGreaterThan(0);
      expect(countItems[0]?.textContent).toMatch(/\d+ fires/);
    });
  });

  it('renders toggle switch for each trigger', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const toggles = screen.queryAllByTestId('trigger-toggle');
      expect(toggles.length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no triggers match this ravn', async () => {
    render(<RavnDetail ravn={{ ...SAMPLE_RAVN, personaName: 'unknown-persona' }} />, {
      wrapper: wrap(),
    });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      expect(screen.getByText('No triggers configured')).toBeInTheDocument();
    });
  });

  it('shows trigger count badge on triggers tab when triggers exist', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    await waitFor(() => {
      const triggersTab = screen.getByTestId('sectab-triggers');
      expect(triggersTab.textContent).toMatch(/triggers/i);
    });
  });
});

// ── Activity tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Activity tab', () => {
  it('renders activity section when activity tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => expect(screen.getByTestId('activity-section-body')).toBeInTheDocument());
  });

  it('renders activity filter controls', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.getByTestId('activity-filter')).toBeInTheDocument();
      expect(screen.getByTestId('activity-filter-all')).toBeInTheDocument();
      expect(screen.getByTestId('activity-filter-user')).toBeInTheDocument();
      expect(screen.getByTestId('activity-filter-asst')).toBeInTheDocument();
    });
  });

  it('shows live indicator when ravn is active', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.getByTestId('activity-live')).toBeInTheDocument();
    });
  });

  it('does not show live indicator when ravn is idle', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.queryByTestId('activity-live')).not.toBeInTheDocument();
    });
  });

  it('renders messages with kind badges when sessions exist', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      const badges = screen.queryAllByTestId('activity-kind-badge');
      expect(badges.length).toBeGreaterThan(0);
    });
  });

  it('filters messages by kind when filter is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      // Wait for messages to load
      expect(screen.queryAllByTestId('activity-message').length).toBeGreaterThan(0);
    });

    const userFilter = screen.getByTestId('activity-filter-user');
    fireEvent.click(userFilter);

    await waitFor(() => {
      // After filtering to 'user' only, all visible kind badges should be 'user'
      const badges = screen.queryAllByTestId('activity-kind-badge');
      badges.forEach((badge) => {
        expect(badge.textContent).toBe('user');
      });
    });
  });

  it('shows empty state when ravn has no sessions', async () => {
    const ravnNoSessions: Ravn = {
      ...SAMPLE_RAVN,
      id: 'zzzzzzzz-0000-4000-8000-000000000000',
    };
    render(<RavnDetail ravn={ravnNoSessions} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.getByText('No activity for this ravn')).toBeInTheDocument();
    });
  });
});

// ── Sessions tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Sessions tab', () => {
  it('renders sessions section when sessions tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => expect(screen.getByTestId('sessions-section-body')).toBeInTheDocument());
  });

  it('renders session cards', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      expect(screen.queryAllByTestId('session-card').length).toBeGreaterThan(0);
    });
  });

  it('shows session title in card', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      expect(screen.getByText('Implement login form')).toBeInTheDocument();
    });
  });

  it('shows session message count in metrics', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      const countEls = screen.queryAllByTestId('session-message-count');
      expect(countEls.length).toBeGreaterThan(0);
      expect(countEls[0]?.textContent).toMatch(/\d+ msgs/);
    });
  });

  it('shows session cost in metrics', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      const costEls = screen.queryAllByTestId('session-cost');
      expect(costEls.length).toBeGreaterThan(0);
      expect(costEls[0]?.textContent).toMatch(/\$\d+\.\d{2}/);
    });
  });

  it('dispatches ravn:session-selected event when session card is clicked', async () => {
    const handler = vi.fn();
    window.addEventListener('ravn:session-selected', handler);
    window.history.replaceState(null, '', '/ravn/ravens');

    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));

    await waitFor(() => {
      const cards = screen.queryAllByTestId('session-card');
      expect(cards.length).toBeGreaterThan(0);
    });

    const card = screen.queryAllByTestId('session-card')[0];
    if (card) fireEvent.click(card);

    expect(handler).toHaveBeenCalled();
    expect(localStorage.getItem('ravn.session')).toBeTruthy();
    expect(window.location.pathname).toBe('/ravn/sessions');
    expect(window.location.search).toContain('session=');
    window.removeEventListener('ravn:session-selected', handler);
  });

  it('shows empty state when no sessions exist', async () => {
    const ravnNoSessions: Ravn = {
      ...SAMPLE_RAVN,
      id: 'zzzzzzzz-0000-4000-8000-000000000000',
    };
    render(<RavnDetail ravn={ravnNoSessions} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      expect(screen.getByText('No sessions')).toBeInTheDocument();
    });
  });
});

// ── Connectivity tab ─────────────────────────────────────────────────────────

describe('RavnDetail — Connectivity tab', () => {
  it('renders connectivity section when connectivity tab is clicked', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('connectivity-section-body')).toBeInTheDocument();
  });

  it('renders MCP servers panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('conn-mcp-panel')).toBeInTheDocument();
  });

  it('renders gateway channels panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('conn-gateway-panel')).toBeInTheDocument();
  });

  it('renders event subscriptions panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('conn-events-panel')).toBeInTheDocument();
  });

  it('shows MCP server chips when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const chips = screen.queryAllByTestId('mcp-server-chip');
    expect(chips.length).toBe(3);
    expect(chips[0]?.textContent).toBe('filesystem');
  });

  it('shows gateway channel chips when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const chips = screen.queryAllByTestId('gateway-channel-chip');
    expect(chips.length).toBe(2);
    expect(chips[0]?.textContent).toBe('slack-dev');
  });

  it('shows event subscription chips when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const chips = screen.queryAllByTestId('event-subscription-chip');
    expect(chips.length).toBe(3);
  });

  it('shows "None configured" when no MCP servers', () => {
    render(
      <RavnDetail
        ravn={{ ...SAMPLE_RAVN, mcpServers: [], gatewayChannels: [], eventSubscriptions: [] }}
      />,
      { wrapper: wrap() },
    );
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const emptyTexts = screen.queryAllByText('None configured');
    expect(emptyTexts.length).toBe(3);
  });

  it('shows "None configured" when connectivity fields are absent', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const emptyTexts = screen.queryAllByText('None configured');
    expect(emptyTexts.length).toBe(3);
  });
});

// ── No resident Chat tab (consolidated into the Sessions view) ────────────────

describe('RavnDetail — no Chat tab', () => {
  it('does not render a Chat tab for a persona ravn', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.queryByTestId('sectab-chat')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /chat/i })).not.toBeInTheDocument();
  });

  it('does not render a Chat tab even for a resident ravn with a chatEndpoint', () => {
    render(<RavnDetail ravn={SAMPLE_RESIDENT} />, { wrapper: wrap() });
    expect(screen.queryByTestId('sectab-chat')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chat-section-body')).not.toBeInTheDocument();
  });

  it('still renders the Sessions tab for a resident ravn', () => {
    render(<RavnDetail ravn={SAMPLE_RESIDENT} />, { wrapper: wrap() });
    const sessionsTab = screen.getByTestId('sectab-sessions');
    expect(sessionsTab).toBeInTheDocument();
    fireEvent.click(sessionsTab);
    expect(sessionsTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('sessions-section-body')).toBeInTheDocument();
  });

  it('falls back to overview when a stored "chat" tab is not available', () => {
    localStorage.setItem('ravn.detail.tab', '"chat"');
    render(<RavnDetail ravn={SAMPLE_RESIDENT} />, { wrapper: wrap() });
    expect(screen.getByTestId('sectab-overview')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('section-body-overview')).toBeInTheDocument();
  });
});

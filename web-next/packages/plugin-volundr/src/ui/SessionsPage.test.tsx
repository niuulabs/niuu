import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { SessionsPage } from './SessionsPage';
import {
  createMockSessionStore,
  createMockVolundrService,
  createMockPtyStream,
  createMockFileSystemPort,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { Session } from '../domain/session';

// ---------------------------------------------------------------------------
// Mock xterm + shiki (SessionDetailPage embeds terminal)
// ---------------------------------------------------------------------------

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => ({
    open: vi.fn(),
    write: vi.fn(),
    dispose: vi.fn(),
    loadAddon: vi.fn(),
    onData: vi.fn().mockReturnValue({ dispose: vi.fn() }),
    options: {},
  })),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => ({
    fit: vi.fn(),
    dispose: vi.fn(),
  })),
}));

vi.mock('shiki', () => ({
  codeToHtml: vi.fn().mockResolvedValue('<pre><code>highlighted</code></pre>'),
}));

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ sessionId: 'ds-1' }),
}));

function wrap(sessionStore: ISessionStore = createMockSessionStore()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          volundr: createMockVolundrService(),
          sessionStore,
          ptyStream: createMockPtyStream(),
          filesystem: createMockFileSystemPort(),
        }}
      >
        <SessionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

function makeSession(
  overrides: Partial<Session> & Pick<Session, 'id' | 'personaName' | 'state'>,
): Session {
  return {
    id: overrides.id,
    ravnId: overrides.ravnId ?? `ravn-${overrides.id}`,
    personaName: overrides.personaName,
    templateId: overrides.templateId ?? 'tpl-default',
    clusterId: overrides.clusterId ?? 'cluster-a',
    state: overrides.state,
    startedAt: overrides.startedAt ?? new Date('2026-05-01T00:00:00.000Z').toISOString(),
    readyAt: overrides.readyAt,
    lastActivityAt: overrides.lastActivityAt ?? new Date('2026-05-01T00:05:00.000Z').toISOString(),
    terminatedAt: overrides.terminatedAt,
    resources: overrides.resources ?? {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.5,
      memRequestMi: 512,
      memLimitMi: 1024,
      memUsedMi: 256,
      gpuCount: 0,
    },
    env: overrides.env ?? {},
    events: overrides.events ?? [],
    bootProgress: overrides.bootProgress,
    connectionType: overrides.connectionType,
    tokensIn: overrides.tokensIn,
    tokensOut: overrides.tokensOut,
    costCents: overrides.costCents,
    preview: overrides.preview,
    files: overrides.files,
    sagaId: overrides.sagaId,
    raidId: overrides.raidId,
  };
}

function createSessionStoreWithSessions(sessions: Session[]): ISessionStore {
  return {
    getSession: async (id) => sessions.find((session) => session.id === id) ?? null,
    listSessions: async () => sessions,
    createSession: async () => {
      throw new Error('not implemented');
    },
    updateSession: async () => {
      throw new Error('not implemented');
    },
    deleteSession: async () => {},
    subscribe: () => () => {},
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SessionsPage', () => {
  it('renders the sessions page container', () => {
    wrap();
    expect(screen.getByTestId('sessions-page')).toBeInTheDocument();
  });

  it('renders the pod list sidebar', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-list-sidebar')).toBeInTheDocument());
  });

  it('renders sidebar header with Sessions title', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('Sessions')).toBeInTheDocument());
  });

  it('renders session count badge', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-count')).toBeInTheDocument());
  });

  it('renders search input in sidebar', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-search')).toBeInTheDocument());
  });

  it('renders ACTIVE group with running sessions', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-group-active')).toBeInTheDocument());
  });

  it('renders BOOTING group with provisioning sessions', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-group-booting')).toBeInTheDocument());
  });

  it('renders ERROR group with failed sessions', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-group-error')).toBeInTheDocument());
  });

  it('renders pod entries for running sessions', async () => {
    wrap();
    await waitFor(() =>
      expect(screen.getByTestId('pod-entry-laptop-volundr-local')).toBeInTheDocument(),
    );
  });

  it('auto-selects the first running session and shows detail page', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('live-session-detail-page')).toBeInTheDocument());
  });

  it('switches detail view when clicking a different session', async () => {
    wrap();
    await waitFor(() =>
      expect(screen.getByTestId('pod-entry-mimir-bge-reindex')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('pod-entry-mimir-bge-reindex'));
    await waitFor(() => expect(screen.getByTestId('live-session-detail-page')).toBeInTheDocument());
  });

  it('filters sidebar entries by search query', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-search')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('pod-search'), { target: { value: 'mimir' } });
    await waitFor(() =>
      expect(screen.getByTestId('pod-entry-mimir-bge-reindex')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('pod-entry-ds-1')).not.toBeInTheDocument();
  });

  it('can group sessions by repo', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({
        id: 'alpha-1',
        personaName: 'alpha one',
        state: 'running',
        preview: 'github.com/acme/alpha#main',
      }),
      makeSession({
        id: 'alpha-2',
        personaName: 'alpha two',
        state: 'idle',
        preview: 'github.com/acme/alpha#feature/docs',
      }),
      makeSession({
        id: 'beta-1',
        personaName: 'beta one',
        state: 'failed',
        preview: 'github.com/acme/beta#fix/login',
      }),
    ]);

    wrap(store);
    await waitFor(() => expect(screen.getByTestId('pod-group-mode-repo')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pod-group-mode-repo'));

    await waitFor(() => expect(screen.getByTestId('pod-group-alpha')).toBeInTheDocument());
    expect(screen.getByTestId('pod-group-alpha-count')).toHaveTextContent('2');
    expect(screen.getByTestId('pod-group-beta')).toBeInTheDocument();
    expect(screen.queryByTestId('pod-group-active')).not.toBeInTheDocument();
  });

  it('shows loading state initially', () => {
    const slowStore: ISessionStore = {
      ...createMockSessionStore(),
      listSessions: () => new Promise(() => {}),
    };
    wrap(slowStore);
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it('renders session row metadata without crashing', async () => {
    wrap();
    await waitFor(() =>
      expect(screen.getByTestId('pod-entry-laptop-volundr-local')).toBeInTheDocument(),
    );
    const row = screen.getByTestId('pod-entry-laptop-volundr-local');
    expect(row).toHaveTextContent(/reading volundr/i);
    expect(row).toHaveTextContent(/ago/i);
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { SessionsPage } from './SessionsPage';
import {
  createMockSessionStore,
  createMockVolundrService,
  createMockPtyStream,
  createMockFileSystemPort,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { IVolundrService } from '../ports/IVolundrService';
import type { Session } from '../domain/session';

const navigate = vi.fn();

// ---------------------------------------------------------------------------
// Mock xterm + shiki (SessionDetailPage embeds terminal)
// ---------------------------------------------------------------------------

vi.mock('@xterm/xterm', () => ({
  Terminal: class MockXtermTerminal {
    open = vi.fn();
    write = vi.fn();
    dispose = vi.fn();
    loadAddon = vi.fn();
    onData = vi.fn().mockReturnValue({ dispose: vi.fn() });
    options = {};
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit = vi.fn();
    dispose = vi.fn();
  },
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
  useNavigate: () => navigate,
  useParams: () => ({ sessionId: 'ds-1' }),
}));

function wrap(
  sessionStore: ISessionStore = createMockSessionStore(),
  volundr: IVolundrService = createMockVolundrService(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr) },
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
    name: overrides.name,
    ravnId: overrides.ravnId ?? `ravn-${overrides.id}`,
    personaName: overrides.personaName,
    templateId: overrides.templateId ?? 'tpl-default',
    clusterId: overrides.clusterId ?? 'cluster-a',
    clusterName: overrides.clusterName,
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
    runId: overrides.runId,
    origin: overrides.origin,
    model: overrides.model,
    source: overrides.source,
    sessionDefinition: overrides.sessionDefinition,
    coordination: overrides.coordination,
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
  beforeEach(() => {
    localStorage.clear();
    navigate.mockClear();
  });

  it('shows healthy sessions while a Forge is unavailable and allows retrying the connection', async () => {
    const healthy = makeSession({
      id: 'ds-1',
      name: 'Healthy session',
      personaName: 'dev',
      state: 'running',
    });
    const recovered = makeSession({
      id: 'recovered',
      name: 'Recovered session',
      personaName: 'dev',
      state: 'running',
    });
    let offline = true;
    const store = createSessionStoreWithSessions([healthy, recovered]);
    store.listSources = async () => [
      { id: 'thor', name: 'Thor' },
      { id: 'build', name: 'Build' },
    ];
    store.listSessions = async (filters) => {
      if (filters?.instanceId === 'build') {
        if (offline) throw new Error('Connection timed out. Check this Forge in Guild.');
        return filters.archivedOnly ? [] : [recovered];
      }
      return filters?.archivedOnly ? [] : [healthy];
    };
    wrap(store);
    expect(await screen.findByTestId('pod-entry-ds-1')).toBeInTheDocument();
    const unavailable = await screen.findByText('Build: unavailable');
    expect(unavailable.closest('[role="status"]')).toHaveAttribute(
      'title',
      'Connection timed out. Check this Forge in Guild.',
    );
    expect(screen.queryByText('Build archive: unavailable')).not.toBeInTheDocument();
    offline = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry Forge connections' }));
    expect(await screen.findByTestId('pod-entry-recovered')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByLabelText('Forge connections')).not.toBeInTheDocument(),
    );
  });

  it('keeps the requested session loading instead of selecting another Forge and reports archive failures', async () => {
    const other = makeSession({
      id: 'other',
      name: 'Other session',
      personaName: 'dev',
      state: 'running',
    });
    const requested = makeSession({
      id: 'ds-1',
      name: 'Requested session',
      personaName: 'dev',
      state: 'running',
    });
    let finishRequested!: (sessions: Session[]) => void;
    const pending = new Promise<Session[]>((resolve) => {
      finishRequested = resolve;
    });
    const store = createSessionStoreWithSessions([other, requested]);
    store.listSources = async () => [
      { id: 'thor', name: 'Thor' },
      { id: 'build', name: 'Build' },
    ];
    store.listSessions = async (filters) => {
      if (filters?.archivedOnly) {
        if (filters.instanceId === 'thor') throw new Error('Archive access denied');
        return [];
      }
      return filters?.instanceId === 'build' ? pending : [other];
    };
    wrap(store);
    expect(await screen.findByTestId('pod-entry-other')).toBeInTheDocument();
    expect(await screen.findByText('Loading selected session…')).toBeInTheDocument();
    expect(screen.getByText('Build: loading…')).toBeInTheDocument();
    expect(screen.getByText('Thor archive: unavailable')).toBeInTheDocument();
    await act(async () => {
      finishRequested([requested]);
    });
    expect(await screen.findByTestId('pod-entry-ds-1')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText('Loading selected session…')).not.toBeInTheDocument(),
    );
  });

  it('keeps pinned sessions above every grouping and state filter without duplicate rows', async () => {
    const sessions = [
      makeSession({
        id: 'active-pin',
        name: 'active-review',
        personaName: 'dev',
        state: 'running',
      }),
      makeSession({ id: 'idle-pin', name: 'idle-review', personaName: 'dev', state: 'idle' }),
      makeSession({
        id: 'archived-pin',
        name: 'archived-review',
        personaName: 'dev',
        state: 'archived',
      }),
      makeSession({ id: 'other', name: 'other-review', personaName: 'dev', state: 'running' }),
    ];
    localStorage.setItem(
      'niuu.forge.pinnedSessions',
      '["idle-pin","active-pin","archived-pin","missing"]',
    );
    wrap(createSessionStoreWithSessions(sessions));
    const pinned = await screen.findByTestId('pod-group-pinned');
    expect(
      within(pinned)
        .getAllByTestId(/^pod-entry-[^-]+-pin$/)
        .map((row) => row.dataset.testid),
    ).toEqual(['pod-entry-idle-pin', 'pod-entry-active-pin', 'pod-entry-archived-pin']);
    for (const mode of ['state', 'repo', 'forge', 'project']) {
      fireEvent.click(screen.getByTestId(`pod-group-mode-${mode}`));
      for (const filter of [
        'live',
        'active',
        'idle',
        'attention',
        'stopped',
        'failed',
        'all',
        'archived',
      ]) {
        fireEvent.click(screen.getByTestId(`session-filter-${filter}`));
        expect(screen.getAllByTestId('pod-entry-active-pin')).toHaveLength(1);
        expect(within(pinned).getByTestId('pod-entry-archived-pin')).toBeInTheDocument();
      }
    }
    fireEvent.click(within(pinned).getByRole('button', { name: /^Pinned/ }));
    expect(within(pinned).queryByTestId('pod-entry-active-pin')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pod-group-mode-state'));
    expect(within(pinned).getByRole('button', { name: /^Pinned/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('pins beside rename without selecting the row, lets search narrow pins, and unpins to the normal group', async () => {
    const session = makeSession({
      id: 'pin-me',
      name: 'review',
      personaName: 'dev',
      state: 'idle',
    });
    wrap(createSessionStoreWithSessions([session]));
    const row = await screen.findByTestId('pod-entry-pin-me');
    fireEvent.click(within(row.parentElement!).getByRole('button', { name: 'Pin review' }));
    expect(navigate).not.toHaveBeenCalled();
    const pinned = screen.getByTestId('pod-group-pinned');
    expect(within(pinned).getByTestId('pod-entry-pin-me')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('pod-search'), { target: { value: 'no-match' } });
    expect(screen.queryByTestId('pod-group-pinned')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('pod-search'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Unpin review' }));
    expect(screen.queryByTestId('pod-group-pinned')).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId('pod-group-idle')).getByTestId('pod-entry-pin-me'),
    ).toBeInTheDocument();
  });

  it('renders the sessions page container', async () => {
    await act(async () => {
      wrap();
    });
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

  it('keeps the Sessions heading free of the total count', async () => {
    wrap();
    await screen.findByRole('heading', { name: 'Sessions' });
    expect(screen.queryByTestId('pod-count')).not.toBeInTheDocument();
  });

  it('renders search input in sidebar', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-search')).toBeInTheDocument());
  });

  it('opens the launch wizard from the sidebar add button', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-launch-button')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pod-launch-button'));
    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: 'Quick launch' })).toBeInTheDocument(),
    );
  });

  it('renders WORKING group with running sessions', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-group-working')).toBeInTheDocument());
  });

  it('renders BOOTING group with provisioning sessions', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-group-booting')).toBeInTheDocument());
  });

  it('renders ERROR group with failed sessions', async () => {
    localStorage.setItem('niuu.forge.filter', 'all');
    wrap();
    await waitFor(() => expect(screen.getByTestId('pod-group-error')).toBeInTheDocument());
  });

  it('renders ARCHIVED group when archived sessions are present', async () => {
    localStorage.setItem('niuu.forge.filter', 'all');
    const store = createSessionStoreWithSessions([
      makeSession({ id: 'arch-1', personaName: 'archiver', state: 'archived' }),
    ]);
    wrap(store);
    await waitFor(() => expect(screen.getByTestId('pod-group-archived')).toBeInTheDocument());
    expect(screen.getByTestId('pod-entry-arch-1')).toBeInTheDocument();
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
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'mimir-bge-reindex' },
    });
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

  it('filters sidebar entries by forge name', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({
        id: 'alpha-1',
        personaName: 'alpha one',
        state: 'running',
        clusterId: 'guild-alpha',
        clusterName: 'Guild Alpha',
      }),
      makeSession({
        id: 'beta-1',
        personaName: 'beta one',
        state: 'idle',
        clusterId: 'guild-beta',
        clusterName: 'Guild Beta',
      }),
    ]);

    wrap(store);
    await waitFor(() => expect(screen.getByTestId('pod-search')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('pod-search'), { target: { value: 'guild beta' } });

    await waitFor(() => expect(screen.getByTestId('pod-entry-beta-1')).toBeInTheDocument());
    expect(screen.queryByTestId('pod-entry-alpha-1')).not.toBeInTheDocument();
  });

  it('can group sessions by repo', async () => {
    localStorage.setItem('niuu.forge.filter', 'all');
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
    expect(screen.queryByTestId('pod-group-working')).not.toBeInTheDocument();
  });

  it('can group sessions by forge', async () => {
    localStorage.setItem('niuu.forge.filter', 'all');
    const store = createSessionStoreWithSessions([
      makeSession({
        id: 'alpha-1',
        personaName: 'alpha one',
        state: 'running',
        clusterId: 'guild-alpha',
        clusterName: 'Guild Alpha',
      }),
      makeSession({
        id: 'alpha-2',
        personaName: 'alpha two',
        state: 'idle',
        clusterId: 'guild-alpha',
        clusterName: 'Guild Alpha',
      }),
      makeSession({
        id: 'beta-1',
        personaName: 'beta one',
        state: 'failed',
        clusterId: 'guild-beta',
        clusterName: 'Guild Beta',
      }),
    ]);

    wrap(store);
    await waitFor(() => expect(screen.getByTestId('pod-group-mode-forge')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pod-group-mode-forge'));

    await waitFor(() => expect(screen.getByTestId('pod-group-guild-alpha')).toBeInTheDocument());
    expect(screen.getByTestId('pod-group-guild-alpha-count')).toHaveTextContent('2');
    expect(screen.getByTestId('pod-group-guild-beta')).toBeInTheDocument();
    expect(screen.queryByTestId('pod-group-working')).not.toBeInTheDocument();
  });

  it('shows loading state initially', async () => {
    const slowStore: ISessionStore = {
      ...createMockSessionStore(),
      listSessions: () => new Promise(() => {}),
    };
    await act(async () => {
      wrap(slowStore);
    });
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it('renders session row metadata without crashing', async () => {
    localStorage.setItem('niuu.forge.details', '1');
    wrap();
    await waitFor(() =>
      expect(screen.getByTestId('pod-entry-laptop-volundr-local')).toBeInTheDocument(),
    );
    const row = screen.getByTestId('pod-entry-laptop-volundr-local');
    expect(row).toHaveTextContent(/reading volundr/i);
    expect(row).toHaveTextContent('Working');
  });

  it('renders the forge label when a session has an instance name', async () => {
    localStorage.setItem('niuu.forge.details', '1');
    const store = createSessionStoreWithSessions([
      makeSession({
        id: 'forge-1',
        personaName: 'forge test',
        state: 'running',
        clusterId: 'guild-alpha',
        clusterName: 'Guild Alpha',
      }),
    ]);

    wrap(store);
    await waitFor(() => expect(screen.getByTestId('pod-entry-forge-1')).toBeInTheDocument());
    const row = screen.getByTestId('pod-entry-forge-1');
    expect(row).toHaveTextContent(/forge/i);
    expect(row).toHaveTextContent('Guild Alpha');
  });

  it('shows archive-all-stopped action and calls the service', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({ id: 'stopped-1', personaName: 'stopped one', state: 'terminated' }),
    ]);
    const volundr = createMockVolundrService();
    const archiveStoppedSessions = vi.fn().mockResolvedValue(['stopped-1']);
    (volundr as IVolundrService).archiveStoppedSessions = archiveStoppedSessions;

    wrap(store, volundr);
    await waitFor(() => expect(screen.getByTestId('archive-stopped-button')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('archive-stopped-button'));
    await waitFor(() => expect(archiveStoppedSessions).toHaveBeenCalledTimes(1));
  });

  it('can select multiple stopped sessions and delete them together', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({ id: 'stopped-1', personaName: 'stopped one', state: 'terminated' }),
      makeSession({ id: 'stopped-2', personaName: 'stopped two', state: 'terminated' }),
      makeSession({ id: 'running-1', personaName: 'running one', state: 'running' }),
    ]);
    const volundr = createMockVolundrService();
    const deleteSession = vi.fn().mockResolvedValue(undefined);
    (volundr as IVolundrService).deleteSession = deleteSession;

    wrap(store, volundr);

    await waitFor(() =>
      expect(screen.getByTestId('toggle-stopped-selection-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('toggle-stopped-selection-button'));
    expect(screen.getByTestId('stopped-session-checkbox-stopped-1')).toBeChecked();
    expect(screen.getByTestId('stopped-session-checkbox-stopped-2')).toBeChecked();
    fireEvent.click(screen.getByTestId('delete-selected-stopped-button'));

    await waitFor(() =>
      expect(screen.getByTestId('confirm-delete-selected-stopped-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('confirm-delete-selected-stopped-button'));

    await waitFor(() => expect(deleteSession).toHaveBeenCalledTimes(2));
    expect(deleteSession).toHaveBeenCalledWith('stopped-1');
    expect(deleteSession).toHaveBeenCalledWith('stopped-2');
  });

  it('shows an origin badge on imported sessions but not on volundr-native ones', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({ id: 'imp-1', personaName: 'imported claude', state: 'idle', origin: 'claude' }),
      makeSession({ id: 'imp-2', personaName: 'imported codex', state: 'idle', origin: 'codex' }),
      makeSession({ id: 'native-1', personaName: 'native', state: 'running', origin: 'volundr' }),
      makeSession({ id: 'native-2', personaName: 'legacy', state: 'running' }),
    ]);

    wrap(store);

    await waitFor(() =>
      expect(screen.getByTestId('session-origin-badge-imp-1')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('session-origin-badge-imp-1')).toHaveTextContent('Claude');
    expect(screen.getByTestId('session-origin-badge-imp-2')).toHaveTextContent('Codex');
    expect(screen.queryByTestId('session-origin-badge-native-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('session-origin-badge-native-2')).not.toBeInTheDocument();
  });

  it('opens the import dialog from the sidebar import button', async () => {
    wrap();

    await waitFor(() => expect(screen.getByTestId('pod-import-button')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pod-import-button'));

    await waitFor(() =>
      expect(screen.getByTestId('import-external-sessions-panel')).toBeInTheDocument(),
    );
    expect(screen.getByText('Import CLI Sessions')).toBeInTheDocument();
  });

  it('defers external session discovery until the import dialog opens', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('not available'), { status: 503 }));

    wrap(createMockSessionStore(), volundr);

    await waitFor(() => expect(screen.getByTestId('pod-import-button')).toBeInTheDocument());
    expect(volundr.listExternalSessions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('pod-import-button'));

    await waitFor(() => expect(volundr.listExternalSessions).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('Discovery unavailable')).toBeInTheDocument());
  });

  it('does not probe external session discovery before import is requested', async () => {
    const volundr = createMockVolundrService();
    volundr.listExternalSessions = vi.fn().mockRejectedValue(new Error('boom'));

    wrap(createMockSessionStore(), volundr);

    await waitFor(() => expect(screen.getByTestId('pod-import-button')).toBeInTheDocument());
    expect(volundr.listExternalSessions).not.toHaveBeenCalled();
  });

  it('imports an external session and refreshes the sessions list', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({ id: 'ds-1', personaName: 'existing', state: 'running' }),
    ]);
    const listSessions = vi.spyOn(store, 'listSessions');
    const volundr = createMockVolundrService();
    const importExternalSession = vi.fn().mockResolvedValue({
      id: 'sess-imported',
      name: 'imported session',
      source: { type: 'git', repo: '~/code/niuu/volundr', branch: 'main' },
      status: 'stopped',
      model: 'claude-sonnet',
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
      origin: 'claude',
      externalSessionId: 'ext-claude-1',
    });
    volundr.importExternalSession = importExternalSession;

    wrap(store, volundr);

    await waitFor(() => expect(screen.getByTestId('pod-import-button')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pod-import-button'));

    await waitFor(() =>
      expect(screen.getByTestId('external-session-import-ext-claude-1')).toBeInTheDocument(),
    );
    const callsBeforeImport = listSessions.mock.calls.length;
    fireEvent.click(screen.getByTestId('external-session-import-ext-claude-1'));

    await waitFor(() =>
      expect(importExternalSession).toHaveBeenCalledWith('claude-code', 'ext-claude-1'),
    );
    await waitFor(() => expect(listSessions.mock.calls.length).toBeGreaterThan(callsBeforeImport));
  });

  it('navigates back to the sessions list when deleting the selected stopped session', async () => {
    const store = createSessionStoreWithSessions([
      makeSession({ id: 'ds-1', personaName: 'stopped current', state: 'terminated' }),
      makeSession({ id: 'stopped-2', personaName: 'stopped two', state: 'terminated' }),
    ]);
    const volundr = createMockVolundrService();
    const deleteSession = vi.fn().mockResolvedValue(undefined);
    (volundr as IVolundrService).deleteSession = deleteSession;

    wrap(store, volundr);

    await waitFor(() =>
      expect(screen.getByTestId('toggle-stopped-selection-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('toggle-stopped-selection-button'));
    fireEvent.click(screen.getByTestId('stopped-session-checkbox-stopped-2'));
    fireEvent.click(screen.getByTestId('delete-selected-stopped-button'));
    await waitFor(() =>
      expect(screen.getByTestId('confirm-delete-selected-stopped-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('confirm-delete-selected-stopped-button'));

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({ to: '/volundr/sessions', replace: true }),
    );
  });
});

describe('Forge session review controls', () => {
  beforeEach(() => {
    localStorage.clear();
    navigate.mockClear();
  });
  const sessions = [
    makeSession({
      id: 'working',
      name: 'Release review',
      personaName: 'dev-user',
      state: 'running',
      clusterName: 'Thor',
      preview: '/home/thor/repos/niuu',
    }),
    makeSession({ id: 'waiting', personaName: 'Ready for input', state: 'awaiting_input' }),
    makeSession({ id: 'idle', personaName: 'Idle session', state: 'idle' }),
    makeSession({ id: 'stopped', personaName: 'Stopped session', state: 'terminated' }),
    makeSession({ id: 'archived', personaName: 'Old session', state: 'archived' }),
  ];
  it('defaults to live sessions, counts filters, preserves selection and composes search', async () => {
    wrap(createSessionStoreWithSessions(sessions));
    await screen.findByTestId('pod-entry-working');
    expect(screen.getByTestId('session-filter-live')).toHaveTextContent('Live3');
    expect(screen.queryByTestId('pod-entry-stopped')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pod-entry-archived')).not.toBeInTheDocument();
    expect(screen.getByTestId('pod-entry-working')).not.toHaveTextContent('dev-user');
    expect(screen.getByTestId('pod-entry-working')).toHaveTextContent('Thor');
    expect(screen.getByTestId('pod-entry-working')).toHaveTextContent('~/repos/niuu');
    fireEvent.click(screen.getByTestId('session-filter-all'));
    expect(screen.getByTestId('pod-entry-archived')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('pod-search'), { target: { value: 'Release review' } });
    expect(screen.getByTestId('pod-entry-working')).toBeInTheDocument();
    expect(screen.queryByTestId('pod-entry-idle')).not.toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show session details' }));
    expect(screen.getByTestId('pod-entry-working')).toHaveTextContent('Thor');
  });
  it.each([
    ['/home/horde/repos/niuu', '~/repos/niuu'],
    ['/home/horde', '~/'],
    ['/home/horde/', '~/'],
    ['/workspace/niuu', '/workspace/niuu'],
    ['~/repos/niuu', '~/repos/niuu'],
    ['/home', '/home'],
    ['/homebrew/niuu', '/homebrew/niuu'],
  ])(
    'shows the agent, host and compact folder for %s with its full path on hover',
    async (path, label) => {
      wrap(
        createSessionStoreWithSessions([
          makeSession({
            id: 'host-row',
            personaName: 'Workspace review',
            state: 'running',
            clusterName: 'Build Bro',
            model: 'gpt-6-astra',
            source: { type: 'local_mount', local_path: path },
          }),
        ]),
      );
      const row = await screen.findByTestId('pod-entry-host-row');
      expect(row).toHaveTextContent(`CodexBuild Bro${label}`);
      expect(within(row).getByTitle('Host: Build Bro')).toHaveTextContent('Build Bro');
      expect(within(row).getByTitle(path)).toHaveTextContent(label);
    },
  );
  it('persists keyboard resizing and collapsed groups', async () => {
    wrap(createSessionStoreWithSessions(sessions));
    await screen.findByTestId('pod-entry-working');
    const separator = screen.getByRole('separator', { name: 'Resize session list' });
    expect(separator).toHaveAttribute('aria-valuenow', '340');
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(localStorage.getItem('niuu.forge.sidebarWidth')).toBe('360');
    fireEvent.keyDown(separator, { key: 'End' });
    expect(separator).toHaveAttribute('aria-valuenow', '640');
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(separator).toHaveAttribute('aria-valuenow', '640');
    fireEvent.doubleClick(separator);
    expect(separator).toHaveAttribute('aria-valuenow', '340');
    fireEvent.click(
      within(screen.getByTestId('pod-group-working')).getByRole('button', { expanded: true }),
    );
    expect(screen.queryByTestId('pod-entry-working')).not.toBeInTheDocument();
    expect(localStorage.getItem('niuu.forge.group.state:WORKING')).toBe('1');
  });
  it('reports a failed stop and does not proceed to archive or change selection', async () => {
    const service = createMockVolundrService();
    service.stopSession = vi.fn().mockRejectedValue(new Error('Thor could not stop this session'));
    service.archiveSession = vi.fn();
    wrap(createSessionStoreWithSessions(sessions), service);
    fireEvent.click(await screen.findByTestId('pod-entry-working-archive'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Thor could not stop this session');
    expect(service.archiveSession).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });
  it('stops before archiving and restores an archived record', async () => {
    const calls: string[] = [];
    const service = createMockVolundrService();
    service.stopSession = vi.fn(async () => {
      calls.push('stop');
    });
    service.archiveSession = vi.fn(async () => {
      calls.push('archive');
    });
    service.restoreSession = vi.fn().mockResolvedValue(undefined);
    wrap(createSessionStoreWithSessions(sessions), service);
    fireEvent.click(await screen.findByTestId('pod-entry-working-archive'));
    await waitFor(() => expect(calls).toEqual(['stop', 'archive']));
    fireEvent.click(screen.getByTestId('session-filter-archived'));
    fireEvent.click(screen.getByRole('button', { name: 'Restore Old session' }));
    await waitFor(() => expect(service.restoreSession).toHaveBeenCalledWith('archived'));
  });
  it('requires explicit delete confirmation and leaves failures visible for retry', async () => {
    const service = createMockVolundrService();
    service.deleteSession = vi
      .fn()
      .mockRejectedValueOnce(new Error('Deletion rejected'))
      .mockResolvedValue(undefined);
    wrap(createSessionStoreWithSessions(sessions), service);
    fireEvent.click(await screen.findByTestId('pod-entry-working-delete'));
    expect(service.deleteSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel', exact: true }));
    expect(service.deleteSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('pod-entry-working-delete'));
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));
    await waitFor(() =>
      expect(screen.getAllByRole('alert')[0]).toHaveTextContent('Deletion rejected'),
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(service.deleteSession).toHaveBeenCalledTimes(2);
  });
});

it('groups registered projects as a collapsible session tree and keeps settings in the footer', async () => {
  const service = createMockVolundrService();
  service.getProjects = vi
    .fn()
    .mockResolvedValue([{ id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active' }]);
  const sessions = [
    makeSession({
      id: 'parent',
      personaName: 'Coordinator',
      state: 'running',
      coordination: { projectId: 'lexi', role: 'coordinator' },
    }),
    makeSession({
      id: 'child',
      personaName: 'iOS work',
      state: 'idle',
      coordination: {
        projectId: 'lexi',
        role: 'worker',
        parent: { instanceId: 'cluster-a', sessionId: 'parent' },
      },
    }),
    makeSession({ id: 'other', personaName: 'Independent', state: 'idle' }),
  ];
  wrap(createSessionStoreWithSessions(sessions), service);
  await screen.findByTestId('pod-entry-parent');
  expect(service.getProjects).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId('pod-group-mode-project'));
  await screen.findByTestId('pod-group-lexi');
  expect(screen.getByTestId('pod-group-no-project')).toHaveTextContent('Independent');
  fireEvent.click(screen.getByRole('button', { name: 'Collapse child sessions of Coordinator' }));
  expect(screen.queryByTestId('pod-entry-child')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Expand child sessions of Coordinator' }));
  expect(screen.getByTestId('pod-entry-child')).toBeInTheDocument();
  const footer = screen.getByRole('group', { name: 'Session list settings' });
  expect(within(footer).getByRole('checkbox', { name: 'Show token usage' })).not.toBeChecked();
  fireEvent.click(within(footer).getByRole('checkbox', { name: 'Show token usage' }));
  expect(localStorage.getItem('niuu.forge.tokens')).toBe('1');
});

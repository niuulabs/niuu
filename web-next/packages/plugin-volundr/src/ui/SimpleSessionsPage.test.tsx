import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { SimpleSessionsPage } from './SimpleSessionsPage';
import {
  createMockVolundrService,
  createMockPtyStream,
  createMockFileSystemPort,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { IVolundrService } from '../ports/IVolundrService';
import type { Session, SessionState } from '../domain/session';

const navigate = vi.fn();
let routeSessionId: string | undefined;

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ sessionId: routeSessionId }),
  Link: ({
    children,
    to,
    params,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
    params?: Record<string, string>;
    [key: string]: unknown;
  }) => {
    const href = to.replace(/\$(\w+)/g, (_, key: string) => params?.[key] ?? '');
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  },
}));

vi.mock('./LiveSessionDetailPage', () => ({
  LiveSessionDetailPage: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="live-session-detail">{sessionId}</div>
  ),
}));

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

const NOW = Date.now();

function makeSession(id: string, state: SessionState, overrides: Partial<Session> = {}): Session {
  return {
    id,
    ravnId: `ravn-${id}`,
    name: id,
    personaName: 'builder',
    templateId: 'tpl',
    clusterId: 'cluster-a',
    state,
    startedAt: new Date(NOW - 60_000).toISOString(),
    lastActivityAt: new Date(NOW - 60_000).toISOString(),
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.5,
      memRequestMi: 512,
      memLimitMi: 1024,
      memUsedMi: 256,
      gpuCount: 0,
    },
    env: {},
    events: [],
    ...overrides,
  };
}

function storeWith(sessions: Session[]): ISessionStore {
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

function wrap(sessionStore: ISessionStore, volundr: IVolundrService = createMockVolundrService()) {
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
        <SimpleSessionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

const SESSIONS = [
  makeSession('waiting-1', 'awaiting_input'),
  makeSession('running-1', 'running'),
  makeSession('booting-1', 'provisioning'),
  makeSession('done-1', 'terminated'),
  makeSession('old-1', 'failed', {
    lastActivityAt: new Date(NOW - 3 * 24 * 60 * 60 * 1000).toISOString(),
  }),
  makeSession('archived-1', 'archived'),
];

describe('SimpleSessionsPage', () => {
  beforeEach(() => {
    localStorage.clear();
    navigate.mockClear();
    routeSessionId = undefined;
  });

  it('heads the list with the session count and a link to the new-session page', async () => {
    wrap(storeWith(SESSIONS));
    await waitFor(() => expect(screen.getByTestId('simple-session-count')).toHaveTextContent('6'));
    expect(screen.getByTestId('simple-session-new')).toHaveAttribute(
      'href',
      '/volundr/sessions/new',
    );
  });

  it('groups sessions into needs you, running and done today', async () => {
    wrap(storeWith(SESSIONS));

    await waitFor(() => expect(screen.getByTestId('pod-group-needs-you')).toBeInTheDocument());
    expect(screen.getByTestId('pod-group-needs-you-count')).toHaveTextContent('1');
    expect(screen.getByTestId('pod-group-running-count')).toHaveTextContent('2');
    expect(screen.getByTestId('pod-group-done-today-count')).toHaveTextContent('1');
    expect(screen.getByTestId('pod-group-older-count')).toHaveTextContent('1');
  });

  it('folds Older away until its header is clicked', async () => {
    wrap(storeWith(SESSIONS));

    await waitFor(() => expect(screen.getByTestId('pod-group-older')).toBeInTheDocument());
    expect(screen.queryByTestId('pod-entry-old-1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('pod-group-older-header'));
    expect(screen.getByTestId('pod-entry-old-1')).toBeInTheDocument();
  });

  it('keeps archived sessions behind the footer row', async () => {
    wrap(storeWith(SESSIONS));

    const toggle = await screen.findByTestId('simple-session-archived-toggle');
    await waitFor(() => expect(toggle).toHaveTextContent('Archived · 1'));
    expect(screen.queryByTestId('pod-entry-archived-1')).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByTestId('pod-entry-archived-1')).toBeInTheDocument();
  });

  it('filters the list by the search box', async () => {
    wrap(storeWith(SESSIONS));
    await waitFor(() => expect(screen.getByTestId('pod-entry-running-1')).toBeInTheDocument());

    fireEvent.change(screen.getByTestId('simple-session-search'), {
      target: { value: 'waiting' },
    });

    expect(screen.getByTestId('pod-entry-waiting-1')).toBeInTheDocument();
    expect(screen.queryByTestId('pod-entry-running-1')).not.toBeInTheDocument();
  });

  it('opens the first running session and follows a click to another one', async () => {
    wrap(storeWith(SESSIONS));

    await waitFor(() =>
      expect(screen.getByTestId('live-session-detail')).toHaveTextContent('running-1'),
    );

    fireEvent.click(screen.getByTestId('pod-entry-waiting-1'));
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'waiting-1' },
    });
    await waitFor(() =>
      expect(screen.getByTestId('live-session-detail')).toHaveTextContent('waiting-1'),
    );
  });

  it('opens the session named in the route', async () => {
    routeSessionId = 'done-1';
    wrap(storeWith(SESSIONS));

    await waitFor(() =>
      expect(screen.getByTestId('live-session-detail')).toHaveTextContent('done-1'),
    );
  });

  it('archives every stopped session from the overflow menu', async () => {
    const volundr = createMockVolundrService();
    const archiveStoppedSessions = vi.fn(volundr.archiveStoppedSessions);
    volundr.archiveStoppedSessions = archiveStoppedSessions;
    wrap(storeWith(SESSIONS), volundr);

    fireEvent.click(await screen.findByTestId('simple-session-menu'));
    fireEvent.click(await screen.findByTestId('simple-session-archive-stopped'));

    await waitFor(() => expect(archiveStoppedSessions).toHaveBeenCalledTimes(1));
  });

  it('reports a failure to archive stopped sessions', async () => {
    const volundr = createMockVolundrService();
    volundr.archiveStoppedSessions = async () => {
      throw new Error('forge unreachable');
    };
    wrap(storeWith(SESSIONS), volundr);

    fireEvent.click(await screen.findByTestId('simple-session-menu'));
    fireEvent.click(await screen.findByTestId('simple-session-archive-stopped'));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('forge unreachable'));
  });

  it('opens the import dialog from the overflow menu', async () => {
    wrap(storeWith(SESSIONS));

    fireEvent.click(await screen.findByTestId('simple-session-menu'));
    fireEvent.click(await screen.findByTestId('simple-session-import'));

    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: /import/i })).toBeInTheDocument(),
    );
  });

  it('invites a first session when there are none', async () => {
    wrap(storeWith([]));

    await waitFor(() => expect(screen.getByText('No sessions yet')).toBeInTheDocument());
    expect(screen.getByRole('link', { name: 'Start a session' })).toHaveAttribute(
      'href',
      '/volundr/sessions/new',
    );
  });
});

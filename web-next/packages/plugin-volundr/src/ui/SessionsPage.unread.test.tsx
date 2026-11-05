import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { SessionsPage } from './SessionsPage';
import {
  createMockFileSystemPort,
  createMockPtyStream,
  createMockVolundrService,
} from '../adapters/mock';
import { SessionReadStateConflictError, type Session } from '../domain/session';
import type { ISessionStore } from '../ports/ISessionStore';
import type { IVolundrService } from '../ports/IVolundrService';
import type { SessionReadState } from '../models/volundr.model';

const navigate = vi.fn();
const route = vi.hoisted(() => ({ params: {} as { sessionId?: string } }));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useParams: () => route.params,
}));
vi.mock('./LiveSessionDetailPage', () => ({
  LiveSessionDetailPage: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="live-session-detail-page">{sessionId}</div>
  ),
}));
vi.stubGlobal(
  'IntersectionObserver',
  class {
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      this.callback(
        [{ isIntersecting: true, target } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      );
    }
    disconnect() {}
  },
);
vi.stubGlobal(
  'ResizeObserver',
  class {
    observe() {}
    disconnect() {}
    unobserve() {}
  },
);

const NOW = Date.parse('2026-11-05T12:00:00Z');
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

function readState(overrides: Partial<SessionReadState> = {}): SessionReadState {
  return {
    isUnread: true,
    latestOutputSeq: 14,
    latestOutputTurnId: 'turn-1',
    latestOutputAt: minutesAgo(3),
    readThroughSeq: 9,
    manuallyUnread: false,
    revision: 3,
    ...overrides,
  };
}

function makeSession(overrides: Partial<Session> & Pick<Session, 'id' | 'state'>): Session {
  return {
    name: overrides.id,
    ravnId: overrides.id,
    personaName: overrides.id,
    templateId: 't',
    clusterId: 'thor',
    clusterName: 'Thor',
    startedAt: minutesAgo(600),
    lastActivityAt: minutesAgo(5),
    resources: {
      cpuRequest: 0,
      cpuLimit: 0,
      cpuUsed: 0,
      memRequestMi: 0,
      memLimitMi: 0,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    ...overrides,
  };
}

function setup(initial: Session[], overrides: Partial<IVolundrService> = {}) {
  let sessions = initial;
  const store: ISessionStore = {
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
  const changeSessionReadState = vi.fn(
    async (id: string, change: { state: 'read' | 'unread'; expectedRevision: number }) => {
      const next = readState({
        isUnread: change.state === 'unread',
        manuallyUnread: change.state === 'unread',
        readThroughSeq: change.state === 'read' ? 14 : 9,
        revision: change.expectedRevision + 1,
      });
      sessions = sessions.map((session) =>
        session.id === id ? { ...session, readState: next } : session,
      );
      return next;
    },
  );
  const volundr = {
    ...createMockVolundrService(),
    changeSessionReadState,
    getConversationHistory: async () => ({
      turns: [
        {
          id: 'turn-1',
          role: 'assistant',
          content: 'The migration lives in `migrations/`.',
          created_at: minutesAgo(3),
        },
      ],
    }),
    ...overrides,
  } as IVolundrService;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr) },
          sessionStore: store,
          ptyStream: createMockPtyStream(),
          filesystem: createMockFileSystemPort(),
        }}
      >
        <SessionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { changeSessionReadState };
}

const sessions = [
  makeSession({
    id: 'migr',
    name: 'chart-migrations',
    state: 'awaiting_input',
    model: 'claude-opus-5-5',
    shortDescription: 'Which database should migration 000081 target?',
    activityStateSince: minutesAgo(3),
    readState: readState(),
  }),
  makeSession({
    id: 'webux',
    name: 'web-session-ux',
    state: 'idle',
    model: 'gpt-6-astra',
    shortDescription: 'Mockup ready for review',
    activityStateSince: minutesAgo(25),
    readState: readState({ latestOutputTurnId: 'turn-1', latestOutputAt: minutesAgo(25) }),
  }),
  makeSession({
    id: 'gates',
    name: 'plan-review-gates',
    state: 'running',
    model: 'claude-fable-5-1',
    turnStartedAt: minutesAgo(4),
    readState: readState({ isUnread: false, readThroughSeq: 14 }),
  }),
];

beforeEach(() => {
  localStorage.clear();
  navigate.mockClear();
  route.params = {};
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('Forge session rows', () => {
  it('shows title, unread dot and state, then the agent summary, then model, host and time', async () => {
    setup(sessions);
    const row = await screen.findByTestId('pod-entry-migr');

    expect(within(row).getByText('chart-migrations')).toBeInTheDocument();
    expect(within(row).getByTestId('session-unread-dot-migr')).toBeInTheDocument();
    expect(within(row).getByText('Needs you')).toHaveAttribute('data-state', 'awaiting_input');
    expect(within(row).getByTestId('session-preview-migr')).toHaveTextContent(
      'Which database should migration 000081 target?',
    );
    const model = within(row).getByText('Claude Opus 5.5');
    expect(model).toHaveAttribute('data-harness', 'claude');
    expect(within(row).getByText('Thor')).toBeInTheDocument();
    expect(within(row).getByTestId('session-time-fact-migr')).toHaveTextContent('waiting 3m');

    const codex = screen.getByTestId('pod-entry-webux');
    expect(within(codex).getByText('GPT-6 Astra')).toHaveAttribute('data-harness', 'codex');
    expect(within(codex).getByTestId('session-time-fact-webux')).toHaveTextContent('idle 25m');
    expect(screen.getByTestId('session-time-fact-gates')).toHaveTextContent('running 4m');
    expect(screen.queryByTestId('session-unread-dot-gates')).not.toBeInTheDocument();
  });

  it('keeps a row to two lines when the agent wrote no summary', async () => {
    setup([
      makeSession({
        id: 'plain',
        state: 'idle',
        model: 'claude-opus-5-5',
        source: { type: 'git', repo: 'https://github.com/niuulabs/niuu.git', branch: 'dev' },
      }),
    ]);
    const row = await screen.findByTestId('pod-entry-plain');
    expect(within(row).queryByTestId('session-preview-plain')).toBeNull();
    expect(row).not.toHaveTextContent('niuu @dev');
  });
});

describe('Today line', () => {
  it('sums the 24-hour work Forge reported', async () => {
    setup([
      makeSession({ id: 'a', state: 'idle', workedSeconds24h: 3600 }),
      makeSession({ id: 'b', state: 'idle', workedSeconds24h: 1800 }),
      makeSession({ id: 'c', state: 'idle', workedSeconds24h: 0 }),
    ]);
    await screen.findByTestId('pod-entry-a');
    expect(screen.getByTestId('sessions-today')).toHaveTextContent(
      'Last 24 h · 2 sessions · 1 h 30 m',
    );
  });

  it('says no work was recorded when every reported total is zero', async () => {
    setup([makeSession({ id: 'a', state: 'idle', workedSeconds24h: 0 })]);
    await screen.findByTestId('pod-entry-a');
    expect(screen.getByTestId('sessions-today')).toHaveTextContent('Last 24 h · no work recorded');
  });

  it('shows only the session count when no host reports work time', async () => {
    setup([
      makeSession({ id: 'a', state: 'idle' }),
      makeSession({ id: 'b', state: 'archived' }),
      makeSession({ id: 'c', state: 'running' }),
    ]);
    await screen.findByTestId('pod-entry-a');
    expect(screen.getByTestId('sessions-today')).toHaveTextContent(/^2 sessions$/);
  });
});

describe('Unread filter', () => {
  it('is the first chip, counts the unread sessions and narrows the list to them', async () => {
    setup(sessions);
    await screen.findByTestId('pod-entry-migr');
    const chips = within(screen.getByRole('group', { name: 'Session states' })).getAllByRole(
      'button',
    );
    expect(chips[0]).toHaveTextContent('Unread2');
    expect(screen.getByTestId('pod-entry-gates')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('session-filter-unread'));

    expect(screen.getByTestId('session-filter-unread')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByTestId('pod-entry-gates')).not.toBeInTheDocument();
    expect(screen.getByTestId('pod-entry-migr')).toBeInTheDocument();
    expect(screen.getByTestId('pod-entry-webux')).toBeInTheDocument();
  });

  it('stays out of the way while nothing is unread', async () => {
    setup([sessions[2]!]);
    await screen.findByTestId('pod-entry-gates');
    expect(screen.queryByTestId('session-filter-unread')).not.toBeInTheDocument();
  });

  it('shows the unread stream while no session is open', async () => {
    setup(sessions);
    await screen.findByTestId('pod-entry-migr');
    fireEvent.click(screen.getByTestId('session-filter-unread'));

    expect(await screen.findByTestId('unread-stream')).toBeInTheDocument();
    expect(screen.getByTestId('unread-stream-summary')).toHaveTextContent(
      '2 sessions have new replies',
    );
    expect(screen.queryByTestId('live-session-detail-page')).not.toBeInTheDocument();
  });

  it('leaves an open session for the stream when the chip is chosen', async () => {
    route.params = { sessionId: 'gates' };
    setup(sessions);
    await screen.findByTestId('live-session-detail-page');
    fireEvent.click(screen.getByTestId('session-filter-unread'));
    expect(navigate).toHaveBeenCalledWith({ to: '/volundr/sessions' });
  });

  it('opens a session from the stream', async () => {
    setup(sessions);
    await screen.findByTestId('pod-entry-migr');
    fireEvent.click(screen.getByTestId('session-filter-unread'));
    const block = await screen.findByTestId('unread-block-migr');
    fireEvent.click(within(block).getByRole('button', { name: 'Open session' }));
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'migr' },
    });
  });
});

describe('Row menu', () => {
  it('marks an unread session read with the revision and output it saw', async () => {
    const { changeSessionReadState } = setup(sessions);
    await screen.findByTestId('pod-entry-migr');
    fireEvent.click(screen.getByRole('button', { name: 'Mark as read: chart-migrations' }));
    await waitFor(() =>
      expect(changeSessionReadState).toHaveBeenCalledExactlyOnceWith('migr', {
        state: 'read',
        expectedRevision: 3,
        throughSeq: 14,
      }),
    );
  });

  it('marks a read session unread, and the row then shows the dot', async () => {
    const { changeSessionReadState } = setup(sessions);
    await screen.findByTestId('pod-entry-gates');
    fireEvent.click(screen.getByRole('button', { name: 'Mark as unread: plan-review-gates' }));
    await waitFor(() =>
      expect(changeSessionReadState).toHaveBeenCalledWith('gates', {
        state: 'unread',
        expectedRevision: 3,
        throughSeq: 14,
      }),
    );
    expect(await screen.findByTestId('session-unread-dot-gates')).toBeInTheDocument();
  });

  it('offers no read action for a session whose host reports no read state', async () => {
    setup([makeSession({ id: 'plain', name: 'plain', state: 'idle' })]);
    await screen.findByTestId('pod-entry-plain');
    expect(screen.queryByTestId('pod-entry-plain-read-toggle')).not.toBeInTheDocument();
  });

  it('shows a revision conflict instead of retrying', async () => {
    const changeSessionReadState = vi.fn(async () => {
      throw new SessionReadStateConflictError();
    });
    setup(sessions, { changeSessionReadState });
    await screen.findByTestId('pod-entry-migr');
    fireEvent.click(screen.getByRole('button', { name: 'Mark as read: chart-migrations' }));
    expect(await screen.findByTestId('session-read-error')).toHaveTextContent(
      'marked read or unread elsewhere',
    );
    expect(changeSessionReadState).toHaveBeenCalledTimes(1);
    fireEvent.click(within(screen.getByTestId('session-read-error')).getByText('Dismiss'));
    expect(screen.queryByTestId('session-read-error')).not.toBeInTheDocument();
  });
});

describe('Opening a session', () => {
  it('marks an unread session read once', async () => {
    route.params = { sessionId: 'migr' };
    const { changeSessionReadState } = setup(sessions);
    await screen.findByTestId('live-session-detail-page');
    await waitFor(() =>
      expect(changeSessionReadState).toHaveBeenCalledExactlyOnceWith('migr', {
        state: 'read',
        expectedRevision: 3,
        throughSeq: 14,
      }),
    );
    // The list refreshes with the session read; it is not read again.
    await screen.findByTestId('pod-entry-migr');
    await waitFor(() =>
      expect(screen.queryByTestId('session-unread-dot-migr')).not.toBeInTheDocument(),
    );
    expect(changeSessionReadState).toHaveBeenCalledTimes(1);
  });

  it('does not read the session shown by default when the page opens', async () => {
    route.params = {};
    const unreadFirst = sessions.map((session) =>
      session.id === 'migr' ? session : { ...session, state: 'idle' as const },
    );
    const { changeSessionReadState } = setup(unreadFirst);
    await screen.findByTestId('pod-entry-migr');
    await screen.findByTestId('live-session-detail-page');
    expect(changeSessionReadState).not.toHaveBeenCalled();
  });

  it('leaves a read session alone', async () => {
    route.params = { sessionId: 'gates' };
    const { changeSessionReadState } = setup(sessions);
    await screen.findByTestId('live-session-detail-page');
    await screen.findByTestId('pod-entry-gates');
    expect(changeSessionReadState).not.toHaveBeenCalled();
  });

  it('reports a failure to mark it read', async () => {
    route.params = { sessionId: 'migr' };
    setup(sessions, {
      changeSessionReadState: vi.fn(async () => {
        throw new Error('Forge is down');
      }),
    });
    expect(await screen.findByTestId('session-read-error')).toHaveTextContent('Forge is down');
  });
});

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import { SessionsView } from './SessionsView';
import {
  createMockBudgetStream,
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';
import type { ISessionStream } from '../ports';
import type { Session } from '../domain/session';

const wrap = wrapWithServices;

const useSkuldChatMock = vi.hoisted(() => vi.fn());

// Replace only the Skuld chat hook — jsdom has no WebSocket, and the live chat
// surface must render without opening a real connection. Everything else stays real.
vi.mock('@niuulabs/ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@niuulabs/ui')>();
  return { ...actual, useSkuldChat: useSkuldChatMock };
});

function makeChatState(overrides: Record<string, unknown> = {}) {
  return {
    messages: [],
    streamingContent: undefined,
    streamingParts: undefined,
    streamingModel: undefined,
    connected: true,
    historyLoaded: true,
    participants: new Map(),
    meshEvents: [],
    agentEvents: new Map(),
    pendingPermissions: [],
    availableCommands: [],
    capabilities: {},
    sendMessage: vi.fn(),
    sendDirectedMessages: vi.fn(),
    sendResendPrompt: vi.fn(),
    respondToPermission: vi.fn(),
    sendInterrupt: vi.fn(),
    sendSetModel: vi.fn(),
    sendSetThinkingTokens: vi.fn(),
    sendRewindFiles: vi.fn(),
    sendSetInternalVisibility: vi.fn(),
    clearMessages: vi.fn(),
    ...overrides,
  };
}

const LIVE_CHAT_ENDPOINT = 'wss://skuld.example/s/live-1/session';

/** A single-session stream so we can drive live vs. read-only surfaces. */
function singleSessionStream(session: Session): ISessionStream {
  return {
    async listSessions() {
      return [session];
    },
    async getSession() {
      return session;
    },
    async getMessages() {
      return [];
    },
  };
}

function liveRunningSession(overrides: Partial<Session> = {}): Session {
  return {
    id: '10000001-0000-4000-8000-0000000000aa',
    ravnId: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
    personaName: 'sindri',
    personaRole: 'build',
    personaLetter: 'S',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T09:00:00Z',
    title: 'Live resident chat',
    chatEndpoint: LIVE_CHAT_ENDPOINT,
    ...overrides,
  };
}

function services() {
  return {
    'ravn.sessions': createMockSessionStream(),
    'ravn.ravens': createMockRavenStream(),
    'ravn.personas': createMockPersonaStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

function servicesWith(sessionStream: ISessionStream) {
  return {
    'ravn.sessions': sessionStream,
    'ravn.ravens': createMockRavenStream(),
    'ravn.personas': createMockPersonaStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

beforeEach(() => {
  localStorage.clear();
  useSkuldChatMock.mockReset();
  useSkuldChatMock.mockImplementation(() => makeChatState());
});

describe('SessionsView', () => {
  it('shows loading state initially', () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it('renders the page-owned sessions rail after loading', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-page')).toBeInTheDocument());
    expect(screen.getByText(/10 active/i)).toBeInTheDocument();
    expect(screen.getByText(/2 closed/i)).toBeInTheDocument();
  });

  it('selects the newest running session by default and shows the header', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const header = await screen.findByTestId('sessions-header');
    expect(
      within(header).getByRole('heading', { name: 'Run integration tests' }),
    ).toBeInTheDocument();
    expect(within(header).getByText(/trigger:/i)).toBeInTheDocument();
  });

  it('clicking a rail item selects that session', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const target = await screen.findByRole('button', {
      name: 'Open session Review PR #142',
    });
    fireEvent.click(target);
    expect(target).toHaveAttribute('aria-pressed', 'true');
    const header = await screen.findByTestId('sessions-header');
    expect(within(header).getByRole('heading', { name: 'Review PR #142' })).toBeInTheDocument();
  });

  it('responds to ravn:session-selected events', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-page')).toBeInTheDocument());

    act(() => {
      window.dispatchEvent(
        new CustomEvent('ravn:session-selected', {
          detail: '10000001-0000-4000-8000-000000000005',
        }),
      );
    });

    const header = await screen.findByTestId('sessions-header');
    expect(within(header).getByRole('heading', { name: 'Review PR #142' })).toBeInTheDocument();
  });

  it('persists selection to localStorage', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const target = await screen.findByRole('button', {
      name: 'Open session Security audit — API endpoints',
    });
    fireEvent.click(target);
    expect(localStorage.getItem('ravn.session')).toBe('"10000001-0000-4000-8000-000000000004"');
  });

  it('renders transcript toolbar filters', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const group = await screen.findByRole('group', { name: /session transcript filter/i });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'all' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'chat only' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ tools' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ system' })).toBeInTheDocument();
  });

  it('chat only filter hides the system init line', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const reviewSession = await screen.findByRole('button', {
      name: 'Open session Implement login form',
    });
    fireEvent.click(reviewSession);
    const log = await screen.findByRole('log', { name: /session transcript/i });
    expect(within(log).getByText(/session init/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'chat only' }));

    await waitFor(() => {
      expect(within(log).queryByText(/session init/i)).not.toBeInTheDocument();
      expect(within(log).getByText(/Please implement the login form/i)).toBeInTheDocument();
    });
  });

  it('shows context cards for summary, timeline, injects, emissions, and raven', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-context')).toBeInTheDocument());
    expect(screen.getByTestId('sessions-summary')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-timeline')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-injects')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-emissions')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-raven-card')).toBeInTheDocument();
  });

  it('shows a running composer for active sessions', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    expect(await screen.findByTestId('sessions-composer')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /session message composer/i })).toBeInTheDocument();
  });

  it('shows the read-only composer variant for closed sessions', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const closedSession = await screen.findByRole('button', {
      name: 'Open session Monitor overnight alerts',
    });
    fireEvent.click(closedSession);
    expect(await screen.findByTestId('sessions-composer-closed')).toBeInTheDocument();
  });
});

// ── Live SessionChat surface ─────────────────────────────────────────────────

describe('SessionsView — live chat', () => {
  it('renders the shared SessionChat for a running session with a chatEndpoint', async () => {
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession()))),
    });
    expect(await screen.findByTestId('sessions-live-chat')).toBeInTheDocument();
    expect(screen.getByTestId('session-chat')).toBeInTheDocument();
    expect(useSkuldChatMock).toHaveBeenCalledWith(LIVE_CHAT_ENDPOINT);
    // The synthesized read-only transcript must NOT be present.
    expect(screen.queryByTestId('sessions-composer')).not.toBeInTheDocument();
    expect(screen.queryByRole('log', { name: /session transcript/i })).not.toBeInTheDocument();
  });

  it('drives SessionChat from the useSkuldChat hook messages', async () => {
    useSkuldChatMock.mockImplementation(() =>
      makeChatState({
        messages: [
          {
            id: 'm1',
            role: 'assistant',
            content: 'Live from the resident.',
            createdAt: new Date('2026-07-01T10:00:00Z'),
            status: 'done',
          },
        ],
      }),
    );
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession()))),
    });
    await screen.findByTestId('sessions-live-chat');
    expect(screen.getByText('Live from the resident.')).toBeInTheDocument();
  });

  it('sends via the hook sendMessage callback', async () => {
    const sendMessage = vi.fn();
    useSkuldChatMock.mockImplementation(() => makeChatState({ sendMessage }));
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession()))),
    });
    await screen.findByTestId('sessions-live-chat');
    // The connected empty state offers suggestion chips wired to onSend.
    fireEvent.click(screen.getByText('Review the code and suggest improvements'));
    expect(sendMessage).toHaveBeenCalledWith('Review the code and suggest improvements', []);
  });

  it('keeps the read-only transcript for a running session without a chatEndpoint', async () => {
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession({ chatEndpoint: null })))),
    });
    expect(await screen.findByTestId('sessions-composer')).toBeInTheDocument();
    expect(screen.queryByTestId('sessions-live-chat')).not.toBeInTheDocument();
    expect(useSkuldChatMock).not.toHaveBeenCalled();
  });

  it('keeps the read-only surface for a stopped session even with a chatEndpoint', async () => {
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession({ status: 'stopped' })))),
    });
    expect(await screen.findByTestId('sessions-composer-closed')).toBeInTheDocument();
    expect(screen.queryByTestId('sessions-live-chat')).not.toBeInTheDocument();
    expect(useSkuldChatMock).not.toHaveBeenCalled();
  });
});

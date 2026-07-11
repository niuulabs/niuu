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

vi.mock('@niuulabs/plugin-volundr', () => ({
  TelemetryTab: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="volundr-trace-tab">trace {sessionId}</div>
  ),
  LiveLogsTab: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="volundr-logs-tab">logs {sessionId}</div>
  ),
}));

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

function servicesWithVolundr(sessionStream: ISessionStream) {
  return {
    ...servicesWith(sessionStream),
    volundr: {},
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
    expect(screen.getByText(/1 idle/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Open session Monitor overnight alerts' }),
    ).toBeNull();
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

  it('collapses and expands the sessions rail', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const collapse = await screen.findByRole('button', { name: /collapse sessions sidebar/i });

    fireEvent.click(collapse);
    expect(screen.getByRole('button', { name: /expand sessions sidebar/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /open session/i }).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /expand sessions sidebar/i }));
    expect(screen.getByRole('button', { name: /collapse sessions sidebar/i })).toBeInTheDocument();
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

  it('responds to legacy object-shaped ravn:session-selected events', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-page')).toBeInTheDocument());

    act(() => {
      window.dispatchEvent(
        new CustomEvent('ravn:session-selected', {
          detail: { sessionId: '10000001-0000-4000-8000-000000000005' },
        }),
      );
    });

    const header = await screen.findByTestId('sessions-header');
    expect(within(header).getByRole('heading', { name: 'Review PR #142' })).toBeInTheDocument();
  });

  it('selects a session from the route query param', async () => {
    window.history.replaceState(
      null,
      '',
      '/ravn/sessions?session=10000001-0000-4000-8000-000000000005',
    );
    render(<SessionsView />, { wrapper: wrap(services()) });

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

  it('omits stopped sessions from the live control-room rail', async () => {
    render(<SessionsView />, {
      wrapper: wrap(
        servicesWith(
          singleSessionStream(liveRunningSession({ status: 'stopped', chatEndpoint: null })),
        ),
      ),
    });
    expect(await screen.findByTestId('sessions-empty')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /open session/i })).toBeNull();
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
    expect(useSkuldChatMock).toHaveBeenCalledWith(LIVE_CHAT_ENDPOINT, {
      historyMode: 'none',
    });
    // The synthesized read-only transcript must NOT be present.
    expect(screen.queryByTestId('sessions-composer')).not.toBeInTheDocument();
    expect(screen.queryByRole('log', { name: /session transcript/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId('sessions-context')).not.toBeInTheDocument();
  });

  it('uses websocket-owned history for engine-native resident sessions', async () => {
    const resident = {
      id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
      personaName: '',
      residentName: 'valaskjalf-qwen-proof',
      kind: 'resident' as const,
      status: 'active' as const,
      model: 'niuu/Qwen/Qwen3.6-35B-A3B-FP8',
      createdAt: '2026-07-11T15:04:14Z',
    };
    const ravenStream = {
      async listRavens() {
        return [resident];
      },
      async getRaven() {
        return resident;
      },
    };
    const personaStore = createMockPersonaStore();
    const getPersona = vi.spyOn(personaStore, 'getPersona');
    const sessionStream = singleSessionStream(
      liveRunningSession({
        personaName: 'valaskjalf-qwen-proof',
        model: resident.model,
      }),
    );
    const getMessages = vi.spyOn(sessionStream, 'getMessages');

    render(<SessionsView />, {
      wrapper: wrap({
        'ravn.sessions': sessionStream,
        'ravn.ravens': ravenStream,
        'ravn.personas': personaStore,
        'ravn.budget': createMockBudgetStream(),
      }),
    });

    expect(await screen.findByTestId('sessions-live-chat')).toBeInTheDocument();
    expect(useSkuldChatMock).toHaveBeenCalledWith(LIVE_CHAT_ENDPOINT, {
      historyMode: 'none',
    });
    expect(getPersona).not.toHaveBeenCalled();
    expect(getMessages).not.toHaveBeenCalled();
  });

  it('mounts Volundr trace and logs tabs for the selected live session', async () => {
    const session = liveRunningSession();
    render(<SessionsView />, {
      wrapper: wrap(servicesWithVolundr(singleSessionStream(session))),
    });

    expect(await screen.findByTestId('sessions-live-chat')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /trace/i }));
    expect(await screen.findByTestId('volundr-trace-tab')).toHaveTextContent(session.id);

    fireEvent.click(screen.getByRole('tab', { name: /logs/i }));
    expect(await screen.findByTestId('volundr-logs-tab')).toHaveTextContent(session.id);
  });

  it('uses the live resident persona for header metadata and emissions', async () => {
    render(<SessionsView />, {
      wrapper: wrap(
        servicesWith(singleSessionStream(liveRunningSession({ personaName: 'product-steward' }))),
      ),
    });

    const header = await screen.findByTestId('sessions-header');
    expect(within(header).getByText('product-steward')).toBeInTheDocument();
    expect(within(header).queryByText('coder')).not.toBeInTheDocument();
    expect(screen.queryByTestId('sessions-context')).not.toBeInTheDocument();
    expect(screen.queryByText('code.changed')).not.toBeInTheDocument();
  });

  it('toggles internal visibility for live chat sessions', async () => {
    const sendSetInternalVisibility = vi.fn();
    useSkuldChatMock.mockImplementation(() => makeChatState({ sendSetInternalVisibility }));
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession()))),
    });
    const toggle = await screen.findByTestId('internal-toggle');

    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(sendSetInternalVisibility).toHaveBeenCalledWith(true);
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

  it('sends @mentioned resident messages through the directed hook callback', async () => {
    const sendDirectedMessages = vi.fn();
    const productSteward = {
      peerId: 'flock-product-steward',
      persona: 'product-steward',
      displayName: 'Muninn',
      participantType: 'ravn',
    };
    useSkuldChatMock.mockImplementation(() =>
      makeChatState({
        participants: new Map([[productSteward.peerId, productSteward]]),
        sendDirectedMessages,
      }),
    );
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession()))),
    });

    await screen.findByTestId('sessions-live-chat');
    fireEvent.change(screen.getByTestId('chat-textarea'), {
      target: { value: '@product-steward research solvent defaults' },
    });
    fireEvent.click(screen.getByTestId('send-btn'));

    expect(sendDirectedMessages).toHaveBeenCalledWith(
      [productSteward],
      '@product-steward research solvent defaults',
      [],
    );
  });

  it('keeps the read-only transcript for a running session without a chatEndpoint', async () => {
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession({ chatEndpoint: null })))),
    });
    expect(await screen.findByTestId('sessions-composer')).toBeInTheDocument();
    expect(screen.queryByTestId('sessions-live-chat')).not.toBeInTheDocument();
    expect(useSkuldChatMock).not.toHaveBeenCalled();
  });

  it('hides stopped sessions even when a stale chatEndpoint is present', async () => {
    render(<SessionsView />, {
      wrapper: wrap(servicesWith(singleSessionStream(liveRunningSession({ status: 'stopped' })))),
    });
    expect(await screen.findByTestId('sessions-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('sessions-live-chat')).not.toBeInTheDocument();
    expect(useSkuldChatMock).not.toHaveBeenCalled();
  });
});

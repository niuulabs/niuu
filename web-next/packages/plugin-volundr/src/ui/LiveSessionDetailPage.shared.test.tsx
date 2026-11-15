import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ChatConnectionsContext } from '@niuulabs/ui';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import * as chatHooks from './hooks/useSkuldChat';
import {
  createMockVolundrService,
  createMockSessionStore,
  createMockMetricsStream,
} from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import type { ISessionStore } from '../ports/ISessionStore';
import type { VolundrSession } from '../models/volundr.model';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const navigate = vi.fn();

const sessionChatProps = vi.hoisted(() => vi.fn());

// The real SessionChat still renders; the spy only records the props the page hands it.
vi.mock('@niuulabs/ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@niuulabs/ui')>();
  return {
    ...actual,
    SessionChat: (props: React.ComponentProps<typeof actual.SessionChat>) => {
      sessionChatProps(props);
      return <actual.SessionChat {...props} />;
    },
  };
});

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

vi.mock('@xterm/xterm', () => ({
  Terminal: class MockXtermTerminal {
    open = vi.fn();
    write = vi.fn();
    dispose = vi.fn();
    loadAddon = vi.fn();
    onData = vi.fn().mockReturnValue({ dispose: vi.fn() });
    onResize = vi.fn().mockReturnValue({ dispose: vi.fn() });
    focus = vi.fn();
    refresh = vi.fn();
    cols = 80;
    rows = 24;
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

const resizeCallbacks: ResizeObserverCallback[] = [];
class ResizeObserverStub {
  constructor(callback: ResizeObserverCallback) {
    resizeCallbacks.push(callback);
  }
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

function buildPtyStream(): IPtyStream {
  return {
    subscribe: vi.fn().mockReturnValue(() => {}),
    send: vi.fn(),
  };
}

function buildFilesystem(): IFileSystemPort {
  return {
    listTree: vi.fn().mockResolvedValue([]),
    expandDirectory: vi.fn().mockResolvedValue([]),
    readFile: vi.fn().mockResolvedValue(''),
  };
}

const SESSION_FEATURES = [
  {
    key: 'chat',
    label: 'Chat',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 10,
  },
  {
    key: 'terminal',
    label: 'Terminal',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 20,
  },
  {
    key: 'files',
    label: 'Files',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 40,
  },
  {
    key: 'chronicles',
    label: 'Chronicle',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 50,
  },
  {
    key: 'telemetry',
    label: 'Telemetry',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 55,
  },
  {
    key: 'logs',
    label: 'Logs',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 60,
  },
];

function mockChatState(overrides: Partial<ReturnType<typeof chatHooks.useSkuldChat>> = {}) {
  vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue({
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
    pendingInputRequests: [],
    capabilities: {},
    previewPorts: null,
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
    plan: null,
    agents: [],
    effort: null,
    runtimeOptions: null,
    usage: null,
    sendSetEffort: vi.fn(),
    sendSetRuntimeOptions: vi.fn(),
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// A session shared through a room: no source, and the room says what the caller may do
// ---------------------------------------------------------------------------

const PROJECT_ID = 'proj-lexi';

function sharedSession(role: 'viewer' | 'approver'): VolundrSession {
  return {
    id: 'shared-session-1',
    name: 'alice-feature',
    source: null,
    room: { role, participantStatus: 'active', expiresAt: null },
    status: 'running',
    activityState: 'awaiting_input',
    needsAttention: true,
    model: 'claude-sonnet-4-6',
    lastActive: Date.now() - 60_000,
    messageCount: 4,
    tokensUsed: 0,
    ownerId: 'alice',
    instanceName: 'Guild Alpha',
    chatEndpoint: 'wss://forge.test/s/shared-session-1/session',
    coordination: { projectId: PROJECT_ID, role: 'worker' },
  };
}

function build(session: VolundrSession): IVolundrService {
  const base = createMockVolundrService();
  return {
    ...base,
    getSessionParticipants: vi.fn().mockResolvedValue([
      {
        userId: 'u1',
        displayName: 'Dev',
        role: 'viewer',
        status: 'active',
        invitedBy: 'alice',
        expiresAt: null,
      },
    ]),
    removeSessionParticipant: vi.fn().mockResolvedValue(undefined),
    getSessions: vi.fn().mockResolvedValue([session]),
    getSession: vi.fn().mockResolvedValue(session),
    getFeatureModules: vi.fn().mockResolvedValue(SESSION_FEATURES),
    getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
    getProjects: vi
      .fn()
      .mockResolvedValue([{ id: PROJECT_ID, name: 'Lexi', slug: 'lexi', status: 'active' }]),
    getProjectMembers: vi.fn().mockResolvedValue([
      {
        project_id: PROJECT_ID,
        user_id: 'alice',
        role: 'owner',
        expertise: '',
        skills: [],
        display_name: 'Alice Smith',
        email: 'alice@example.test',
      },
    ]),
  };
}

function renderShared(session: VolundrSession) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const base = createMockSessionStore();
  const sessionStore: ISessionStore = {
    ...base,
    getSession: vi.fn().mockResolvedValue({
      id: session.id,
      ravnId: 'alice',
      name: session.name,
      personaName: session.name,
      state: 'awaiting_input',
      clusterId: 'local',
      startedAt: new Date(Date.now() - 60_000).toISOString(),
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
    }),
  };
  const volundr = build(session);
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          ptyStream: buildPtyStream(),
          filesystem: buildFilesystem(),
          sessionStore,
          metricsStream: createMockMetricsStream(),
        }}
      >
        <ChatConnectionsContext.Provider value={vi.fn()}>
          <LiveSessionDetailPage sessionId={session.id} />
        </ChatConnectionsContext.Provider>
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return volundr;
}

const PERMISSION = {
  requestId: 'perm-1',
  toolName: 'Bash',
  description: 'Run a command',
  command: 'ls',
};
const QUESTION = {
  requestId: 'input-1',
  questions: [{ prompt: 'Which branch should I use?', choices: [] }],
};

describe('LiveSessionDetailPage for a session shared through a room', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('niuu.forge.sessionTabs', 'chat,diffs,files,chronicles,telemetry,logs');
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('opens from the summary: chat connects to the proxied endpoint and the composer works', async () => {
    const sendMessage = vi.fn();
    mockChatState({ sendMessage });
    const spy = vi.spyOn(chatHooks, 'useSkuldChat');
    renderShared(sharedSession('viewer'));

    const composer = await screen.findByRole('textbox');
    expect(spy).toHaveBeenCalledWith(
      'wss://forge.test/s/shared-session-1/session',
      expect.anything(),
    );
    fireEvent.change(composer, { target: { value: 'try the other branch' } });
    fireEvent.keyDown(composer, { key: 'Enter' });
    expect(sendMessage).toHaveBeenCalledWith('try the other branch', expect.anything());
  });

  it("names the role and the owner, and offers none of the owner's panels or actions", async () => {
    mockChatState();
    renderShared(sharedSession('viewer'));

    const badge = await screen.findByTestId('session-shared-badge');
    expect(badge).toHaveTextContent('Shared · viewer');
    await screen.findByText(/Alice Smith/);
    // Only the conversation: no workspace, files, records, terminal or dock.
    const tabs = within(screen.getByRole('tablist', { name: 'Session tabs' })).getAllByRole('tab');
    expect(tabs).toHaveLength(1);
    expect(tabs[0]).toHaveTextContent(/^Chat/);
    expect(screen.queryByTitle(/the terminal/)).not.toBeInTheDocument();
    expect(screen.queryByTitle(/the dock/)).not.toBeInTheDocument();
    // Neither the owner's lifecycle nor the menu entries that change the session.
    expect(screen.queryByTitle('Stop session')).not.toBeInTheDocument();
    // Rename and file under a project are the owner's; with nothing left, the menu is gone.
    expect(screen.queryByRole('button', { name: /^Rename / })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Assign project/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
  });

  it('shows a viewer that an approver must answer, with nothing to answer it with', async () => {
    const respondToPermission = vi.fn();
    mockChatState({
      respondToPermission,
      pendingPermissions: [PERMISSION],
      pendingInputRequests: [QUESTION],
    });
    renderShared(sharedSession('viewer'));

    expect(await screen.findByTestId('permission-awaiting-approver')).toHaveTextContent(
      'an approver of this session must answer',
    );
    expect(screen.queryByRole('button', { name: 'deny' })).not.toBeInTheDocument();
    expect(screen.queryByText('Which branch should I use?')).not.toBeInTheDocument();
    expect(respondToPermission).not.toHaveBeenCalled();
  });

  it("lets an approver answer the permission prompt, still without the owner's controls", async () => {
    const respondToPermission = vi.fn();
    mockChatState({ respondToPermission, pendingPermissions: [PERMISSION] });
    renderShared(sharedSession('approver'));

    expect(await screen.findByTestId('session-shared-badge')).toHaveTextContent(
      'Shared · approver',
    );
    fireEvent.click(await screen.findByRole('button', { name: 'deny' }));

    expect(respondToPermission).toHaveBeenCalledWith('perm-1', 'deny');
    expect(screen.queryByTestId('permission-awaiting-approver')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Stop session')).not.toBeInTheDocument();
    expect(screen.queryByTitle(/the terminal/)).not.toBeInTheDocument();
  });

  it('does not count what only an approver can answer as needing a viewer', async () => {
    mockChatState({ pendingPermissions: [PERMISSION] });
    renderShared(sharedSession('viewer'));

    await screen.findByTestId('permission-awaiting-approver');
    // The panel's button would read "Needs you 1" for someone who could answer.
    expect(screen.getByRole('button', { name: 'Show session panel' })).not.toHaveTextContent(
      'Needs you',
    );
  });

  describe('the people in the chat', () => {
    it('gives a participant a People button to see who is in it and to leave', async () => {
      mockChatState();
      const volundr = renderShared(sharedSession('viewer'));

      fireEvent.click(await screen.findByRole('button', { name: 'People' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Leave chat' }));

      await waitFor(() =>
        expect(volundr.removeSessionParticipant).toHaveBeenCalledWith(
          'shared-session-1',
          'u1',
          expect.anything(),
        ),
      );
      await waitFor(() =>
        expect(navigate).toHaveBeenCalledWith({ to: '/volundr/sessions', replace: true }),
      );
    });
  });

  describe('a question the agent put to a person', () => {
    const QUESTION_MESSAGE = {
      id: 'pq_q-1',
      role: 'assistant' as const,
      content: 'Asked Carol: Which branch?',
      createdAt: new Date(0),
      status: 'done' as const,
      parts: [
        {
          type: 'tool_use' as const,
          id: 'q-1',
          name: 'ask_person',
          input: {
            question_id: 'q-1',
            user_id: 'carol',
            display_name: 'Carol',
            role: 'viewer',
            question: 'Which branch?',
          },
        },
      ],
    };

    it('lets an approver answer it, sending person_answer through the chat', async () => {
      const sendPersonAnswer = vi.fn();
      mockChatState({
        messages: [QUESTION_MESSAGE],
        sendPersonAnswer,
        personAnswers: new Map(),
        personAnswerErrors: new Map(),
        personAnswersSending: new Set(),
      } as never);
      renderShared(sharedSession('approver'));

      fireEvent.change(await screen.findByRole('textbox', { name: /Answer Carol/ }), {
        target: { value: 'Use main' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));

      expect(sendPersonAnswer).toHaveBeenCalledWith('q-1', 'Use main');
    });

    it('shows a viewer the question and who can answer it, with no box', async () => {
      mockChatState({
        messages: [QUESTION_MESSAGE],
        sendPersonAnswer: vi.fn(),
        personAnswers: new Map(),
        personAnswerErrors: new Map(),
        personAnswersSending: new Set(),
      } as never);
      renderShared(sharedSession('viewer'));

      expect(await screen.findByTestId('person-question-who-can-answer')).toBeInTheDocument();
      expect(screen.queryByRole('textbox', { name: /Answer Carol/ })).not.toBeInTheDocument();
    });

    it('shows who answered it when the answer came back', async () => {
      mockChatState({
        messages: [QUESTION_MESSAGE],
        sendPersonAnswer: vi.fn(),
        personAnswers: new Map([
          ['q-1', { content: 'Use main', answeredBy: { userId: 'dan', displayName: 'Dan' } }],
        ]),
        personAnswerErrors: new Map(),
        personAnswersSending: new Set(),
      } as never);
      renderShared(sharedSession('viewer'));

      expect(await screen.findByTestId('person-question-answer')).toHaveTextContent('Dan');
    });
  });
});

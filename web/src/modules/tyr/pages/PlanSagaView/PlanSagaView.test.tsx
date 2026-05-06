import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { PlanSagaView } from './PlanSagaView';

/* ------------------------------------------------------------------ */
/*  Shared mock data                                                  */
/* ------------------------------------------------------------------ */

const mockSendMessage = vi.fn();
let mockSkuldMessages: Array<{
  id: string;
  role: string;
  content: string;
  status: string;
}> = [];

vi.mock('@/modules/shared/hooks/useSkuldChat', () => ({
  useSkuldChat: () => ({
    messages: mockSkuldMessages,
    connected: true,
    isRunning: false,
    historyLoaded: true,
    pendingPermissions: [],
    availableCommands: [],
    sendMessage: mockSendMessage,
    respondToPermission: vi.fn(),
    sendInterrupt: vi.fn(),
    sendSetModel: vi.fn(),
    sendSetMaxThinkingTokens: vi.fn(),
    sendRewindFiles: vi.fn(),
    clearMessages: vi.fn(),
  }),
}));

vi.mock('@/modules/shared/components/SessionChat', () => ({
  SessionChat: ({ url }: { url: string | null }) => (
    <div data-testid="session-chat">{url ? `Connected to ${url}` : 'No URL'}</div>
  ),
}));

const mockSpawnPlanSession = vi.fn(() =>
  Promise.resolve({
    session_id: 'plan-sess-001',
    chat_endpoint: 'wss://sessions.test/s/plan-sess-001/session',
  })
);
const mockCommitSaga = vi.fn(() => Promise.resolve({ id: 'saga-001' }));
const mockExtractStructure = vi.fn(() => Promise.resolve({ found: false, structure: null }));

vi.mock('../../adapters', () => ({
  tyrService: {
    spawnPlanSession: (...args: unknown[]) => mockSpawnPlanSession(...args),
    decompose: vi.fn(() => Promise.resolve([])),
    commitSaga: (...args: unknown[]) => mockCommitSaga(...args),
    extractStructure: (...args: unknown[]) => mockExtractStructure(...args),
  },
}));

const mockSessions = [
  {
    id: 'sess-1',
    name: 'plan-yggdrasil',
    model: 'claude-sonnet-4-6',
    status: 'running',
    chat_endpoint: 'wss://sessions.test/s/sess-1/session',
    task_type: 'planner',
    source: { type: 'git', repo: 'niuu/volundr', branch: 'plan-ygg', base_branch: 'main' },
  },
  {
    id: 'sess-2',
    name: 'plan-tyr-pipeline',
    model: 'claude-sonnet-4-6',
    status: 'stopped',
    chat_endpoint: null,
    task_type: 'planner',
  },
];

const mockVolundrGet = vi.fn();
const mockVolundrDelete = vi.fn(() => Promise.resolve());
const mockTyrGet = vi.fn();

vi.mock('@/modules/shared/api/client', () => ({
  createApiClient: (basePath: string) => {
    if (basePath.includes('tyr')) {
      return {
        get: (...args: unknown[]) => mockTyrGet(...args),
        post: vi.fn(),
        delete: vi.fn(),
      };
    }
    return {
      get: (...args: unknown[]) => mockVolundrGet(...args),
      post: vi.fn(),
      delete: (...args: unknown[]) => mockVolundrDelete(...args),
    };
  },
  getAccessToken: () => 'test-token',
}));

const mockRepos = [
  {
    provider: 'github',
    org: 'niuu',
    name: 'volundr',
    clone_url: 'https://github.com/niuu/volundr.git',
    url: 'https://github.com/niuu/volundr',
    default_branch: 'main',
    branches: ['main', 'dev'],
  },
];

vi.mock('../../hooks/useRepos', () => ({
  useRepos: () => ({
    repos: mockRepos,
    loading: false,
    error: null,
  }),
}));

vi.mock('../../components/RepoSelector', () => ({
  RepoSelector: ({
    onSelect,
    value,
  }: {
    onSelect: (v: string) => void;
    value: string;
    repos: unknown[];
    mode: string;
    showBranch: boolean;
  }) => (
    <select data-testid="repo-selector" value={value} onChange={e => onSelect(e.target.value)}>
      <option value="">Select repo</option>
      <option value="https://github.com/niuu/volundr">niuu/volundr</option>
    </select>
  ),
}));

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function renderView(initialPath = '/tyr/new') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/tyr/new" element={<PlanSagaView />} />
        <Route path="/tyr/sagas/:id" element={<div data-testid="saga-detail">Saga Detail</div>} />
      </Routes>
    </MemoryRouter>
  );
}

const sampleStructure = {
  name: 'Auth Overhaul',
  phases: [
    {
      name: 'Phase 1 - Foundation',
      raids: [
        {
          name: 'Setup OIDC adapter',
          description: 'Add OIDC adapter for identity',
          acceptance_criteria: ['OIDC flow works', 'Tokens validated'],
          declared_files: ['src/adapters/oidc.ts'],
          estimate_hours: 4,
          confidence: 0.9,
        },
        {
          name: 'Create user model',
          description: 'Domain model for authenticated users',
          acceptance_criteria: ['Model validates email'],
          declared_files: ['src/models/user.ts'],
          estimate_hours: 2,
          confidence: 0.8,
        },
      ],
    },
    {
      name: 'Phase 2 - Integration',
      raids: [
        {
          name: 'Wire middleware',
          description: 'Connect auth middleware to routes',
          acceptance_criteria: ['Protected routes require auth'],
          declared_files: ['src/middleware/auth.ts'],
          estimate_hours: 3,
          confidence: 0.85,
        },
      ],
    },
  ],
};

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe('PlanSagaView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSkuldMessages = [];
    mockVolundrGet.mockResolvedValue([]);
    mockTyrGet.mockResolvedValue({ finalize_prompt: 'Please finalize the plan as JSON.' });
  });

  /* ---- Basic rendering ---- */

  it('renders the planning heading', async () => {
    renderView();
    expect(screen.getByText('Planning')).toBeInTheDocument();
  });

  it('shows new session button', async () => {
    renderView();
    await waitFor(() => {
      expect(screen.getByText('New Session')).toBeInTheDocument();
    });
  });

  it('shows empty state when no sessions', async () => {
    renderView();
    await waitFor(() => {
      expect(screen.getByText(/no planning sessions yet/i)).toBeInTheDocument();
    });
  });

  it('shows prompt to start session when no sessions and no active session', async () => {
    renderView();
    await waitFor(() => {
      expect(screen.getByText('Start a new planning session to begin')).toBeInTheDocument();
    });
  });

  /* ---- Session list ---- */

  it('renders session list when sessions exist', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('plan-yggdrasil')).toBeInTheDocument();
      expect(screen.getByText('plan-tyr-pipeline')).toBeInTheDocument();
    });
  });

  it('auto-selects first session when sessions load', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toBeInTheDocument();
    });
  });

  it('shows stop button only for running sessions', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('plan-yggdrasil')).toBeInTheDocument();
    });

    // running session has stop button with title
    const stopButtons = screen.getAllByTitle('Stop session');
    expect(stopButtons.length).toBe(1);
  });

  it('handles selecting a different session', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      // Initially only one instance of plan-tyr-pipeline (in sidebar)
      expect(screen.getAllByText('plan-tyr-pipeline')).toHaveLength(1);
    });

    await user.click(screen.getByText('plan-tyr-pipeline'));

    // After clicking, the session name appears in both sidebar AND chat header
    await waitFor(() => {
      expect(screen.getAllByText('plan-tyr-pipeline')).toHaveLength(2);
    });
  });

  it('stops a session via the stop button in session list', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByTitle('Stop session')).toBeInTheDocument();
    });

    await user.click(screen.getByTitle('Stop session'));

    expect(mockVolundrDelete).toHaveBeenCalledWith('/sessions/sess-1');
  });

  it('shows error when stopping a session fails', async () => {
    const user = userEvent.setup();
    mockVolundrDelete.mockRejectedValueOnce(new Error('Network error'));
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByTitle('Stop session')).toBeInTheDocument();
    });

    await user.click(screen.getByTitle('Stop session'));

    await waitFor(() => {
      expect(screen.getByText('Failed to stop session')).toBeInTheDocument();
    });
  });

  it('filters sessions to only show planner types', async () => {
    const mixedSessions = [
      ...mockSessions,
      {
        id: 'sess-3',
        name: 'regular-session',
        model: 'claude-sonnet-4-6',
        status: 'running',
        chat_endpoint: null,
        task_type: 'general',
      },
    ];
    mockVolundrGet.mockResolvedValue(mixedSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('plan-yggdrasil')).toBeInTheDocument();
    });

    expect(screen.queryByText('regular-session')).not.toBeInTheDocument();
  });

  /* ---- New session form ---- */

  it('shows new session form when button clicked', async () => {
    const user = userEvent.setup();
    renderView();

    await waitFor(() => {
      expect(screen.getByText('New Session')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New Session'));

    expect(screen.getByText(/what do you want to build/i)).toBeInTheDocument();
    expect(screen.getByText('Start Planning')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('disables Start Planning button when spec is empty', async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByText('New Session'));

    const startBtn = screen.getByText('Start Planning');
    expect(startBtn).toBeDisabled();
  });

  it('enables Start Planning button when spec has text', async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByText('New Session'));

    const textarea = screen.getByPlaceholderText(/describe the feature/i);
    await user.type(textarea, 'Build a new authentication system');

    const startBtn = screen.getByText('Start Planning');
    expect(startBtn).not.toBeDisabled();
  });

  it('closes form when Cancel is clicked', async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByText('New Session'));
    expect(screen.getByText(/what do you want to build/i)).toBeInTheDocument();

    await user.click(screen.getByText('Cancel'));
    expect(screen.queryByText(/what do you want to build/i)).not.toBeInTheDocument();
  });

  it('closes form when X button is clicked', async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByText('New Session'));
    expect(screen.getByText('New Planning Session')).toBeInTheDocument();

    await user.click(screen.getByText('\u2715'));
    expect(screen.queryByText('New Planning Session')).not.toBeInTheDocument();
  });

  it('spawns a planning session on submit', async () => {
    const user = userEvent.setup();
    // After spawn, the session list is refreshed
    mockVolundrGet
      .mockResolvedValueOnce([]) // initial load
      .mockResolvedValueOnce([
        {
          id: 'plan-sess-001',
          name: 'plan-auth',
          model: 'claude-sonnet-4-6',
          status: 'running',
          chat_endpoint: 'wss://sessions.test/s/plan-sess-001/session',
          task_type: 'planner',
        },
      ]);

    renderView();
    await waitFor(() => expect(screen.getByText('New Session')).toBeInTheDocument());

    await user.click(screen.getByText('New Session'));

    const textarea = screen.getByPlaceholderText(/describe the feature/i);
    await user.type(textarea, 'Build auth system');

    const repoSelect = screen.getByTestId('repo-selector');
    await user.selectOptions(repoSelect, 'https://github.com/niuu/volundr');

    await user.click(screen.getByText('Start Planning'));

    await waitFor(() => {
      expect(mockSpawnPlanSession).toHaveBeenCalledWith(
        'Build auth system',
        'https://github.com/niuu/volundr'
      );
    });
  });

  it('shows error when spawning fails', async () => {
    const user = userEvent.setup();
    mockSpawnPlanSession.mockRejectedValueOnce(new Error('Spawn failed'));

    renderView();
    await waitFor(() => expect(screen.getByText('New Session')).toBeInTheDocument());

    await user.click(screen.getByText('New Session'));

    const textarea = screen.getByPlaceholderText(/describe the feature/i);
    await user.type(textarea, 'Build something');

    await user.click(screen.getByText('Start Planning'));

    await waitFor(() => {
      expect(screen.getByText('Spawn failed')).toBeInTheDocument();
    });
  });

  it('shows generic error message when spawn throws non-Error', async () => {
    const user = userEvent.setup();
    mockSpawnPlanSession.mockRejectedValueOnce('string error');

    renderView();
    await waitFor(() => expect(screen.getByText('New Session')).toBeInTheDocument());

    await user.click(screen.getByText('New Session'));

    const textarea = screen.getByPlaceholderText(/describe the feature/i);
    await user.type(textarea, 'Build something');

    await user.click(screen.getByText('Start Planning'));

    await waitFor(() => {
      expect(screen.getByText('Failed to start planning session')).toBeInTheDocument();
    });
  });

  it('shows "Starting..." text while spawning', async () => {
    const user = userEvent.setup();
    let resolveSpawn: (v: unknown) => void;
    mockSpawnPlanSession.mockReturnValueOnce(
      new Promise(r => {
        resolveSpawn = r;
      })
    );

    renderView();
    await waitFor(() => expect(screen.getByText('New Session')).toBeInTheDocument());

    await user.click(screen.getByText('New Session'));

    const textarea = screen.getByPlaceholderText(/describe the feature/i);
    await user.type(textarea, 'Build something');

    await user.click(screen.getByText('Start Planning'));

    expect(screen.getByText('Starting...')).toBeInTheDocument();

    // Clean up
    resolveSpawn!({ session_id: 'x', chat_endpoint: null });
  });

  /* ---- Active session chat area ---- */

  it('shows chat area when session is active', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toBeInTheDocument();
    });
  });

  it('shows session name and status in chat header', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('plan-yggdrasil')).toBeInTheDocument();
      // Status shown in chat header
      const statuses = screen.getAllByText('running');
      expect(statuses.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('shows Finalize Plan button', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('Finalize Plan')).toBeInTheDocument();
    });
  });

  it('shows Stop button in chat header for running session', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('Stop')).toBeInTheDocument();
    });
  });

  it('does not show Stop button in chat header for stopped session', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    // Wait for sessions to load
    await waitFor(() => {
      expect(screen.getAllByText('plan-tyr-pipeline').length).toBeGreaterThanOrEqual(1);
    });

    // Select the stopped session
    await user.click(screen.getAllByText('plan-tyr-pipeline')[0]);

    // The stopped session is now active - name appears in both sidebar and chat header
    await waitFor(() => {
      expect(screen.getAllByText('plan-tyr-pipeline')).toHaveLength(2);
    });

    // The chat-header level "Stop" text button should not exist for a stopped session
    expect(screen.queryByText('Stop')).not.toBeInTheDocument();
  });

  it('shows "Select a planning session" when sessions exist but none selected', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    // Render with a session param that doesn't match any session
    renderView('/tyr/new?session=nonexistent');

    await waitFor(() => {
      expect(screen.getByText('Select a planning session')).toBeInTheDocument();
    });
  });

  /* ---- handleFinalize ---- */

  it('sends finalize prompt when Finalize Plan is clicked', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('Finalize Plan')).toBeInTheDocument();
    });

    // Wait for finalize prompt to load
    await waitFor(() => {
      expect(mockTyrGet).toHaveBeenCalledWith('/plan/config');
    });

    await user.click(screen.getByText('Finalize Plan'));

    expect(mockSendMessage).toHaveBeenCalledWith('Please finalize the plan as JSON.');
  });

  it('disables Finalize Plan button when finalize prompt is not loaded', async () => {
    mockTyrGet.mockRejectedValue(new Error('Not found'));
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      const btn = screen.getByText('Finalize Plan');
      expect(btn.closest('button')).toBeDisabled();
    });
  });

  /* ---- Stop from chat header ---- */

  it('stops session from chat header Stop button', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    renderView();

    await waitFor(() => {
      expect(screen.getByText('Stop')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Stop'));

    expect(mockVolundrDelete).toHaveBeenCalledWith('/sessions/sess-1');
  });

  /* ---- Auto-detect structure ---- */

  it('opens review modal when structure is detected from assistant message', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [
      {
        id: 'msg-1',
        role: 'assistant',
        content: 'Here is the plan...',
        status: 'complete',
      },
    ];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Review Saga Structure')).toBeInTheDocument();
    });
  });

  it('does not open review modal when structure is not found', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({ found: false, structure: null });

    mockSkuldMessages = [
      {
        id: 'msg-2',
        role: 'assistant',
        content: 'Still thinking...',
        status: 'complete',
      },
    ];

    renderView();

    await waitFor(() => {
      expect(mockExtractStructure).toHaveBeenCalled();
    });

    expect(screen.queryByText('Review Saga Structure')).not.toBeInTheDocument();
  });

  it('does not check incomplete assistant messages', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);

    mockSkuldMessages = [
      {
        id: 'msg-3',
        role: 'assistant',
        content: 'Streaming...',
        status: 'streaming',
      },
    ];

    renderView();

    // Wait for sessions to load, then ensure extract was NOT called
    await waitFor(() => {
      expect(screen.getByText('plan-yggdrasil')).toBeInTheDocument();
    });

    expect(mockExtractStructure).not.toHaveBeenCalled();
  });

  it('does not check user messages for structure', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);

    mockSkuldMessages = [
      {
        id: 'msg-4',
        role: 'user',
        content: 'Build this thing',
        status: 'complete',
      },
    ];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('plan-yggdrasil')).toBeInTheDocument();
    });

    expect(mockExtractStructure).not.toHaveBeenCalled();
  });

  /* ---- Review modal ---- */

  it('renders review modal with saga name input', async () => {
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [
      { id: 'msg-r1', role: 'assistant', content: 'Plan ready', status: 'complete' },
    ];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Review Saga Structure')).toBeInTheDocument();
    });

    // Saga name input
    const nameInput = screen.getByDisplayValue('Auth Overhaul');
    expect(nameInput).toBeInTheDocument();

    // Repo input exists (may be empty if session source wasn't resolved yet)
    const repoInput = screen.getByPlaceholderText('owner/repo');
    expect(repoInput).toBeInTheDocument();

    // Phases
    expect(screen.getByText(/Phase 1.*Foundation/)).toBeInTheDocument();
    expect(screen.getByText(/Phase 2.*Integration/)).toBeInTheDocument();

    // Raids
    expect(screen.getByDisplayValue('Setup OIDC adapter')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Create user model')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Wire middleware')).toBeInTheDocument();

    // Acceptance criteria
    expect(screen.getByDisplayValue('OIDC flow works')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Tokens validated')).toBeInTheDocument();
  });

  it('allows editing saga name in review modal', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-e1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByDisplayValue('Auth Overhaul')).toBeInTheDocument();
    });

    const nameInput = screen.getByDisplayValue('Auth Overhaul');
    await user.clear(nameInput);
    await user.type(nameInput, 'New Auth System');

    expect(screen.getByDisplayValue('New Auth System')).toBeInTheDocument();
  });

  it('allows editing raid name in review modal', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-e2', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByDisplayValue('Setup OIDC adapter')).toBeInTheDocument();
    });

    const raidInput = screen.getByDisplayValue('Setup OIDC adapter');
    await user.clear(raidInput);
    await user.type(raidInput, 'Configure OIDC');

    expect(screen.getByDisplayValue('Configure OIDC')).toBeInTheDocument();
  });

  it('allows editing raid description in review modal', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-e3', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByDisplayValue('Add OIDC adapter for identity')).toBeInTheDocument();
    });

    const descInput = screen.getByDisplayValue('Add OIDC adapter for identity');
    await user.clear(descInput);
    await user.type(descInput, 'Updated description');

    expect(screen.getByDisplayValue('Updated description')).toBeInTheDocument();
  });

  it('allows editing acceptance criteria in review modal', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-e4', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByDisplayValue('OIDC flow works')).toBeInTheDocument();
    });

    const criterionInput = screen.getByDisplayValue('OIDC flow works');
    await user.clear(criterionInput);
    await user.type(criterionInput, 'SSO login works');

    expect(screen.getByDisplayValue('SSO login works')).toBeInTheDocument();
  });

  it('allows editing commit repo in review modal', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-e5', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByPlaceholderText('owner/repo')).toBeInTheDocument();
    });

    const repoInput = screen.getByPlaceholderText('owner/repo');
    await user.clear(repoInput);
    await user.type(repoInput, 'niuu/other-repo');

    expect(screen.getByDisplayValue('niuu/other-repo')).toBeInTheDocument();
  });

  it('closes review modal when Keep Editing is clicked', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-c1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Review Saga Structure')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Keep Editing'));

    expect(screen.queryByText('Review Saga Structure')).not.toBeInTheDocument();
  });

  it('closes review modal when X is clicked', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-c2', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Review Saga Structure')).toBeInTheDocument();
    });

    // The close button in the review modal (there are two overlays potentially, pick the one in review)
    const reviewPanel = screen.getByText('Review Saga Structure').closest('div')!;
    const closeBtn = within(reviewPanel).getByText('\u2715');
    await user.click(closeBtn);

    expect(screen.queryByText('Review Saga Structure')).not.toBeInTheDocument();
  });

  it('toggles include transcript checkbox', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-t1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Attach planning transcript')).toBeInTheDocument();
    });

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeChecked();

    await user.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  /* ---- handleCommit ---- */

  it('commits saga and navigates to saga detail', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [
      { id: 'msg-h1', role: 'user', content: 'Build auth', status: 'complete' },
      { id: 'msg-h2', role: 'assistant', content: 'Here is the plan', status: 'complete' },
    ];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    // Fill in repo since it may not be pre-populated due to async timing
    const repoInput = screen.getByPlaceholderText('owner/repo');
    await user.clear(repoInput);
    await user.type(repoInput, 'niuu/volundr');

    await user.click(screen.getByText('Create Saga'));

    await waitFor(() => {
      expect(mockCommitSaga).toHaveBeenCalledTimes(1);
    });

    const commitArg = mockCommitSaga.mock.calls[0][0];
    expect(commitArg.name).toBe('Auth Overhaul');
    expect(commitArg.slug).toBe('auth-overhaul');
    expect(commitArg.repos).toEqual(['niuu/volundr']);
    expect(commitArg.base_branch).toBe('main');
    expect(commitArg.phases).toHaveLength(2);
    expect(commitArg.phases[0].raids).toHaveLength(2);
    expect(commitArg.phases[1].raids).toHaveLength(1);
    expect(commitArg.transcript).toContain('### Human');
    expect(commitArg.transcript).toContain('### AI');
    expect(commitArg.transcript).toContain('Build auth');
    expect(commitArg.transcript).toContain('Here is the plan');

    // Description includes phase count and raid count
    expect(commitArg.description).toContain('2 phases');
    expect(commitArg.description).toContain('3 raids');

    // Navigates to saga detail
    await waitFor(() => {
      expect(screen.getByTestId('saga-detail')).toBeInTheDocument();
    });
  });

  it('commits saga without transcript when checkbox unchecked', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [
      { id: 'msg-nt1', role: 'assistant', content: 'Plan here', status: 'complete' },
    ];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Attach planning transcript')).toBeInTheDocument();
    });

    // Uncheck the transcript checkbox
    const checkbox = screen.getByRole('checkbox');
    await user.click(checkbox);

    await user.click(screen.getByText('Create Saga'));

    await waitFor(() => {
      expect(mockCommitSaga).toHaveBeenCalledTimes(1);
    });

    const commitArg = mockCommitSaga.mock.calls[0][0];
    expect(commitArg.transcript).toBeUndefined();
  });

  it('shows error when commit fails with Error', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });
    mockCommitSaga.mockRejectedValueOnce(new Error('Commit error'));

    mockSkuldMessages = [{ id: 'msg-cf1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Create Saga'));

    await waitFor(() => {
      expect(screen.getByText('Commit error')).toBeInTheDocument();
    });
  });

  it('shows generic error when commit fails with non-Error', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });
    mockCommitSaga.mockRejectedValueOnce('unknown');

    mockSkuldMessages = [{ id: 'msg-cf2', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Create Saga'));

    await waitFor(() => {
      expect(screen.getByText('Failed to commit saga')).toBeInTheDocument();
    });
  });

  it('shows "Creating..." text while committing', async () => {
    const user = userEvent.setup();
    let resolveCommit: (v: unknown) => void;
    mockCommitSaga.mockReturnValueOnce(
      new Promise(r => {
        resolveCommit = r;
      })
    );

    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-cr1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Create Saga'));

    expect(screen.getByText('Creating...')).toBeInTheDocument();

    // Clean up
    resolveCommit!({ id: 'saga-001' });
  });

  it('disables Create Saga button while committing', async () => {
    const user = userEvent.setup();
    let resolveCommit: (v: unknown) => void;
    mockCommitSaga.mockReturnValueOnce(
      new Promise(r => {
        resolveCommit = r;
      })
    );

    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: sampleStructure,
    });

    mockSkuldMessages = [{ id: 'msg-cr2', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Create Saga'));

    const btn = screen.getByText('Creating...').closest('button');
    expect(btn).toBeDisabled();

    resolveCommit!({ id: 'saga-001' });
  });

  /* ---- Description truncation ---- */

  it('truncates long descriptions to 255 characters', async () => {
    const user = userEvent.setup();
    const longPhases = Array.from({ length: 20 }, (_, i) => ({
      name: `Phase ${i + 1} - Very Long Phase Name That Takes Up Space`,
      raids: [
        {
          name: `Raid ${i}`,
          description: 'desc',
          acceptance_criteria: ['done'],
          declared_files: [],
          estimate_hours: 1,
          confidence: 0.9,
        },
      ],
    }));
    const longStructure = { name: 'Big Saga', phases: longPhases };

    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: longStructure,
    });

    mockSkuldMessages = [{ id: 'msg-tr1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Create Saga'));

    await waitFor(() => {
      expect(mockCommitSaga).toHaveBeenCalledTimes(1);
    });

    const commitArg = mockCommitSaga.mock.calls[0][0];
    expect(commitArg.description.length).toBeLessThanOrEqual(255);
    expect(commitArg.description).toMatch(/\.\.\.$/);
  });

  /* ---- Slug generation ---- */

  it('generates correct slug from saga name', async () => {
    const user = userEvent.setup();
    mockVolundrGet.mockResolvedValue(mockSessions);
    mockExtractStructure.mockResolvedValue({
      found: true,
      structure: {
        ...sampleStructure,
        name: 'My  Cool--Feature!!!  v2',
      },
    });

    mockSkuldMessages = [{ id: 'msg-sl1', role: 'assistant', content: 'Plan', status: 'complete' }];

    renderView();

    await waitFor(() => {
      expect(screen.getByText('Create Saga')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Create Saga'));

    await waitFor(() => {
      expect(mockCommitSaga).toHaveBeenCalledTimes(1);
    });

    const commitArg = mockCommitSaga.mock.calls[0][0];
    expect(commitArg.slug).toBe('my-cool-feature-v2');
  });

  /* ---- Loading state ---- */

  it('shows loading text while sessions are being fetched', () => {
    // Never resolve the fetch
    mockVolundrGet.mockReturnValue(new Promise(() => {}));
    renderView();

    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });
});

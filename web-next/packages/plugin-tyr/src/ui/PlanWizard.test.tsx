import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PlanWizard } from './PlanWizard';
import { PlanPrompt } from './PlanPrompt';
import { PlanQuestions } from './PlanQuestions';
import { PlanRaiding } from './PlanRaiding';
import { PlanDraft } from './PlanDraft';
import { PlanApproved } from './PlanApproved';
import type {
  ITyrService,
  IWorkflowService,
  ExtractedStructure,
  PlanSession,
  PlanRisk,
} from '../ports';
import type { Saga } from '../domain/saga';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_SESSION: PlanSession = {
  sessionId: 'plan-test-1',
  chatEndpoint: null,
  questions: [
    { id: 'q1', question: 'Which repos?', hint: 'e.g. niuulabs/volundr' },
    { id: 'q2', question: 'Base branch?' },
  ],
};

const MOCK_RISKS: PlanRisk[] = [
  { kind: 'blast', message: 'Touches dispatch path — ship behind flag.' },
  { kind: 'untested', message: 'No existing tests for subscription graph.' },
];

const MOCK_STRUCTURE: ExtractedStructure = {
  found: true,
  structure: {
    name: 'Auth Rewrite',
    phases: [
      {
        name: 'Phase 1: Foundation',
        raids: [
          {
            name: 'Scaffold OIDC',
            description: 'Add OIDC login',
            acceptanceCriteria: ['SSO works'],
            declaredFiles: ['src/auth.ts'],
            estimateHours: 8,
            confidence: 85,
            size: 'M',
            persona: 'coding-agent',
            phase: 'Build',
          },
        ],
      },
      {
        name: 'Phase 2: Hardening',
        raids: [
          {
            name: 'Add PAT support',
            description: 'Headless PATs',
            acceptanceCriteria: ['PATs revocable'],
            declaredFiles: ['src/pat.ts'],
            estimateHours: 4,
            confidence: 70,
          },
        ],
      },
    ],
    risks: MOCK_RISKS,
  },
};

const MOCK_SAGA: Saga = {
  id: 'saga-test-1',
  trackerId: 'NIU-999',
  trackerType: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/auth-rewrite',
  status: 'active',
  confidence: 77,
  createdAt: '2026-01-01T00:00:00Z',
  phaseSummary: { total: 2, completed: 0 },
};

const MOCK_WORKFLOW: Workflow = {
  id: 'wf-1',
  name: 'Ship',
  nodes: [
    {
      id: 'n1',
      kind: 'stage',
      label: 'Build',
      raidId: null,
      personaIds: [],
      position: { x: 0, y: 0 },
    },
    {
      id: 'n2',
      kind: 'stage',
      label: 'Test',
      raidId: null,
      personaIds: [],
      position: { x: 100, y: 0 },
    },
    { id: 'n3', kind: 'gate', label: 'Review', condition: 'Approved', position: { x: 200, y: 0 } },
  ],
  edges: [],
};

function makeSvc(overrides: Partial<ITyrService> = {}): Partial<ITyrService> {
  return {
    getSagas: vi.fn().mockResolvedValue([]),
    getSaga: vi.fn().mockResolvedValue(null),
    getPhases: vi.fn().mockResolvedValue([]),
    createSaga: vi.fn(),
    commitSaga: vi.fn().mockResolvedValue(MOCK_SAGA),
    decompose: vi.fn().mockResolvedValue([]),
    spawnPlanSession: vi.fn().mockResolvedValue(MOCK_SESSION),
    extractStructure: vi.fn().mockResolvedValue(MOCK_STRUCTURE),
    ...overrides,
  };
}

function makeWorkflowSvc(overrides: Partial<IWorkflowService> = {}): Partial<IWorkflowService> {
  return {
    listWorkflows: vi.fn().mockResolvedValue([MOCK_WORKFLOW]),
    getWorkflow: vi.fn().mockResolvedValue(null),
    saveWorkflow: vi.fn(),
    deleteWorkflow: vi.fn(),
    ...overrides,
  };
}

function wrap(svc: Partial<ITyrService>, workflowSvc?: Partial<IWorkflowService>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wfSvc = workflowSvc ?? makeWorkflowSvc();
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ tyr: svc, 'tyr.workflows': wfSvc }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

// ---------------------------------------------------------------------------
// PlanPrompt unit tests
// ---------------------------------------------------------------------------

describe('PlanPrompt', () => {
  it('renders the heading', () => {
    render(<PlanPrompt onSubmit={vi.fn()} loading={false} error={null} />);
    expect(screen.getByText('Describe your goal')).toBeInTheDocument();
  });

  it('calls onSubmit with trimmed values', async () => {
    const onSubmit = vi.fn();
    render(<PlanPrompt onSubmit={onSubmit} loading={false} error={null} />);

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      '  Build auth  ',
    );
    await userEvent.type(
      screen.getByRole('textbox', { name: /target repository/i }),
      'niuulabs/volundr',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));

    expect(onSubmit).toHaveBeenCalledWith('Build auth', 'niuulabs/volundr');
  });

  it('does not submit when prompt is empty', () => {
    const onSubmit = vi.fn();
    render(<PlanPrompt onSubmit={onSubmit} loading={false} error={null} />);
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('disables submit button while loading', () => {
    render(<PlanPrompt onSubmit={vi.fn()} loading={true} error={null} />);
    expect(screen.getByRole('button', { name: /starting/i })).toBeDisabled();
  });

  it('renders error message', () => {
    render(<PlanPrompt onSubmit={vi.fn()} loading={false} error="Service unavailable" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Service unavailable');
  });

  it('renders hint chips', () => {
    render(<PlanPrompt onSubmit={vi.fn()} loading={false} error={null} />);
    expect(
      screen.getByRole('button', { name: /example: subscription validation/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /example: simple endpoint/i })).toBeInTheDocument();
  });

  it('clicking a hint chip fills the textarea', async () => {
    render(<PlanPrompt onSubmit={vi.fn()} loading={false} error={null} />);
    const chip = screen.getByRole('button', { name: /example: simple endpoint/i });
    fireEvent.click(chip);
    const textarea = screen.getByRole('textbox', { name: /goal description/i });
    expect((textarea as HTMLTextAreaElement).value).toMatch(/health check endpoint/i);
  });
});

// ---------------------------------------------------------------------------
// PlanQuestions unit tests
// ---------------------------------------------------------------------------

describe('PlanQuestions', () => {
  const questions = [
    { id: 'q1', question: 'Which repos?', hint: 'e.g. niuulabs/volundr' },
    { id: 'q2', question: 'Base branch?' },
  ];

  it('renders all questions', () => {
    render(<PlanQuestions questions={questions} onSubmit={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByText('Which repos?')).toBeInTheDocument();
    expect(screen.getByText('Base branch?')).toBeInTheDocument();
  });

  it('renders hints', () => {
    render(<PlanQuestions questions={questions} onSubmit={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByText('e.g. niuulabs/volundr')).toBeInTheDocument();
  });

  it('calls onSubmit with answers', async () => {
    const onSubmit = vi.fn();
    render(<PlanQuestions questions={questions} onSubmit={onSubmit} onBack={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/1\./), 'niuulabs/volundr');
    fireEvent.submit(screen.getByRole('form'));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ q1: 'niuulabs/volundr' }));
  });

  it('calls onBack when back button clicked', () => {
    const onBack = vi.fn();
    render(<PlanQuestions questions={questions} onSubmit={vi.fn()} onBack={onBack} />);
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    expect(onBack).toHaveBeenCalled();
  });

  it('shows empty-state message when no questions', () => {
    render(<PlanQuestions questions={[]} onSubmit={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByText(/no clarifying questions/i)).toBeInTheDocument();
  });

  it('shows YOUR BRIEF quote card when prompt is provided', () => {
    render(
      <PlanQuestions
        questions={questions}
        prompt="Build the auth module"
        onSubmit={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/your brief/i)).toBeInTheDocument();
    expect(screen.getByText('Build the auth module')).toBeInTheDocument();
  });

  it('does not render YOUR BRIEF card when no prompt', () => {
    render(<PlanQuestions questions={questions} onSubmit={vi.fn()} onBack={vi.fn()} />);
    expect(screen.queryByLabelText(/your brief/i)).not.toBeInTheDocument();
  });

  it('renders workflow picker for workflow-kind questions', () => {
    const workflowQuestions = [
      { id: 'wf', question: 'Apply which workflow?', kind: 'workflow' as const },
    ];
    render(
      <PlanQuestions
        questions={workflowQuestions}
        workflows={[MOCK_WORKFLOW]}
        onSubmit={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByRole('group', { name: /workflow template picker/i })).toBeInTheDocument();
    expect(screen.getByText('Ship')).toBeInTheDocument();
    expect(screen.getByText('2 stages')).toBeInTheDocument();
  });

  it('selecting a workflow template sets the answer', () => {
    const workflowQuestions = [
      { id: 'wf', question: 'Apply which workflow?', kind: 'workflow' as const },
    ];
    render(
      <PlanQuestions
        questions={workflowQuestions}
        workflows={[MOCK_WORKFLOW]}
        onSubmit={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    const wfButton = screen.getByRole('button', { name: /ship/i });
    fireEvent.click(wfButton);
    expect(wfButton).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows empty workflow message when no templates', () => {
    const workflowQuestions = [
      { id: 'wf', question: 'Apply which workflow?', kind: 'workflow' as const },
    ];
    render(
      <PlanQuestions
        questions={workflowQuestions}
        workflows={[]}
        onSubmit={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByText(/no workflow templates/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PlanRaiding unit tests
// ---------------------------------------------------------------------------

describe('PlanRaiding', () => {
  it('shows processing indicator', () => {
    render(<PlanRaiding error={null} onBack={vi.fn()} />);
    // Use aria-label to disambiguate from StateDot's inner role="status"
    expect(screen.getByLabelText(/decomposing plan/i)).toBeInTheDocument();
    expect(screen.getByText(/Ravens are raiding/i)).toBeInTheDocument();
  });

  it('shows raven activity lines', () => {
    render(<PlanRaiding error={null} onBack={vi.fn()} />);
    expect(screen.getByText(/decomposer — analyzing brief/i)).toBeInTheDocument();
    expect(screen.getByText(/investigator — probing repo/i)).toBeInTheDocument();
    expect(screen.getByText(/mimir-indexer — pulling in prior-art/i)).toBeInTheDocument();
  });

  it('shows error state when error is present', () => {
    render(<PlanRaiding error="Decompose failed" onBack={vi.fn()} />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Decompose failed')).toBeInTheDocument();
  });

  it('calls onBack on error try-again click', () => {
    const onBack = vi.fn();
    render(<PlanRaiding error="oops" onBack={onBack} />);
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(onBack).toHaveBeenCalled();
  });

  it('has aria-label for the raiding container', () => {
    render(<PlanRaiding error={null} onBack={vi.fn()} />);
    expect(screen.getByLabelText(/decomposing plan/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PlanDraft unit tests
// ---------------------------------------------------------------------------

describe('PlanDraft', () => {
  it('renders saga name', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByText('Auth Rewrite')).toBeInTheDocument();
  });

  it('renders all phase names', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByText('Phase 1: Foundation')).toBeInTheDocument();
    expect(screen.getByText('Phase 2: Hardening')).toBeInTheDocument();
  });

  it('renders raid names', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByText('Scaffold OIDC')).toBeInTheDocument();
  });

  it('renders risk kind badges', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByText('blast')).toBeInTheDocument();
    expect(screen.getByText('untested')).toBeInTheDocument();
    expect(screen.getByText(/touches dispatch path/i)).toBeInTheDocument();
  });

  it('does not render risks section when no risks', () => {
    const noRisksStructure: ExtractedStructure = {
      found: true,
      structure: { name: 'Test', phases: [], risks: [] },
    };
    render(
      <PlanDraft
        structure={noRisksStructure}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.queryByText(/risks flagged/i)).not.toBeInTheDocument();
  });

  it('calls onApprove when approve button clicked', async () => {
    const onApprove = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={onApprove}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    expect(onApprove).toHaveBeenCalled();
  });

  it('calls onBack when back button clicked', () => {
    const onBack = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={onBack}
        onEditPhase={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /← back/i }));
    expect(onBack).toHaveBeenCalled();
  });

  it('calls onReplan when re-plan button clicked', () => {
    const onReplan = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onReplan={onReplan}
        onEditPhase={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /re-plan/i }));
    expect(onReplan).toHaveBeenCalled();
  });

  it('calls onSaveDraft when save as draft button clicked', () => {
    const onSaveDraft = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onSaveDraft={onSaveDraft}
        onEditPhase={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /save as draft/i }));
    expect(onSaveDraft).toHaveBeenCalled();
  });

  it('does not render Re-plan button when onReplan not provided', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /re-plan/i })).not.toBeInTheDocument();
  });

  it('allows editing a phase name', async () => {
    const onEditPhase = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={onEditPhase}
      />,
    );

    // Click edit on the first phase
    const editButtons = screen.getAllByRole('button', { name: /edit phase/i });
    fireEvent.click(editButtons[0]!);

    const input = screen.getByLabelText(/edit phase 1 name/i);
    await userEvent.clear(input);
    await userEvent.type(input, 'Renamed Phase');
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onEditPhase).toHaveBeenCalledWith(0, 'Renamed Phase');
  });

  it('shows empty state when no phases', () => {
    const emptyStructure: ExtractedStructure = {
      found: false,
      structure: { name: 'Saga', phases: [] },
    };
    render(
      <PlanDraft
        structure={emptyStructure}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByText(/no phases extracted/i)).toBeInTheDocument();
  });

  it('disables approve when loading', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={true}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /launching/i })).toBeDisabled();
  });

  it('shows error message', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error="Commit failed"
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Commit failed');
  });

  it('cancels editing without calling onEditPhase', () => {
    const onEditPhase = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={onEditPhase}
      />,
    );
    const editButtons = screen.getAllByRole('button', { name: /edit phase/i });
    fireEvent.click(editButtons[0]!);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onEditPhase).not.toHaveBeenCalled();
  });

  it('renders size pill for raids with size', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.getByText('M')).toBeInTheDocument();
  });

  it('renders "Own saga" button for each raid (disabled stub)', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    const ownSagaButtons = screen.getAllByRole('button', { name: /promote raid .* to own saga/i });
    expect(ownSagaButtons.length).toBeGreaterThan(0);
    ownSagaButtons.forEach((btn) => expect(btn).toBeDisabled());
  });

  it('calls onRemoveRaid when × button clicked', () => {
    const onRemoveRaid = vi.fn();
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
        onRemoveRaid={onRemoveRaid}
      />,
    );
    const removeButtons = screen.getAllByRole('button', { name: /remove raid/i });
    expect(removeButtons.length).toBeGreaterThan(0);
    fireEvent.click(removeButtons[0]!);
    expect(onRemoveRaid).toHaveBeenCalledWith(0, 0);
  });

  it('does not render remove buttons when onRemoveRaid not provided', () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    expect(screen.queryAllByRole('button', { name: /remove raid/i })).toHaveLength(0);
  });

  it('shows "Draft saved (local only)" feedback after clicking save as draft', async () => {
    render(
      <PlanDraft
        structure={MOCK_STRUCTURE}
        loading={false}
        error={null}
        onApprove={vi.fn()}
        onBack={vi.fn()}
        onSaveDraft={vi.fn()}
        onEditPhase={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /save as draft/i }));
    await waitFor(() =>
      expect(screen.getByText(/draft saved \(local only\)/i)).toBeInTheDocument(),
    );
  });
});

// ---------------------------------------------------------------------------
// PlanApproved unit tests
// ---------------------------------------------------------------------------

describe('PlanApproved', () => {
  it('renders the saga name', () => {
    render(<PlanApproved saga={MOCK_SAGA} />);
    // saga name appears in both the description span and the summary dl
    expect(screen.getAllByText('Auth Rewrite').length).toBeGreaterThanOrEqual(1);
  });

  it('shows the "Open in Sagas" link', () => {
    render(<PlanApproved saga={MOCK_SAGA} />);
    expect(screen.getByRole('link', { name: /open in sagas/i })).toBeInTheDocument();
  });

  it('shows feature branch', () => {
    render(<PlanApproved saga={MOCK_SAGA} />);
    expect(screen.getByText('feat/auth-rewrite')).toBeInTheDocument();
  });

  it('calls onNewPlan when new plan button clicked', () => {
    const onNewPlan = vi.fn();
    render(<PlanApproved saga={MOCK_SAGA} onNewPlan={onNewPlan} />);
    fireEvent.click(screen.getByRole('button', { name: /new plan/i }));
    expect(onNewPlan).toHaveBeenCalled();
  });

  it('does not render new plan button when onNewPlan not provided', () => {
    render(<PlanApproved saga={MOCK_SAGA} />);
    expect(screen.queryByRole('button', { name: /new plan/i })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PlanWizard integration tests
// ---------------------------------------------------------------------------

describe('PlanWizard integration', () => {
  it('renders the prompt step initially', () => {
    render(<PlanWizard />, { wrapper: wrap(makeSvc()) });
    expect(screen.getByText('Describe your goal')).toBeInTheDocument();
  });

  it('shows hint chips on the prompt step', () => {
    render(<PlanWizard />, { wrapper: wrap(makeSvc()) });
    expect(
      screen.getByRole('button', { name: /example: subscription validation/i }),
    ).toBeInTheDocument();
  });

  it('shows the step dots navigation', () => {
    render(<PlanWizard />, { wrapper: wrap(makeSvc()) });
    expect(screen.getByRole('navigation', { name: /plan wizard steps/i })).toBeInTheDocument();
  });

  it('shows the Tyr rune in the header', () => {
    render(<PlanWizard />, { wrapper: wrap(makeSvc()) });
    expect(screen.getByText('ᚦ')).toBeInTheDocument();
  });

  it('advances to questions step after submitting prompt', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));

    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    expect(screen.getByText('Which repos?')).toBeInTheDocument();
  });

  it('shows YOUR BRIEF card in questions step', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));

    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    expect(screen.getByLabelText(/your brief/i)).toBeInTheDocument();
    expect(screen.getByText('Build auth module')).toBeInTheDocument();
  });

  it('advances to raiding step after submitting answers', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    // Step 1: prompt
    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());

    // Step 2: questions
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));

    await waitFor(() => expect(screen.getByLabelText(/decomposing plan/i)).toBeInTheDocument());
  });

  it('raiding step shows raven activity lines', async () => {
    const svc = makeSvc({
      decompose: vi.fn(() => new Promise(() => {})), // never resolves
      extractStructure: vi.fn().mockResolvedValue(MOCK_STRUCTURE),
    });
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));

    await waitFor(() => expect(screen.getByLabelText(/decomposing plan/i)).toBeInTheDocument());
    expect(screen.getByText(/decomposer — analyzing brief/i)).toBeInTheDocument();
  });

  it('auto-advances to draft after decomposition', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    // Through to raiding
    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));

    // Should auto-advance to draft
    await waitFor(() => expect(screen.getByText('Review your plan')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText('Auth Rewrite')).toBeInTheDocument();
  });

  it('draft shows risk kind badges', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));
    await waitFor(() => expect(screen.getByText('Review your plan')).toBeInTheDocument(), {
      timeout: 3000,
    });

    expect(screen.getByText('blast')).toBeInTheDocument();
    expect(screen.getByText('untested')).toBeInTheDocument();
  });

  it('draft shows Re-plan and Save as draft buttons', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));
    await waitFor(() => expect(screen.getByText('Review your plan')).toBeInTheDocument(), {
      timeout: 3000,
    });

    expect(screen.getByRole('button', { name: /re-plan/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save as draft/i })).toBeInTheDocument();
  });

  it('re-plan button re-runs decomposition', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(
      screen.getByRole('textbox', { name: /goal description/i }),
      'Build auth module',
    );
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));
    await waitFor(() => expect(screen.getByText('Review your plan')).toBeInTheDocument(), {
      timeout: 3000,
    });

    fireEvent.click(screen.getByRole('button', { name: /re-plan/i }));

    // Should re-enter raiding step then come back to draft
    await waitFor(() => expect(screen.getByText('Review your plan')).toBeInTheDocument(), {
      timeout: 3000,
    });
    // decompose was called twice (once initially, once after re-plan)
    expect(svc.decompose).toHaveBeenCalledTimes(2);
  });

  it('reaches approved step after full flow', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    // Prompt
    await userEvent.type(screen.getByRole('textbox', { name: /goal description/i }), 'Build auth');
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());

    // Questions
    fireEvent.submit(screen.getByRole('form', { name: /clarifying questions form/i }));
    await waitFor(() => expect(screen.getByText('Review your plan')).toBeInTheDocument(), {
      timeout: 3000,
    });

    // Draft → approve
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    });

    await waitFor(() => expect(screen.getByTestId('plan-approved')).toBeInTheDocument());
    expect(screen.getByText('Saga launched!')).toBeInTheDocument();
  });

  it('can navigate back from questions to prompt', async () => {
    const svc = makeSvc();
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(screen.getByRole('textbox', { name: /goal description/i }), 'Build auth');
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));
    await waitFor(() => expect(screen.getByText('Clarify your plan')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    expect(screen.getByText('Describe your goal')).toBeInTheDocument();
  });

  it('shows error when spawnPlanSession fails', async () => {
    const svc = makeSvc({
      spawnPlanSession: vi.fn().mockRejectedValue(new Error('Tyr unavailable')),
    });
    render(<PlanWizard />, { wrapper: wrap(svc) });

    await userEvent.type(screen.getByRole('textbox', { name: /goal description/i }), 'Build auth');
    fireEvent.submit(screen.getByRole('form', { name: /plan prompt form/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('Tyr unavailable')).toBeInTheDocument();
  });
});

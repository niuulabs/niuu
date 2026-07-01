import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { usePlanWizard } from './usePlanWizard';
import type { ITingService } from '../ports';
import type { PlanSession, ExtractedStructure } from '../ports';
import type { Saga, Phase } from '../domain/saga';

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const MOCK_SESSION: PlanSession = {
  sessionId: 'sess-plan-1',
  chatEndpoint: null,
  questions: [
    { id: 'q1', question: 'Which repos?', hint: 'e.g. niuulabs/volundr' },
    { id: 'q2', question: 'Base branch?' },
  ],
};

const MOCK_PHASES: Phase[] = [
  {
    id: 'ph-1',
    sagaId: '',
    trackerId: '',
    number: 1,
    name: 'Phase 1',
    status: 'pending',
    confidence: 80,
    runs: [],
  },
];

const MOCK_STRUCTURE: ExtractedStructure = {
  found: true,
  structure: {
    name: 'Test Saga',
    phases: [
      {
        name: 'Phase 1',
        runs: [
          {
            name: 'Scaffold',
            description: 'Scaffold domain',
            acceptanceCriteria: ['types exported'],
            declaredFiles: ['src/domain.ts'],
            estimateHours: 4,
            confidence: 80,
          },
        ],
      },
    ],
  },
};

const MOCK_SAGA: Saga = {
  id: 'saga-new-1',
  trackerId: 'NIU-999',
  trackerType: 'linear',
  slug: 'test-saga',
  name: 'Test Saga',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/test-saga',
  status: 'active',
  confidence: 80,
  createdAt: '2026-01-01T00:00:00Z',
  phaseSummary: { total: 1, completed: 0 },
};

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function makeWrapper(svc: Partial<ITingService>) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(ServicesProvider, { services: { ting: svc } }, children);
  };
}

function makeMockService(overrides: Partial<ITingService> = {}): Partial<ITingService> {
  return {
    getSagas: vi.fn().mockResolvedValue([]),
    getSaga: vi.fn().mockResolvedValue(null),
    getPhases: vi.fn().mockResolvedValue([]),
    createSaga: vi.fn(),
    commitSaga: vi.fn().mockResolvedValue(MOCK_SAGA),
    decompose: vi.fn().mockResolvedValue(MOCK_PHASES),
    spawnPlanSession: vi.fn().mockResolvedValue(MOCK_SESSION),
    extractStructure: vi.fn().mockResolvedValue(MOCK_STRUCTURE),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('usePlanWizard — initial state', () => {
  it('starts on the prompt step', () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });
    expect(result.current.state.step).toBe('prompt');
  });

  it('has empty prompt and repo', () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });
    expect(result.current.state.prompt).toBe('');
    expect(result.current.state.repo).toBe('');
  });

  it('has no loading or error initially', () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });
    expect(result.current.state.loading).toBe(false);
    expect(result.current.state.error).toBeNull();
  });
});

describe('usePlanWizard — submitPrompt', () => {
  it('transitions to questions step on success', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth module', 'niuulabs/volundr');
    });

    expect(result.current.state.step).toBe('questions');
    expect(result.current.state.prompt).toBe('Build auth module');
    expect(result.current.state.repo).toBe('niuulabs/volundr');
  });

  it('loads the questions from the session', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth module', 'niuulabs/volundr');
    });

    expect(result.current.state.questions).toHaveLength(2);
    expect(result.current.state.questions[0]?.question).toBe('Which repos?');
  });

  it('refreshes workflow-backed plan session status', async () => {
    const getPlanSession = vi.fn().mockResolvedValue({
      ...MOCK_SESSION,
      campaignSlug: 'plan-auth',
      status: 'running',
      activeStageId: 'plan-breakdown',
      stageState: [{ stageId: 'plan-breakdown', label: 'Draft saga breakdown', status: 'active' }],
      questions: [
        {
          id: 'draft-feedback',
          question: 'What should change before this draft is approved?',
        },
      ],
    });
    const svc = makeMockService({
      spawnPlanSession: vi.fn().mockResolvedValue({
        ...MOCK_SESSION,
        campaignSlug: 'plan-auth',
        status: 'pending',
      }),
      getPlanSession,
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth module', 'niuulabs/volundr');
    });

    await waitFor(() => expect(getPlanSession).toHaveBeenCalledWith('plan-auth'));
    await waitFor(() => {
      expect(result.current.state.session?.status).toBe('running');
      expect(result.current.state.session?.activeStageId).toBe('plan-breakdown');
      expect(result.current.state.questions[0]?.id).toBe('draft-feedback');
    });
  });

  it('shows a live draft review gate question in the Plan wizard', async () => {
    vi.useFakeTimers();
    try {
      const getPlanSession = vi
        .fn()
        .mockResolvedValueOnce({
          ...MOCK_SESSION,
          campaignSlug: 'plan-auth',
          status: 'running',
          questions: [],
        })
        .mockResolvedValueOnce({
          ...MOCK_SESSION,
          campaignSlug: 'plan-auth',
          status: 'running',
          questions: [],
        })
        .mockResolvedValue({
          ...MOCK_SESSION,
          campaignSlug: 'plan-auth',
          status: 'running',
          activeStageId: 'plan-review-gate',
          questions: [
            {
              id: 'draft-feedback',
              question: 'The workflow is waiting on draft plan review.',
            },
          ],
        });
      const svc = makeMockService({
        decompose: vi.fn(() => new Promise<Phase[]>(() => {})),
        spawnPlanSession: vi.fn().mockResolvedValue({
          ...MOCK_SESSION,
          campaignSlug: 'plan-auth',
          status: 'pending',
        }),
        getPlanSession,
      });
      const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

      await act(async () => {
        await result.current.submitPrompt('Build auth module', 'niuulabs/volundr');
      });
      await act(async () => {
        await Promise.resolve();
      });

      await act(async () => {
        await result.current.submitAnswers({ q1: 'Keep this to one saga' });
      });
      expect(result.current.state.step).toBe('running');

      await act(async () => {
        vi.advanceTimersByTime(5000);
        await Promise.resolve();
      });

      expect(result.current.state.step).toBe('questions');
      expect(result.current.state.questions[0]?.id).toBe('draft-feedback');
    } finally {
      vi.useRealTimers();
    }
  });

  it('sets error on service failure', async () => {
    const svc = makeMockService({
      spawnPlanSession: vi.fn().mockRejectedValue(new Error('service down')),
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth module', 'niuulabs/volundr');
    });

    expect(result.current.state.error).toBe('service down');
    expect(result.current.state.step).toBe('prompt');
  });
});

describe('usePlanWizard — submitAnswers', () => {
  it('transitions to running step', async () => {
    const svc = makeMockService({
      decompose: vi.fn(() => new Promise<Phase[]>(() => {})),
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'niuulabs/volundr', q2: 'main' });
    });

    expect(result.current.state.step).toBe('running');
    expect(result.current.state.answers).toEqual({ q1: 'niuulabs/volundr', q2: 'main' });
  });

  it('sends clarification answers to the workflow-backed plan session', async () => {
    const sendPlanFeedback = vi.fn().mockResolvedValue(undefined);
    const svc = makeMockService({
      decompose: vi.fn(() => new Promise<Phase[]>(() => {})),
      spawnPlanSession: vi.fn().mockResolvedValue({
        ...MOCK_SESSION,
        campaignSlug: 'plan-auth',
      }),
      sendPlanFeedback,
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'Keep this to one saga', q2: '' });
    });

    expect(sendPlanFeedback).toHaveBeenCalledWith(
      'plan-auth',
      'Planning feedback:\n- Keep this to one saga',
    );
    expect(result.current.state.step).toBe('running');
  });

  it('shows the running step while workflow gate feedback is still being sent', async () => {
    let resolveFeedback!: () => void;
    const sendPlanFeedback = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveFeedback = resolve;
        }),
    );
    const svc = makeMockService({
      decompose: vi.fn(() => new Promise<Phase[]>(() => {})),
      spawnPlanSession: vi.fn().mockResolvedValue({
        ...MOCK_SESSION,
        campaignSlug: 'plan-auth',
      }),
      sendPlanFeedback,
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      void result.current.submitAnswers({ q1: 'Keep this to one saga' });
    });

    expect(sendPlanFeedback).toHaveBeenCalledWith(
      'plan-auth',
      'Planning feedback:\n- Keep this to one saga',
    );
    expect(result.current.state.step).toBe('running');
    expect(result.current.state.answers).toEqual({ q1: 'Keep this to one saga' });

    await act(async () => {
      resolveFeedback();
    });
  });

  it('surfaces workflow gate feedback failures on the running step', async () => {
    const svc = makeMockService({
      decompose: vi.fn(() => new Promise<Phase[]>(() => {})),
      spawnPlanSession: vi.fn().mockResolvedValue({
        ...MOCK_SESSION,
        campaignSlug: 'plan-auth',
      }),
      sendPlanFeedback: vi.fn().mockRejectedValue(new Error('gate unavailable')),
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'Keep this to one saga' });
    });

    expect(result.current.state.step).toBe('running');
    await waitFor(() => expect(result.current.state.error).toBe('gate unavailable'));
  });

  it('auto-decomposes and transitions to draft', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'niuulabs/volundr' });
    });

    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    expect(result.current.state.structure).not.toBeNull();
  });

  it('uses workflow-backed draft before falling back to decompose', async () => {
    const decompose = vi.fn().mockResolvedValue(MOCK_PHASES);
    const getPlanDraft = vi.fn().mockResolvedValue(MOCK_STRUCTURE);
    const svc = makeMockService({
      spawnPlanSession: vi.fn().mockResolvedValue({
        ...MOCK_SESSION,
        campaignSlug: 'plan-auth',
      }),
      getPlanDraft,
      decompose,
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'niuulabs/volundr' });
    });

    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    expect(getPlanDraft).toHaveBeenCalledWith('plan-auth');
    expect(decompose).not.toHaveBeenCalled();
    expect(result.current.state.structure?.structure?.name).toBe('Test Saga');
  });

  it('waits for workflow draft when it is not ready', async () => {
    const decompose = vi.fn().mockResolvedValue(MOCK_PHASES);
    const getPlanDraft = vi.fn().mockResolvedValue({ found: false, structure: null });
    const svc = makeMockService({
      spawnPlanSession: vi.fn().mockResolvedValue({
        ...MOCK_SESSION,
        campaignSlug: 'plan-auth',
      }),
      getPlanDraft,
      decompose,
    });
    const { result, unmount } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'niuulabs/volundr' });
    });

    await waitFor(() => expect(getPlanDraft).toHaveBeenCalledTimes(1));
    expect(result.current.state.step).toBe('running');
    expect(decompose).not.toHaveBeenCalled();

    unmount();
  });
});

describe('usePlanWizard — approveDraft', () => {
  async function advanceToDraft(svc: Partial<ITingService>) {
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    await act(async () => {
      await result.current.submitAnswers({ q1: 'niuulabs/volundr' });
    });

    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    return result;
  }

  it('transitions to approved on success', async () => {
    const svc = makeMockService();
    const result = await advanceToDraft(svc);

    await act(async () => {
      await result.current.approveDraft();
    });

    expect(result.current.state.step).toBe('approved');
    expect(result.current.state.saga).not.toBeNull();
  });

  it('sets error on commit failure', async () => {
    const svc = makeMockService({
      commitSaga: vi.fn().mockRejectedValue(new Error('commit failed')),
    });
    const result = await advanceToDraft(svc);

    await act(async () => {
      await result.current.approveDraft();
    });

    expect(result.current.state.error).toBe('commit failed');
    expect(result.current.state.step).toBe('draft');
  });
});

describe('usePlanWizard — back', () => {
  it('goes back from questions to prompt', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    act(() => result.current.back());
    expect(result.current.state.step).toBe('prompt');
  });

  it('does nothing on prompt step', () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });
    act(() => result.current.back());
    expect(result.current.state.step).toBe('prompt');
  });
});

describe('usePlanWizard — editPhase', () => {
  it('updates a phase name in the structure', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });
    await act(async () => {
      await result.current.submitAnswers({});
    });
    await waitFor(() => expect(result.current.state.step).toBe('draft'));

    act(() => result.current.editPhase(0, 'Renamed Phase'));
    expect(result.current.state.structure?.structure?.phases[0]?.name).toBe('Renamed Phase');
  });
});

describe('usePlanWizard — clearError', () => {
  it('clears the error state', async () => {
    const svc = makeMockService({
      spawnPlanSession: vi.fn().mockRejectedValue(new Error('oops')),
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'repo');
    });

    expect(result.current.state.error).toBe('oops');
    act(() => result.current.clearError());
    expect(result.current.state.error).toBeNull();
  });
});

describe('usePlanWizard — decompose error', () => {
  it('sets error when decompose fails', async () => {
    const svc = makeMockService({
      decompose: vi.fn().mockRejectedValue(new Error('decompose failed')),
    });
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'repo');
    });
    await act(async () => {
      await result.current.submitAnswers({});
    });

    await waitFor(() => expect(result.current.state.error).toBe('decompose failed'));
    expect(result.current.state.step).toBe('running');
  });
});

describe('usePlanWizard — replan', () => {
  async function advanceToDraft(svc: Partial<ITingService>) {
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });
    await act(async () => {
      await result.current.submitPrompt('Build auth', 'repo');
    });
    await act(async () => {
      await result.current.submitAnswers({});
    });
    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    return result;
  }

  it('transitions from draft back to running', async () => {
    const svc = makeMockService();
    const result = await advanceToDraft(svc);

    act(() => result.current.replan());

    expect(result.current.state.step).toBe('running');
  });

  it('clears structure and phases on replan', async () => {
    const svc = makeMockService();
    const result = await advanceToDraft(svc);

    act(() => result.current.replan());

    expect(result.current.state.structure).toBeNull();
    expect(result.current.state.phases).toHaveLength(0);
  });

  it('auto-decomposes again after replan', async () => {
    const svc = makeMockService();
    const result = await advanceToDraft(svc);

    act(() => result.current.replan());

    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    // decompose called twice: once initially, once after replan
    expect(svc.decompose).toHaveBeenCalledTimes(2);
  });
});

describe('usePlanWizard — saveDraft', () => {
  it('does not change step or clear state', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'repo');
    });
    await act(async () => {
      await result.current.submitAnswers({});
    });
    await waitFor(() => expect(result.current.state.step).toBe('draft'));

    const stepBefore = result.current.state.step;
    act(() => result.current.saveDraft());

    expect(result.current.state.step).toBe(stepBefore);
  });

  it('sets draftSaved to true', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'repo');
    });
    await act(async () => {
      await result.current.submitAnswers({});
    });
    await waitFor(() => expect(result.current.state.step).toBe('draft'));

    expect(result.current.state.draftSaved).toBe(false);
    act(() => result.current.saveDraft());
    expect(result.current.state.draftSaved).toBe(true);
  });
});

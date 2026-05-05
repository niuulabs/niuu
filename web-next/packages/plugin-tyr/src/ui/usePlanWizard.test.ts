import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { usePlanWizard } from './usePlanWizard';
import type { ITyrService } from '../ports';
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
    raids: [],
  },
];

const MOCK_STRUCTURE: ExtractedStructure = {
  found: true,
  structure: {
    name: 'Test Saga',
    phases: [
      {
        name: 'Phase 1',
        raids: [
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

function makeWrapper(svc: Partial<ITyrService>) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(ServicesProvider, { services: { tyr: svc } }, children);
  };
}

function makeMockService(overrides: Partial<ITyrService> = {}): Partial<ITyrService> {
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
  it('transitions to raiding step', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    act(() => {
      result.current.submitAnswers({ q1: 'niuulabs/volundr', q2: 'main' });
    });

    expect(result.current.state.step).toBe('raiding');
    expect(result.current.state.answers).toEqual({ q1: 'niuulabs/volundr', q2: 'main' });
  });

  it('auto-decomposes and transitions to draft', async () => {
    const svc = makeMockService();
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    act(() => {
      result.current.submitAnswers({ q1: 'niuulabs/volundr' });
    });

    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    expect(result.current.state.structure).not.toBeNull();
  });
});

describe('usePlanWizard — approveDraft', () => {
  async function advanceToDraft(svc: Partial<ITyrService>) {
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });

    await act(async () => {
      await result.current.submitPrompt('Build auth', 'niuulabs/volundr');
    });

    act(() => {
      result.current.submitAnswers({ q1: 'niuulabs/volundr' });
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
    act(() => result.current.submitAnswers({}));
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
    act(() => result.current.submitAnswers({}));

    await waitFor(() => expect(result.current.state.error).toBe('decompose failed'));
    expect(result.current.state.step).toBe('raiding');
  });
});

describe('usePlanWizard — replan', () => {
  async function advanceToDraft(svc: Partial<ITyrService>) {
    const { result } = renderHook(() => usePlanWizard(), { wrapper: makeWrapper(svc) });
    await act(async () => {
      await result.current.submitPrompt('Build auth', 'repo');
    });
    act(() => result.current.submitAnswers({}));
    await waitFor(() => expect(result.current.state.step).toBe('draft'));
    return result;
  }

  it('transitions from draft back to raiding', async () => {
    const svc = makeMockService();
    const result = await advanceToDraft(svc);

    act(() => result.current.replan());

    expect(result.current.state.step).toBe('raiding');
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
    act(() => result.current.submitAnswers({}));
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
    act(() => result.current.submitAnswers({}));
    await waitFor(() => expect(result.current.state.step).toBe('draft'));

    expect(result.current.state.draftSaved).toBe(false);
    act(() => result.current.saveDraft());
    expect(result.current.state.draftSaved).toBe(true);
  });
});

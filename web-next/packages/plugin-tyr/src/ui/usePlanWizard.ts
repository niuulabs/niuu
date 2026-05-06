import { useReducer, useEffect, useRef } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import { planTransition, type PlanStep } from '../domain/plan';
import type { ITyrService, CommitSagaRequest, PlanSession, ExtractedStructure } from '../ports';
import type { ClarifyingQuestion } from '../domain/plan';
import type { Saga, Phase } from '../domain/saga';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface PlanWizardState {
  step: PlanStep;
  prompt: string;
  repo: string;
  session: PlanSession | null;
  questions: ClarifyingQuestion[];
  answers: Record<string, string>;
  phases: Phase[];
  structure: ExtractedStructure | null;
  saga: Saga | null;
  loading: boolean;
  error: string | null;
  /** True after saveDraft() is called; records the intent for testing. */
  draftSaved: boolean;
}

const initialState: PlanWizardState = {
  step: 'prompt',
  prompt: '',
  repo: '',
  session: null,
  questions: [],
  answers: {},
  phases: [],
  structure: null,
  saga: null,
  loading: false,
  error: null,
  draftSaved: false,
};

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

type Action =
  | { type: 'SET_LOADING' }
  | { type: 'SESSION_READY'; prompt: string; repo: string; session: PlanSession }
  | { type: 'SUBMIT_ANSWERS'; answers: Record<string, string> }
  | { type: 'DECOMPOSE_DONE'; phases: Phase[]; structure: ExtractedStructure }
  | { type: 'DECOMPOSE_ERROR'; error: string }
  | { type: 'APPROVE_DONE'; saga: Saga }
  | { type: 'APPROVE_ERROR'; error: string }
  | { type: 'BACK' }
  | { type: 'REPLAN' }
  | { type: 'EDIT_PHASE'; phaseIndex: number; name: string }
  | { type: 'REMOVE_RAID'; phaseIndex: number; raidIndex: number }
  | { type: 'SAVE_DRAFT' }
  | { type: 'CLEAR_ERROR' };

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

function reducer(state: PlanWizardState, action: Action): PlanWizardState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, loading: true, error: null };

    case 'SESSION_READY': {
      const step = planTransition(state.step, 'questions');
      return {
        ...state,
        step,
        prompt: action.prompt,
        repo: action.repo,
        session: action.session,
        questions: action.session.questions,
        loading: false,
        error: null,
      };
    }

    case 'SUBMIT_ANSWERS': {
      const step = planTransition(state.step, 'raiding');
      return {
        ...state,
        step,
        answers: action.answers,
        loading: false,
        error: null,
      };
    }

    case 'DECOMPOSE_DONE': {
      const step = planTransition(state.step, 'draft');
      return {
        ...state,
        step,
        phases: action.phases,
        structure: action.structure,
        loading: false,
        error: null,
      };
    }

    case 'DECOMPOSE_ERROR':
      return {
        ...state,
        loading: false,
        error: action.error,
      };

    case 'APPROVE_DONE': {
      const step = planTransition(state.step, 'approved');
      return {
        ...state,
        step,
        saga: action.saga,
        loading: false,
        error: null,
      };
    }

    case 'APPROVE_ERROR':
      return { ...state, loading: false, error: action.error };

    case 'BACK': {
      const backMap: Partial<Record<PlanStep, PlanStep>> = {
        questions: 'prompt',
        raiding: 'questions',
        draft: 'raiding',
      };
      const target = backMap[state.step];
      if (!target) return state;
      const step = planTransition(state.step, target);
      return { ...state, step, loading: false, error: null };
    }

    case 'REPLAN': {
      const step = planTransition(state.step, 'raiding');
      return { ...state, step, structure: null, phases: [], loading: false, error: null };
    }

    case 'EDIT_PHASE': {
      if (!state.structure?.structure) return state;
      const phases = state.structure.structure.phases.map((p, i) =>
        i === action.phaseIndex ? { ...p, name: action.name } : p,
      );
      return {
        ...state,
        structure: {
          ...state.structure,
          structure: { ...state.structure.structure, phases },
        },
      };
    }

    case 'REMOVE_RAID': {
      if (!state.structure?.structure) return state;
      const phases = state.structure.structure.phases.map((p, i) => {
        if (i !== action.phaseIndex) return p;
        return { ...p, raids: p.raids.filter((_, ri) => ri !== action.raidIndex) };
      });
      return {
        ...state,
        structure: {
          ...state.structure,
          structure: { ...state.structure.structure, phases },
        },
      };
    }

    case 'SAVE_DRAFT':
      return { ...state, draftSaved: true };

    case 'CLEAR_ERROR':
      return { ...state, error: null };

    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildFullSpec(prompt: string, answers: Record<string, string>): string {
  const answerBlock = Object.values(answers)
    .filter(Boolean)
    .map((a) => `- ${a}`)
    .join('\n');
  if (!answerBlock) return prompt;
  return `${prompt}\n\nAdditional context:\n${answerBlock}`;
}

function buildCommitRequest(state: PlanWizardState): CommitSagaRequest {
  const structure = state.structure?.structure;
  const name = structure?.name ?? 'New Saga';
  const slug = name
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '');

  return {
    name,
    slug,
    description: state.prompt,
    repos: state.repo ? [state.repo] : [],
    baseBranch: 'main',
    phases: (structure?.phases ?? []).map((p) => ({
      name: p.name,
      raids: p.raids.map((r) => ({
        name: r.name,
        description: r.description,
        acceptanceCriteria: r.acceptanceCriteria,
        declaredFiles: r.declaredFiles,
        estimateHours: r.estimateHours,
      })),
    })),
    transcript: buildFullSpec(state.prompt, state.answers),
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface PlanWizardActions {
  submitPrompt(prompt: string, repo: string): Promise<void>;
  submitAnswers(answers: Record<string, string>): void;
  approveDraft(): Promise<void>;
  editPhase(phaseIndex: number, name: string): void;
  removeRaid(phaseIndex: number, raidIndex: number): void;
  back(): void;
  clearError(): void;
  /** Re-run decomposition with the same prompt and answers. */
  replan(): void;
  /** Persist the current draft state without creating the saga. No backend yet — dispatches SAVE_DRAFT. */
  saveDraft(): void;
}

export function usePlanWizard(): { state: PlanWizardState } & PlanWizardActions {
  const tyr = useService<ITyrService>('tyr');
  const [state, dispatch] = useReducer(reducer, initialState);
  // Keep a stable ref to the latest state for effects
  const stateRef = useRef(state);
  stateRef.current = state;

  // Auto-decompose when entering the raiding step
  useEffect(() => {
    if (state.step !== 'raiding') return;

    let cancelled = false;

    async function runDecompose() {
      const { prompt, repo, answers } = stateRef.current;
      const fullSpec = buildFullSpec(prompt, answers);
      try {
        const phases = await tyr.decompose(fullSpec, repo);
        if (cancelled) return;
        const phasesText = JSON.stringify(phases);
        const structure = await tyr.extractStructure(phasesText);
        if (cancelled) return;
        dispatch({ type: 'DECOMPOSE_DONE', phases, structure });
      } catch (err) {
        if (cancelled) return;
        dispatch({
          type: 'DECOMPOSE_ERROR',
          error: err instanceof Error ? err.message : 'Decomposition failed',
        });
      }
    }

    void runDecompose();
    return () => {
      cancelled = true;
    };
  }, [state.step, tyr]);

  async function submitPrompt(prompt: string, repo: string) {
    dispatch({ type: 'SET_LOADING' });
    try {
      const session = await tyr.spawnPlanSession(prompt, repo);
      dispatch({ type: 'SESSION_READY', prompt, repo, session });
    } catch (err) {
      dispatch({
        type: 'DECOMPOSE_ERROR',
        error: err instanceof Error ? err.message : 'Failed to start plan session',
      });
    }
  }

  function submitAnswers(answers: Record<string, string>) {
    dispatch({ type: 'SUBMIT_ANSWERS', answers });
  }

  async function approveDraft() {
    dispatch({ type: 'SET_LOADING' });
    try {
      const request = buildCommitRequest(stateRef.current);
      const saga = await tyr.commitSaga(request);
      dispatch({ type: 'APPROVE_DONE', saga });
    } catch (err) {
      dispatch({
        type: 'APPROVE_ERROR',
        error: err instanceof Error ? err.message : 'Failed to commit saga',
      });
    }
  }

  function editPhase(phaseIndex: number, name: string) {
    dispatch({ type: 'EDIT_PHASE', phaseIndex, name });
  }

  function back() {
    dispatch({ type: 'BACK' });
  }

  function clearError() {
    dispatch({ type: 'CLEAR_ERROR' });
  }

  function removeRaid(phaseIndex: number, raidIndex: number) {
    dispatch({ type: 'REMOVE_RAID', phaseIndex, raidIndex });
  }

  function replan() {
    dispatch({ type: 'REPLAN' });
  }

  function saveDraft() {
    // TODO: send to backend when persistence endpoint is available.
    dispatch({ type: 'SAVE_DRAFT' });
  }

  return {
    state,
    submitPrompt,
    submitAnswers,
    approveDraft,
    editPhase,
    removeRaid,
    back,
    clearError,
    replan,
    saveDraft,
  };
}

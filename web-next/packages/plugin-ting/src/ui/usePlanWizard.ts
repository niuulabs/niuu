import { useReducer, useEffect, useRef } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import { planTransition, type PlanStep } from '../domain/plan';
import type { ITingService, CommitSagaRequest, PlanSession, ExtractedStructure } from '../ports';
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

const PLAN_DRAFT_POLL_MS = 5000;

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

type Action =
  | { type: 'SET_LOADING' }
  | { type: 'SESSION_READY'; prompt: string; repo: string; session: PlanSession }
  | { type: 'RESUME_READY'; session: PlanSession }
  | { type: 'SESSION_STATUS'; session: PlanSession }
  | { type: 'SUBMIT_ANSWERS'; answers: Record<string, string> }
  | { type: 'DECOMPOSE_DONE'; phases: Phase[]; structure: ExtractedStructure }
  | { type: 'DECOMPOSE_ERROR'; error: string }
  | { type: 'APPROVE_DONE'; saga: Saga }
  | { type: 'APPROVE_ERROR'; error: string }
  | { type: 'BACK' }
  | { type: 'REPLAN'; questions?: ClarifyingQuestion[] }
  | { type: 'EDIT_PHASE'; phaseIndex: number; name: string }
  | { type: 'REMOVE_RUN'; phaseIndex: number; runIndex: number }
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

    case 'RESUME_READY': {
      // questions is optional on PlanSession: a plan that has moved past
      // clarification comes back without it, and reading .length threw.
      const questions = action.session.questions ?? [];
      return {
        ...state,
        step: questions.length > 0 ? 'questions' : 'running',
        prompt: action.session.prompt ?? action.session.name ?? state.prompt,
        repo: action.session.repo ?? '',
        session: action.session,
        questions,
        answers: {},
        structure: null,
        phases: [],
        loading: false,
        error: null,
      };
    }

    case 'SESSION_STATUS': {
      const questions =
        (action.session.questions ?? []).length > 0 ? action.session.questions : state.questions;
      return {
        ...state,
        session: state.session ? { ...state.session, ...action.session } : action.session,
        questions,
      };
    }

    case 'SUBMIT_ANSWERS': {
      const step = planTransition(state.step, 'running');
      return {
        ...state,
        step,
        answers: action.answers,
        structure: null,
        phases: [],
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
        running: 'questions',
        draft: 'running',
      };
      const target = backMap[state.step];
      if (!target) return state;
      const step = planTransition(state.step, target);
      return { ...state, step, loading: false, error: null };
    }

    case 'REPLAN': {
      const step = action.questions?.length
        ? planTransition(state.step, 'questions')
        : planTransition(state.step, 'running');
      return {
        ...state,
        step,
        questions: action.questions ?? state.questions,
        answers: action.questions?.length ? {} : state.answers,
        structure: action.questions?.length ? state.structure : null,
        phases: action.questions?.length ? state.phases : [],
        loading: false,
        error: null,
      };
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

    case 'REMOVE_RUN': {
      if (!state.structure?.structure) return state;
      const phases = state.structure.structure.phases.map((p, i) => {
        if (i !== action.phaseIndex) return p;
        return { ...p, runs: p.runs.filter((_, ri) => ri !== action.runIndex) };
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

function buildFeedbackMessage(answers: Record<string, string>): string {
  const lines = Object.values(answers)
    .map((answer) => answer.trim())
    .filter(Boolean)
    .map((answer) => `- ${answer}`);
  if (lines.length === 0) return '';
  const label = Object.keys(answers).some((id) => id === 'draft-feedback')
    ? 'Draft feedback:'
    : 'Planning feedback:';
  return [label, ...lines].join('\n');
}

function defaultDraftFeedbackQuestion(): ClarifyingQuestion {
  return {
    id: 'draft-feedback',
    question: 'What focused changes should the planning workflow make to this draft?',
    hint: 'Request one bounded change. Ting will send it back through the workflow before showing a revised draft.',
  };
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
      runs: p.runs.map((r) => ({
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
  resumePlanSession(session: PlanSession): Promise<void>;
  submitAnswers(answers: Record<string, string>): Promise<void>;
  approveDraft(): Promise<void>;
  editPhase(phaseIndex: number, name: string): void;
  removeRun(phaseIndex: number, runIndex: number): void;
  back(): void;
  clearError(): void;
  /** Re-run decomposition with the same prompt and answers. */
  replan(): void;
  /** Keep the current draft state in this wizard without creating the saga. */
  saveDraft(): void;
}

export function usePlanWizard(): { state: PlanWizardState } & PlanWizardActions {
  const ting = useService<ITingService>('ting');
  const [state, dispatch] = useReducer(reducer, initialState);
  // Keep a stable ref to the latest state for effects
  const stateRef = useRef(state);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const campaignSlug = state.session?.campaignSlug;
    const getPlanSession = ting.getPlanSession;
    if (!campaignSlug || state.step === 'approved' || !getPlanSession) return;

    const refreshSlug = campaignSlug;
    const refresh = getPlanSession;
    let cancelled = false;
    async function refreshPlanSession() {
      try {
        const session = await refresh(refreshSlug);
        if (!cancelled && session) {
          dispatch({ type: 'SESSION_STATUS', session });
        }
      } catch {
        // Polling is advisory; the active planning flow should keep moving.
      }
    }

    void refreshPlanSession();
    const timer = window.setInterval(() => {
      void refreshPlanSession();
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [state.session?.campaignSlug, state.step, ting]);

  // Auto-decompose when entering the running step
  useEffect(() => {
    if (state.step !== 'running') return;

    let cancelled = false;

    async function runDecompose() {
      const { prompt, repo, answers, session } = stateRef.current;
      const fullSpec = buildFullSpec(prompt, answers);
      try {
        if (session?.campaignSlug && ting.getPlanDraft) {
          while (!cancelled) {
            const draft = await ting.getPlanDraft(session.campaignSlug);
            if (cancelled) return;
            if (draft.found && draft.structure) {
              dispatch({ type: 'DECOMPOSE_DONE', phases: [], structure: draft });
              return;
            }
            await new Promise((resolve) => window.setTimeout(resolve, PLAN_DRAFT_POLL_MS));
          }
          return;
        }

        const phases = await ting.decompose(fullSpec, repo);
        if (cancelled) return;
        const phasesText = JSON.stringify(phases);
        const structure = await ting.extractStructure(phasesText);
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
  }, [state.step, ting]);

  async function submitPrompt(prompt: string, repo: string) {
    dispatch({ type: 'SET_LOADING' });
    try {
      const trimmedRepo = repo.trim();
      const session = await ting.spawnPlanSession(prompt, trimmedRepo);
      dispatch({ type: 'SESSION_READY', prompt, repo: trimmedRepo, session });
    } catch (err) {
      dispatch({
        type: 'DECOMPOSE_ERROR',
        error: err instanceof Error ? err.message : 'Failed to start plan session',
      });
    }
  }

  async function resumePlanSession(session: PlanSession) {
    dispatch({ type: 'SET_LOADING' });
    try {
      const refreshed =
        session.campaignSlug && ting.getPlanSession
          ? await ting.getPlanSession(session.campaignSlug)
          : session;
      dispatch({ type: 'RESUME_READY', session: refreshed ?? session });
    } catch (err) {
      dispatch({
        type: 'DECOMPOSE_ERROR',
        error: err instanceof Error ? err.message : 'Failed to resume plan session',
      });
    }
  }

  async function submitAnswers(answers: Record<string, string>) {
    const campaignSlug = stateRef.current.session?.campaignSlug;
    const feedback = buildFeedbackMessage(answers);
    const decision = Object.keys(answers).some((id) => id === 'draft-feedback')
      ? 'changes_requested'
      : 'approve';
    dispatch({ type: 'SUBMIT_ANSWERS', answers });
    if (campaignSlug && feedback && ting.sendPlanFeedback) {
      try {
        await ting.sendPlanFeedback(campaignSlug, feedback, decision);
      } catch (err) {
        dispatch({
          type: 'DECOMPOSE_ERROR',
          error: err instanceof Error ? err.message : 'Failed to send plan feedback',
        });
        return;
      }
    }
  }

  async function approveDraft() {
    dispatch({ type: 'SET_LOADING' });
    try {
      const campaignSlug = stateRef.current.session?.campaignSlug;
      if (campaignSlug && ting.sendPlanFeedback) {
        try {
          await ting.sendPlanFeedback(campaignSlug, 'Approved in Ting Plan.', 'approve');
        } catch {
          // Final approval already carries the user's commit intent; a closed workflow session
          // should not block materializing the reviewed draft.
        }
      }
      const request = buildCommitRequest(stateRef.current);
      const saga = await ting.commitSaga(request);
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

  function removeRun(phaseIndex: number, runIndex: number) {
    dispatch({ type: 'REMOVE_RUN', phaseIndex, runIndex });
  }

  function replan() {
    const campaignSlug = stateRef.current.session?.campaignSlug;
    const draftFeedbackQuestions = stateRef.current.questions.filter(
      (question) => question.id === 'draft-feedback',
    );
    dispatch({
      type: 'REPLAN',
      questions: campaignSlug
        ? draftFeedbackQuestions.length > 0
          ? draftFeedbackQuestions
          : [defaultDraftFeedbackQuestion()]
        : undefined,
    });
  }

  function saveDraft() {
    dispatch({ type: 'SAVE_DRAFT' });
  }

  return {
    state,
    submitPrompt,
    resumePlanSession,
    submitAnswers,
    approveDraft,
    editPhase,
    removeRun,
    back,
    clearError,
    replan,
    saveDraft,
  };
}

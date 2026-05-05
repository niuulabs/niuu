/**
 * Plan wizard domain model.
 *
 * Pure state machine describing the five steps of the Tyr plan wizard.
 * No framework imports — business logic only.
 */

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

export type PlanStep = 'prompt' | 'questions' | 'raiding' | 'draft' | 'approved';

export const PLAN_STEPS: PlanStep[] = ['prompt', 'questions', 'raiding', 'draft', 'approved'];

export const PLAN_STEP_LABELS: Record<PlanStep, string> = {
  prompt: 'Describe',
  questions: 'Clarify',
  raiding: 'Decompose',
  draft: 'Review',
  approved: 'Launch',
};

// ---------------------------------------------------------------------------
// Transitions
// ---------------------------------------------------------------------------

const VALID_TRANSITIONS: Record<PlanStep, readonly PlanStep[]> = {
  prompt: ['questions'],
  questions: ['raiding', 'prompt'],
  raiding: ['draft', 'questions'],
  draft: ['approved', 'raiding'],
  approved: [],
};

export class PlanTransitionError extends Error {
  readonly from: PlanStep;
  readonly to: PlanStep;

  constructor(from: PlanStep, to: PlanStep) {
    super(`Invalid plan transition: ${from} → ${to}`);
    this.name = 'PlanTransitionError';
    this.from = from;
    this.to = to;
  }
}

/**
 * Validate and execute a state transition.
 * Throws PlanTransitionError if the transition is not in the allowed set.
 */
export function planTransition(from: PlanStep, to: PlanStep): PlanStep {
  const allowed = VALID_TRANSITIONS[from];
  if (!(allowed as readonly string[]).includes(to)) {
    throw new PlanTransitionError(from, to);
  }
  return to;
}

/**
 * Returns whether a transition from `from` to `to` is valid without throwing.
 */
export function canTransition(from: PlanStep, to: PlanStep): boolean {
  return (VALID_TRANSITIONS[from] as readonly string[]).includes(to);
}

/**
 * Returns the zero-based index of a step in PLAN_STEPS.
 */
export function stepIndex(step: PlanStep): number {
  return PLAN_STEPS.indexOf(step);
}

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

export interface ClarifyingQuestion {
  id: string;
  question: string;
  hint?: string;
  /** When 'workflow', renders a template picker grid instead of a text input. */
  kind?: 'text' | 'workflow';
}

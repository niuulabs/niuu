import { describe, it, expect } from 'vitest';
import {
  planTransition,
  canTransition,
  PlanTransitionError,
  PLAN_STEPS,
  PLAN_STEP_LABELS,
  stepIndex,
  type PlanStep,
} from './plan';

describe('planTransition — valid paths', () => {
  it('allows prompt → questions', () => {
    expect(planTransition('prompt', 'questions')).toBe('questions');
  });

  it('allows questions → raiding', () => {
    expect(planTransition('questions', 'raiding')).toBe('raiding');
  });

  it('allows questions → prompt (back)', () => {
    expect(planTransition('questions', 'prompt')).toBe('prompt');
  });

  it('allows raiding → draft', () => {
    expect(planTransition('raiding', 'draft')).toBe('draft');
  });

  it('allows raiding → questions (back)', () => {
    expect(planTransition('raiding', 'questions')).toBe('questions');
  });

  it('allows draft → approved', () => {
    expect(planTransition('draft', 'approved')).toBe('approved');
  });

  it('allows draft → raiding (back)', () => {
    expect(planTransition('draft', 'raiding')).toBe('raiding');
  });
});

describe('planTransition — refused transitions', () => {
  it('refuses prompt → raiding (skip questions)', () => {
    expect(() => planTransition('prompt', 'raiding')).toThrow(PlanTransitionError);
  });

  it('refuses prompt → draft (skip ahead)', () => {
    expect(() => planTransition('prompt', 'draft')).toThrow(PlanTransitionError);
  });

  it('refuses prompt → approved (skip all)', () => {
    expect(() => planTransition('prompt', 'approved')).toThrow(PlanTransitionError);
  });

  it('refuses questions → draft (skip raiding)', () => {
    expect(() => planTransition('questions', 'draft')).toThrow(PlanTransitionError);
  });

  it('refuses questions → approved', () => {
    expect(() => planTransition('questions', 'approved')).toThrow(PlanTransitionError);
  });

  it('refuses raiding → prompt (skip back two)', () => {
    expect(() => planTransition('raiding', 'prompt')).toThrow(PlanTransitionError);
  });

  it('refuses raiding → approved (skip draft)', () => {
    expect(() => planTransition('raiding', 'approved')).toThrow(PlanTransitionError);
  });

  it('refuses draft → prompt', () => {
    expect(() => planTransition('draft', 'prompt')).toThrow(PlanTransitionError);
  });

  it('refuses approved → prompt (no restart)', () => {
    expect(() => planTransition('approved', 'prompt')).toThrow(PlanTransitionError);
  });

  it('refuses approved → draft', () => {
    expect(() => planTransition('approved', 'draft')).toThrow(PlanTransitionError);
  });

  it('refuses approved → approved (self-loop)', () => {
    expect(() => planTransition('approved', 'approved')).toThrow(PlanTransitionError);
  });
});

describe('PlanTransitionError', () => {
  it('carries from and to properties', () => {
    let caught: unknown;
    try {
      planTransition('prompt', 'approved');
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlanTransitionError);
    const err = caught as PlanTransitionError;
    expect(err.from).toBe('prompt');
    expect(err.to).toBe('approved');
  });

  it('has descriptive message', () => {
    const err = new PlanTransitionError('prompt', 'approved');
    expect(err.message).toContain('prompt');
    expect(err.message).toContain('approved');
  });

  it('has correct name', () => {
    const err = new PlanTransitionError('questions', 'draft');
    expect(err.name).toBe('PlanTransitionError');
  });
});

describe('canTransition', () => {
  const validPairs: [PlanStep, PlanStep][] = [
    ['prompt', 'questions'],
    ['questions', 'raiding'],
    ['questions', 'prompt'],
    ['raiding', 'draft'],
    ['raiding', 'questions'],
    ['draft', 'approved'],
    ['draft', 'raiding'],
  ];

  it.each(validPairs)('returns true for %s → %s', (from, to) => {
    expect(canTransition(from, to)).toBe(true);
  });

  const invalidPairs: [PlanStep, PlanStep][] = [
    ['prompt', 'raiding'],
    ['prompt', 'draft'],
    ['prompt', 'approved'],
    ['questions', 'draft'],
    ['approved', 'prompt'],
    ['approved', 'approved'],
  ];

  it.each(invalidPairs)('returns false for %s → %s', (from, to) => {
    expect(canTransition(from, to)).toBe(false);
  });
});

describe('PLAN_STEPS', () => {
  it('has exactly 5 steps', () => {
    expect(PLAN_STEPS).toHaveLength(5);
  });

  it('is ordered: prompt → questions → raiding → draft → approved', () => {
    expect(PLAN_STEPS).toEqual(['prompt', 'questions', 'raiding', 'draft', 'approved']);
  });
});

describe('PLAN_STEP_LABELS', () => {
  it('has a label for every step', () => {
    for (const step of PLAN_STEPS) {
      expect(PLAN_STEP_LABELS[step]).toBeTruthy();
    }
  });
});

describe('stepIndex', () => {
  it('returns 0 for prompt', () => expect(stepIndex('prompt')).toBe(0));
  it('returns 1 for questions', () => expect(stepIndex('questions')).toBe(1));
  it('returns 2 for raiding', () => expect(stepIndex('raiding')).toBe(2));
  it('returns 3 for draft', () => expect(stepIndex('draft')).toBe(3));
  it('returns 4 for approved', () => expect(stepIndex('approved')).toBe(4));
});

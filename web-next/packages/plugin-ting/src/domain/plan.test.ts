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

  it('allows questions → running', () => {
    expect(planTransition('questions', 'running')).toBe('running');
  });

  it('allows questions → prompt (back)', () => {
    expect(planTransition('questions', 'prompt')).toBe('prompt');
  });

  it('allows running → draft', () => {
    expect(planTransition('running', 'draft')).toBe('draft');
  });

  it('allows running → questions (back)', () => {
    expect(planTransition('running', 'questions')).toBe('questions');
  });

  it('allows draft → approved', () => {
    expect(planTransition('draft', 'approved')).toBe('approved');
  });

  it('allows draft → running (back)', () => {
    expect(planTransition('draft', 'running')).toBe('running');
  });

  it('allows draft → questions (workflow change request)', () => {
    expect(planTransition('draft', 'questions')).toBe('questions');
  });
});

describe('planTransition — refused transitions', () => {
  it('refuses prompt → running (skip questions)', () => {
    expect(() => planTransition('prompt', 'running')).toThrow(PlanTransitionError);
  });

  it('refuses prompt → draft (skip ahead)', () => {
    expect(() => planTransition('prompt', 'draft')).toThrow(PlanTransitionError);
  });

  it('refuses prompt → approved (skip all)', () => {
    expect(() => planTransition('prompt', 'approved')).toThrow(PlanTransitionError);
  });

  it('refuses questions → draft (skip running)', () => {
    expect(() => planTransition('questions', 'draft')).toThrow(PlanTransitionError);
  });

  it('refuses questions → approved', () => {
    expect(() => planTransition('questions', 'approved')).toThrow(PlanTransitionError);
  });

  it('refuses running → prompt (skip back two)', () => {
    expect(() => planTransition('running', 'prompt')).toThrow(PlanTransitionError);
  });

  it('refuses running → approved (skip draft)', () => {
    expect(() => planTransition('running', 'approved')).toThrow(PlanTransitionError);
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
    ['questions', 'running'],
    ['questions', 'prompt'],
    ['running', 'draft'],
    ['running', 'questions'],
    ['draft', 'approved'],
    ['draft', 'running'],
  ];

  it.each(validPairs)('returns true for %s → %s', (from, to) => {
    expect(canTransition(from, to)).toBe(true);
  });

  const invalidPairs: [PlanStep, PlanStep][] = [
    ['prompt', 'running'],
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

  it('is ordered: prompt → questions → running → draft → approved', () => {
    expect(PLAN_STEPS).toEqual(['prompt', 'questions', 'running', 'draft', 'approved']);
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
  it('returns 2 for running', () => expect(stepIndex('running')).toBe(2));
  it('returns 3 for draft', () => expect(stepIndex('draft')).toBe(3));
  it('returns 4 for approved', () => expect(stepIndex('approved')).toBe(4));
});

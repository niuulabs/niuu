import { describe, it, expect } from 'vitest';
import {
  classifyBudget,
  budgetRunway,
  budgetRatio,
  burnRate,
  projectedDepletion,
  burnTrend,
  runwayFraction,
} from './budgetAttention';
import type { BudgetState } from '@niuulabs/domain';

const budget = (spentUsd: number, capUsd: number, warnAt = 0.7): BudgetState => ({
  spentUsd,
  capUsd,
  warnAt,
});

describe('classifyBudget', () => {
  it('returns idle when capUsd is 0 (unlimited)', () => {
    expect(classifyBudget(budget(0, 0))).toBe('idle');
  });

  it('returns idle when ratio is below idle threshold (10%)', () => {
    expect(classifyBudget(budget(0.04, 5.0))).toBe('idle');
    expect(classifyBudget(budget(0, 5.0))).toBe('idle');
  });

  it('returns normal for mid-range spending', () => {
    expect(classifyBudget(budget(1.5, 5.0))).toBe('normal');
    expect(classifyBudget(budget(2.5, 5.0))).toBe('normal');
  });

  it('returns near-cap when ratio >= warnAt and < 0.9', () => {
    expect(classifyBudget(budget(4.0, 5.0))).toBe('near-cap'); // 80%
    expect(classifyBudget(budget(4.3, 5.0))).toBe('near-cap'); // 86%
  });

  it('returns burning-fast when ratio >= 90%', () => {
    expect(classifyBudget(budget(4.5, 5.0))).toBe('burning-fast'); // 90%
    expect(classifyBudget(budget(5.0, 5.0))).toBe('burning-fast'); // 100%
  });

  it('returns over-cap when ratio > 100%', () => {
    expect(classifyBudget(budget(5.1, 5.0))).toBe('over-cap');
    expect(classifyBudget(budget(10.0, 5.0))).toBe('over-cap');
  });

  it('respects custom warnAt threshold', () => {
    // warnAt = 0.5 → near-cap starts at 50%
    expect(classifyBudget(budget(2.6, 5.0, 0.5))).toBe('near-cap');
    // normal below 50%
    expect(classifyBudget(budget(2.0, 5.0, 0.5))).toBe('normal');
  });
});

describe('budgetRunway', () => {
  it('returns remaining budget', () => {
    expect(budgetRunway(budget(1.24, 5.0))).toBeCloseTo(3.76);
  });

  it('returns 0 when over cap', () => {
    expect(budgetRunway(budget(6.0, 5.0))).toBe(0);
  });

  it('returns full cap when nothing spent', () => {
    expect(budgetRunway(budget(0, 5.0))).toBe(5.0);
  });
});

describe('budgetRatio', () => {
  it('returns 0 when cap is 0', () => {
    expect(budgetRatio(budget(0, 0))).toBe(0);
  });

  it('returns ratio clamped to 1', () => {
    expect(budgetRatio(budget(5.0, 5.0))).toBe(1);
    expect(budgetRatio(budget(6.0, 5.0))).toBe(1);
  });

  it('calculates ratio correctly', () => {
    expect(budgetRatio(budget(2.5, 5.0))).toBeCloseTo(0.5);
  });
});

describe('burnRate', () => {
  it('returns spend per hour', () => {
    expect(burnRate(budget(12.0, 100.0), 6)).toBeCloseTo(2.0);
  });

  it('returns 0 when elapsedHours is 0', () => {
    expect(burnRate(budget(12.0, 100.0), 0)).toBe(0);
  });

  it('returns 0 when elapsedHours is negative', () => {
    expect(burnRate(budget(12.0, 100.0), -1)).toBe(0);
  });

  it('returns 0 when nothing spent', () => {
    expect(burnRate(budget(0, 100.0), 6)).toBe(0);
  });
});

describe('projectedDepletion', () => {
  it('returns hours until cap is breached', () => {
    // $5 remaining, $2/h rate → 2.5h
    expect(projectedDepletion(budget(5.0, 10.0), 2.0)).toBeCloseTo(2.5);
  });

  it('returns 0 when already over cap', () => {
    expect(projectedDepletion(budget(11.0, 10.0), 2.0)).toBe(0);
  });

  it('returns Infinity when rate is 0', () => {
    expect(projectedDepletion(budget(5.0, 10.0), 0)).toBe(Infinity);
  });

  it('returns Infinity when rate is negative', () => {
    expect(projectedDepletion(budget(5.0, 10.0), -1)).toBe(Infinity);
  });
});

describe('burnTrend', () => {
  it('returns accelerating when current rate is >10% above previous', () => {
    expect(burnTrend(2.3, 2.0)).toBe('accelerating'); // 15% increase
  });

  it('returns decelerating when current rate is >10% below previous', () => {
    expect(burnTrend(1.7, 2.0)).toBe('decelerating'); // 15% decrease
  });

  it('returns steady for small changes', () => {
    expect(burnTrend(2.05, 2.0)).toBe('steady');
    expect(burnTrend(1.95, 2.0)).toBe('steady');
  });

  it('returns steady when previous rate is 0', () => {
    expect(burnTrend(2.0, 0)).toBe('steady');
  });

  it('returns steady when rates are equal', () => {
    expect(burnTrend(2.0, 2.0)).toBe('steady');
  });
});

describe('runwayFraction', () => {
  it('returns 1 when no spend', () => {
    // zero rate → Infinity hours left → fraction = 1
    expect(runwayFraction(budget(0, 10.0), 12)).toBe(1);
  });

  it('returns 0 when already over cap', () => {
    expect(runwayFraction(budget(11.0, 10.0), 12)).toBe(0);
  });

  it('returns fraction in 0–1 range for partial spend', () => {
    const f = runwayFraction(budget(5.0, 10.0), 12);
    expect(f).toBeGreaterThan(0);
    expect(f).toBeLessThanOrEqual(1);
  });

  it('clamps result to [0, 1]', () => {
    const f = runwayFraction(budget(1.0, 10.0), 1);
    expect(f).toBeGreaterThanOrEqual(0);
    expect(f).toBeLessThanOrEqual(1);
  });
});

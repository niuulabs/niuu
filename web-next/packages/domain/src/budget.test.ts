import { describe, it, expect } from 'vitest';
import { budgetStateSchema } from './budget';

describe('budgetStateSchema', () => {
  it('round-trips a typical budget state', () => {
    const input = { spentUsd: 1.25, capUsd: 5.0, warnAt: 0.8 };
    expect(budgetStateSchema.parse(input)).toEqual(input);
  });

  it('accepts zero spent', () => {
    expect(budgetStateSchema.parse({ spentUsd: 0, capUsd: 5, warnAt: 0.5 }).spentUsd).toBe(0);
  });

  it('accepts zero capUsd (unlimited)', () => {
    expect(budgetStateSchema.parse({ spentUsd: 0, capUsd: 0, warnAt: 0.8 }).capUsd).toBe(0);
  });

  it('accepts warnAt = 0', () => {
    expect(budgetStateSchema.parse({ spentUsd: 0, capUsd: 10, warnAt: 0 }).warnAt).toBe(0);
  });

  it('accepts warnAt = 1', () => {
    expect(budgetStateSchema.parse({ spentUsd: 0, capUsd: 10, warnAt: 1 }).warnAt).toBe(1);
  });

  it('rejects negative spentUsd', () => {
    expect(() => budgetStateSchema.parse({ spentUsd: -0.01, capUsd: 5, warnAt: 0.8 })).toThrow();
  });

  it('rejects negative capUsd', () => {
    expect(() => budgetStateSchema.parse({ spentUsd: 0, capUsd: -1, warnAt: 0.8 })).toThrow();
  });

  it('rejects warnAt > 1', () => {
    expect(() => budgetStateSchema.parse({ spentUsd: 0, capUsd: 5, warnAt: 1.1 })).toThrow();
  });

  it('rejects warnAt < 0', () => {
    expect(() => budgetStateSchema.parse({ spentUsd: 0, capUsd: 5, warnAt: -0.1 })).toThrow();
  });

  it('rejects missing fields', () => {
    expect(() => budgetStateSchema.parse({ spentUsd: 1 })).toThrow();
  });
});

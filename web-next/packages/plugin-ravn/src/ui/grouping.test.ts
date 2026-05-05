import { describe, it, expect } from 'vitest';
import { groupRavens, topBudgetSpenders, modelToLocation } from './grouping';
import type { Ravn } from '../domain/ravn';
import type { BudgetState } from '@niuulabs/domain';

const RAVENS: Ravn[] = [
  {
    id: 'id-1',
    personaName: 'coder',
    status: 'active',
    model: 'claude-sonnet-4-6',
    createdAt: '2026-04-15T09:00:00Z',
  },
  {
    id: 'id-2',
    personaName: 'fjölnir',
    status: 'active',
    model: 'claude-opus-4-6',
    createdAt: '2026-04-15T08:00:00Z',
  },
  {
    id: 'id-3',
    personaName: 'gefjon',
    status: 'idle',
    model: 'claude-haiku-4-5',
    createdAt: '2026-04-15T07:00:00Z',
  },
  {
    id: 'id-4',
    personaName: 'höðr',
    status: 'suspended',
    model: 'claude-sonnet-4-6',
    createdAt: '2026-04-14T20:00:00Z',
  },
];

describe('modelToLocation', () => {
  it('maps opus model to asgard', () => {
    expect(modelToLocation('claude-opus-4-6')).toBe('asgard');
  });

  it('maps haiku model to jotunheim', () => {
    expect(modelToLocation('claude-haiku-4-5')).toBe('jotunheim');
  });

  it('maps sonnet (and unknown) model to midgard', () => {
    expect(modelToLocation('claude-sonnet-4-6')).toBe('midgard');
    expect(modelToLocation('unknown-model')).toBe('midgard');
  });
});

describe('groupRavens', () => {
  describe('none grouping', () => {
    it('returns a single "all" group sorted alphabetically by personaName', () => {
      const result = groupRavens(RAVENS, 'none');
      expect(Object.keys(result)).toEqual(['all']);
      const names = result.all!.map((r) => r.personaName);
      expect(names).toEqual([...names].sort());
    });

    it('returns all ravens', () => {
      const result = groupRavens(RAVENS, 'none');
      expect(result.all).toHaveLength(RAVENS.length);
    });
  });

  describe('state grouping', () => {
    it('groups ravens by status', () => {
      const result = groupRavens(RAVENS, 'state');
      expect(Object.keys(result).sort()).toEqual(['active', 'idle', 'suspended']);
      expect(result.active).toHaveLength(2);
      expect(result.idle).toHaveLength(1);
      expect(result.suspended).toHaveLength(1);
    });

    it('active group contains active ravens', () => {
      const result = groupRavens(RAVENS, 'state');
      expect(result.active!.every((r) => r.status === 'active')).toBe(true);
    });
  });

  describe('persona grouping', () => {
    it('groups ravens by personaName', () => {
      const result = groupRavens(RAVENS, 'persona');
      expect(Object.keys(result)).toContain('coder');
      expect(Object.keys(result)).toContain('fjölnir');
      expect(result.coder).toHaveLength(1);
    });

    it('contains all ravens across groups', () => {
      const result = groupRavens(RAVENS, 'persona');
      const total = Object.values(result).reduce((sum, g) => sum + g.length, 0);
      expect(total).toBe(RAVENS.length);
    });
  });

  describe('location grouping', () => {
    it('groups ravens by model-derived location', () => {
      const result = groupRavens(RAVENS, 'location');
      expect(result.asgard).toHaveLength(1); // opus
      expect(result.jotunheim).toHaveLength(1); // haiku
      expect(result.midgard).toHaveLength(2); // 2× sonnet
    });
  });

  describe('empty input', () => {
    it('returns empty groups for empty input (none)', () => {
      const result = groupRavens([], 'none');
      expect(result.all).toHaveLength(0);
    });

    it('returns empty groups for empty input (state)', () => {
      const result = groupRavens([], 'state');
      expect(Object.values(result).flat()).toHaveLength(0);
    });
  });

  describe('alphabetical ordering within groups', () => {
    it('sorts ravens alphabetically by personaName within each group', () => {
      const result = groupRavens(RAVENS, 'state');
      for (const group of Object.values(result)) {
        const names = group.map((r) => r.personaName);
        expect(names).toEqual([...names].sort());
      }
    });
  });
});

describe('topBudgetSpenders', () => {
  const budgets: Record<string, BudgetState> = {
    'id-1': { spentUsd: 1.5, capUsd: 5.0, warnAt: 0.7 },
    'id-2': { spentUsd: 3.9, capUsd: 5.0, warnAt: 0.7 },
    'id-3': { spentUsd: 0.1, capUsd: 2.0, warnAt: 0.7 },
    // id-4 has no budget entry
  };

  it('returns ravens sorted by spend descending', () => {
    const result = topBudgetSpenders(RAVENS, budgets);
    expect(result[0]!.id).toBe('id-2'); // highest spend 3.9
    expect(result[1]!.id).toBe('id-1'); // 1.5
    expect(result[2]!.id).toBe('id-3'); // 0.1
    expect(result[3]!.id).toBe('id-4'); // 0 (no entry)
  });

  it('respects the n limit', () => {
    const result = topBudgetSpenders(RAVENS, budgets, 2);
    expect(result).toHaveLength(2);
    expect(result[0]!.id).toBe('id-2');
    expect(result[1]!.id).toBe('id-1');
  });

  it('treats missing budget as $0', () => {
    const result = topBudgetSpenders(RAVENS, {});
    // All are $0, order from original
    expect(result).toHaveLength(RAVENS.length);
  });

  it('does not mutate the input array', () => {
    const original = [...RAVENS];
    topBudgetSpenders(RAVENS, budgets);
    expect(RAVENS).toEqual(original);
  });

  it('returns empty array for empty ravens input', () => {
    const result = topBudgetSpenders([], budgets);
    expect(result).toHaveLength(0);
  });
});

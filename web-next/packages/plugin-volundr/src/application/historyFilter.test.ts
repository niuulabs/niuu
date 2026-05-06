import { describe, it, expect } from 'vitest';
import { applyHistoryFilters } from './historyFilter';
import type { Session } from '../domain/session';

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 'ds-1',
    ravnId: 'r1',
    personaName: 'skald',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'terminated',
    startedAt: '2026-01-01T00:00:00Z',
    terminatedAt: '2026-01-02T00:00:00Z',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    ...overrides,
  };
}

describe('applyHistoryFilters', () => {
  const terminated = makeSession({ state: 'terminated' });
  const failed = makeSession({ id: 'ds-2', state: 'failed' });
  const running = makeSession({ id: 'ds-3', state: 'running' });
  const idle = makeSession({ id: 'ds-4', state: 'idle' });

  it('excludes non-terminal sessions', () => {
    const result = applyHistoryFilters([terminated, failed, running, idle], {});
    expect(result).toHaveLength(2);
    expect(result.find((s) => s.id === 'ds-3')).toBeUndefined();
    expect(result.find((s) => s.id === 'ds-4')).toBeUndefined();
  });

  it('returns all terminal sessions when no filters', () => {
    const result = applyHistoryFilters([terminated, failed], {});
    expect(result).toHaveLength(2);
  });

  it('returns empty array when no sessions', () => {
    expect(applyHistoryFilters([], {})).toHaveLength(0);
  });

  it('filters by ravnId', () => {
    const s1 = makeSession({ id: 'a', ravnId: 'r1' });
    const s2 = makeSession({ id: 'b', ravnId: 'r2' });
    const result = applyHistoryFilters([s1, s2], { ravnId: 'r2' });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('b');
  });

  it('passes sessions matching the ravnId filter', () => {
    const s1 = makeSession({ id: 'a', ravnId: 'r1' });
    const s2 = makeSession({ id: 'b', ravnId: 'r1' });
    const result = applyHistoryFilters([s1, s2], { ravnId: 'r1' });
    expect(result).toHaveLength(2);
  });

  it('filters by personaName', () => {
    const s1 = makeSession({ id: 'a', personaName: 'skald' });
    const s2 = makeSession({ id: 'b', personaName: 'bard' });
    const result = applyHistoryFilters([s1, s2], { personaName: 'bard' });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('b');
  });

  it('filters by sagaId', () => {
    const s1 = makeSession({ id: 'a', sagaId: 'saga-1' });
    const s2 = makeSession({ id: 'b', sagaId: 'saga-2' });
    const s3 = makeSession({ id: 'c' }); // no sagaId
    const result = applyHistoryFilters([s1, s2, s3], { sagaId: 'saga-2' });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('b');
  });

  it('filters by outcome (terminated)', () => {
    const t = makeSession({ id: 'a', state: 'terminated' });
    const f = makeSession({ id: 'b', state: 'failed' });
    const result = applyHistoryFilters([t, f], { outcome: 'terminated' });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('a');
  });

  it('filters by outcome (failed)', () => {
    const t = makeSession({ id: 'a', state: 'terminated' });
    const f = makeSession({ id: 'b', state: 'failed' });
    const result = applyHistoryFilters([t, f], { outcome: 'failed' });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('b');
  });

  it('filters by dateFrom — excludes sessions terminated before', () => {
    const early = makeSession({ id: 'a', terminatedAt: '2026-01-01T00:00:00Z' });
    const late = makeSession({ id: 'b', terminatedAt: '2026-03-01T00:00:00Z' });
    const result = applyHistoryFilters([early, late], {
      dateFrom: '2026-02-01T00:00:00Z',
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('b');
  });

  it('filters by dateTo — excludes sessions terminated after', () => {
    const early = makeSession({ id: 'a', terminatedAt: '2026-01-01T00:00:00Z' });
    const late = makeSession({ id: 'b', terminatedAt: '2026-03-01T00:00:00Z' });
    const result = applyHistoryFilters([early, late], {
      dateTo: '2026-02-01T00:00:00Z',
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('a');
  });

  it('combines multiple filters', () => {
    const match = makeSession({ id: 'match', ravnId: 'r1', personaName: 'skald', state: 'failed' });
    const wrongRavn = makeSession({
      id: 'wrong-ravn',
      ravnId: 'r2',
      personaName: 'skald',
      state: 'failed',
    });
    const wrongOutcome = makeSession({
      id: 'wrong-state',
      ravnId: 'r1',
      personaName: 'skald',
      state: 'terminated',
    });
    const result = applyHistoryFilters([match, wrongRavn, wrongOutcome], {
      ravnId: 'r1',
      outcome: 'failed',
    });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe('match');
  });

  it('does not exclude sessions with no terminatedAt when dateFrom is set', () => {
    const s = makeSession({ id: 'a', terminatedAt: undefined });
    const result = applyHistoryFilters([s], { dateFrom: '2026-01-01T00:00:00Z' });
    // No terminatedAt → not filtered out by date range
    expect(result).toHaveLength(1);
  });
});

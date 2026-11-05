import { describe, expect, it } from 'vitest';
import {
  CONTEXT_WARN_PERCENT,
  agentDuration,
  contextMeterData,
  formatDuration,
  isSessionWorking,
  sessionInfoRows,
  stateTone,
} from './sessionHeaderModel';

describe('contextMeterData', () => {
  it('turns tokens and window into a percentage', () => {
    expect(contextMeterData({ contextTokens: 62_000, window: 200_000 })).toEqual({
      percent: 31,
      used: 62_000,
      window: 200_000,
    });
  });

  it('stops the ring at full when the runtime reports more than the window', () => {
    expect(contextMeterData({ contextTokens: 250_000, window: 200_000 })?.percent).toBe(100);
  });

  it.each([
    ['no usage', null],
    ['no tokens', { window: 200_000 }],
    ['no window', { contextTokens: 10 }],
    ['a zero window', { contextTokens: 10, window: 0 }],
    ['negative tokens', { contextTokens: -1, window: 100 }],
    ['non-finite tokens', { contextTokens: Number.NaN, window: 100 }],
  ])('has no ring for %s', (_name, usage) => {
    expect(contextMeterData(usage)).toBeNull();
  });

  it('warns above the configured share of the window', () => {
    expect(CONTEXT_WARN_PERCENT).toBeGreaterThan(0);
    expect(CONTEXT_WARN_PERCENT).toBeLessThan(100);
  });
});

describe('isSessionWorking / stateTone', () => {
  it('works while thinking or running a tool', () => {
    expect(isSessionWorking('active')).toBe(true);
    expect(isSessionWorking('tool_executing')).toBe(true);
    for (const state of ['idle', 'awaiting_input', 'provisioning', 'stopped', 'error', null]) {
      expect(isSessionWorking(state as never)).toBe(false);
    }
    expect(isSessionWorking(undefined)).toBe(false);
  });

  it('colours the pill by lifecycle state', () => {
    expect(stateTone('running')).toBe('working');
    expect(stateTone('awaiting_input')).toBe('attention');
    expect(stateTone('failed')).toBe('critical');
    expect(stateTone('idle')).toBe('muted');
    expect(stateTone('archived')).toBe('muted');
  });
});

describe('formatDuration / agentDuration', () => {
  it('uses the largest units', () => {
    expect(formatDuration(-5)).toBe('0s');
    expect(formatDuration(42_000)).toBe('42s');
    expect(formatDuration(112_000)).toBe('1m');
    expect(formatDuration(72 * 60_000)).toBe('1h 12m');
    expect(formatDuration((2 * 24 + 3) * 3_600_000)).toBe('2d 3h');
  });

  it('measures a finished agent to its end and a running one to now', () => {
    const base = { id: 'a', kind: 'subagent', name: 'A', status: 'done' };
    expect(
      agentDuration(
        { ...base, startedAt: '2026-11-05T10:00:00Z', endedAt: '2026-11-05T10:01:52Z' },
        Date.parse('2026-11-05T12:00:00Z'),
      ),
    ).toBe('1m');
    expect(
      agentDuration(
        { ...base, status: 'running', startedAt: '2026-11-05T10:00:00Z' },
        Date.parse('2026-11-05T10:00:42Z'),
      ),
    ).toBe('42s');
  });

  it('has no duration without a usable start or end', () => {
    const base = { id: 'a', kind: 'subagent', name: 'A', status: 'done' };
    expect(agentDuration(base, 0)).toBeNull();
    expect(agentDuration({ ...base, startedAt: 'nonsense' }, 0)).toBeNull();
    expect(
      agentDuration({ ...base, startedAt: '2026-11-05T10:00:00Z', endedAt: 'nonsense' }, 0),
    ).toBeNull();
  });
});

describe('sessionInfoRows', () => {
  it('lists only the facts the session has', () => {
    expect(sessionInfoRows({})).toEqual([]);
    expect(
      sessionInfoRows({
        engine: { model: 'Claude Sonnet 4.6', harness: 'claude' },
        host: 'valhalla',
        workspace: 'niuulabs/niuu',
        project: 'Plan review',
        startedAt: '2026-11-05T10:00:00Z',
      }).map((row) => [row.id, row.label]),
    ).toEqual([
      ['engine', 'Engine'],
      ['host', 'Host'],
      ['workspace', 'Workspace'],
      ['project', 'Project'],
      ['started', 'Started'],
    ]);
  });

  it('names the harness beside the model, or nothing when it is unknown', () => {
    expect(sessionInfoRows({ engine: { model: 'm', harness: 'codex' } })[0]).toMatchObject({
      harness: 'codex',
      detail: 'Codex',
    });
    expect(sessionInfoRows({ engine: { model: 'm' } })[0]?.detail).toBeUndefined();
  });

  it('skips an unreadable start time and empty values', () => {
    expect(
      sessionInfoRows({ host: '', workspace: null, project: undefined, startedAt: 'nonsense' }),
    ).toEqual([]);
  });
});

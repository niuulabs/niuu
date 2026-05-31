import { describe, expect, it, vi } from 'vitest';
import type { Session } from '../domain/session';
import { formatTimestamp, formatTokens, tabCount, tabIcon, truncate } from './SessionDetailPage';

const sessionFixture: Session = {
  id: 'sess-1',
  ravnId: 's-1',
  name: 'session',
  personaName: 'session',
  templateId: 'git-default',
  state: 'running',
  clusterId: 'local',
  startedAt: '2026-05-31T10:00:00.000Z',
  resources: {
    cpuRequest: 1,
    cpuLimit: 2,
    cpuUsed: 0.5,
    memRequestMi: 1024,
    memLimitMi: 2048,
    memUsedMi: 512,
    gpuCount: 0,
  },
  env: {},
  files: { added: 1, modified: 2, deleted: 3 },
  events: [
    { ts: '2026-05-31T10:01:00.000Z', kind: 'message', body: 'started' },
    { ts: '2026-05-31T10:02:00.000Z', kind: 'file', body: 'patched' },
  ],
  tokensIn: 1200,
  tokensOut: 2500,
  costCents: 99,
};

describe('SessionDetailPage helpers', () => {
  it('maps tab icons and truncates text safely', () => {
    expect(tabIcon('chat')).toBe('▭');
    expect(tabIcon('terminal')).toBe('>_');
    expect(tabIcon('diffs')).toBe('⥮');
    expect(tabIcon('files')).toBe('◰');
    expect(tabIcon('chronicle')).toBe('≣');
    expect(tabIcon('logs')).toBe('⌨');

    expect(truncate(undefined, 8)).toBe('');
    expect(truncate('short', 8)).toBe('short');
    expect(truncate('truncate me', 8)).toBe('truncat…');
  });

  it('formats timestamps, token counts, and tab counts', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-05-31T12:00:00Z'));

    expect(formatTimestamp(Date.parse('2026-05-31T09:07:00-04:00'))).toBe('09:07');
    expect(formatTokens(999)).toBe('999');
    expect(formatTokens(1_200)).toBe('1.2k');
    expect(formatTokens(1_250_000)).toBe('1.3M');

    expect(tabCount('chat', sessionFixture)).toBe(2);
    expect(tabCount('diffs', sessionFixture)).toBe(6);
    expect(tabCount('chronicle', sessionFixture)).toBe(2);
    expect(tabCount('terminal', sessionFixture)).toBeUndefined();

    vi.useRealTimers();
  });
});

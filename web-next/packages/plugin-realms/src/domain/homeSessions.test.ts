import { describe, expect, it } from 'vitest';
import type { Session } from '@niuulabs/plugin-volundr';
import { continueSessions, isLive, needsYouSessions, sessionLabel } from './homeSessions';

function session(overrides: Partial<Session> & Pick<Session, 'id' | 'state'>): Session {
  return {
    ravnId: 'r',
    personaName: 'p',
    templateId: 't',
    clusterId: 'c',
    startedAt: '2026-09-01T00:00:00Z',
    resources: {
      cpuRequest: 0,
      cpuLimit: 0,
      cpuUsed: 0,
      memRequestMi: 0,
      memLimitMi: 0,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    ...overrides,
  } as Session;
}

const blocked = session({
  id: 'a',
  state: 'awaiting_input',
  lastActivityAt: '2026-09-02T10:00:00Z',
});
const alsoBlocked = session({
  id: 'b',
  state: 'awaiting_input',
  lastActivityAt: '2026-09-02T12:00:00Z',
});
const running = session({ id: 'c', state: 'running', lastActivityAt: '2026-09-01T08:00:00Z' });
const stopped = session({ id: 'd', state: 'terminated', lastActivityAt: '2026-09-03T08:00:00Z' });

describe('homeSessions', () => {
  it('knows which states are still on their feet', () => {
    expect(isLive(running)).toBe(true);
    expect(isLive(stopped)).toBe(false);
    expect(isLive(session({ id: 'f', state: 'provisioning' }))).toBe(true);
  });

  it('lists the sessions blocked on you, newest first', () => {
    expect(needsYouSessions([blocked, running, alsoBlocked]).map((s) => s.id)).toEqual(['b', 'a']);
    expect(needsYouSessions(undefined)).toEqual([]);
  });

  it('offers live sessions to continue before stopped ones, and never the blocked ones', () => {
    expect(continueSessions([stopped, running, blocked], 3).map((s) => s.id)).toEqual(['c', 'd']);
    expect(continueSessions([stopped, running], 1).map((s) => s.id)).toEqual(['c']);
    expect(continueSessions(undefined, 3)).toEqual([]);
  });

  it('names a session by title, then name, then id', () => {
    expect(sessionLabel(session({ id: 'x', state: 'idle', title: 'T', name: 'N' }))).toBe('T');
    expect(sessionLabel(session({ id: 'x', state: 'idle', name: 'N' }))).toBe('N');
    expect(sessionLabel(session({ id: 'x', state: 'idle' }))).toBe('x');
  });
});

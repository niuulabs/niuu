import { describe, it, expect } from 'vitest';
import { canTransition, transitionSession } from './session';
import type { Session, SessionState } from './session';

const BASE_SESSION: Session = {
  id: 's1',
  ravnId: 'r1',
  personaName: 'skald',
  templateId: 'tpl1',
  clusterId: 'c1',
  state: 'requested',
  startedAt: '2026-04-19T00:00:00Z',
  resources: {
    cpuRequest: 1,
    cpuLimit: 2,
    cpuUsed: 0,
    memRequestMi: 512,
    memLimitMi: 1024,
    memUsedMi: 0,
    gpuCount: 0,
  },
  env: {},
  events: [],
};

describe('canTransition — valid transitions', () => {
  it.each([
    ['requested', 'provisioning'],
    ['requested', 'failed'],
    ['provisioning', 'ready'],
    ['provisioning', 'failed'],
    ['ready', 'running'],
    ['ready', 'terminating'],
    ['ready', 'failed'],
    ['running', 'idle'],
    ['running', 'terminating'],
    ['running', 'failed'],
    ['idle', 'running'],
    ['idle', 'terminating'],
    ['idle', 'failed'],
    ['terminating', 'terminated'],
    ['terminating', 'failed'],
    ['failed', 'terminated'],
  ] as [SessionState, SessionState][])('allows %s → %s', (from, to) => {
    expect(canTransition(from, to)).toBe(true);
  });
});

describe('canTransition — illegal transitions', () => {
  it.each([
    ['requested', 'running'],
    ['requested', 'idle'],
    ['requested', 'terminated'],
    ['requested', 'ready'],
    ['provisioning', 'running'],
    ['provisioning', 'idle'],
    ['ready', 'provisioning'],
    ['ready', 'requested'],
    ['running', 'requested'],
    ['running', 'ready'],
    ['running', 'provisioning'],
    ['terminated', 'running'],
    ['terminated', 'failed'],
    ['terminated', 'provisioning'],
    ['terminated', 'requested'],
  ] as [SessionState, SessionState][])('rejects %s → %s', (from, to) => {
    expect(canTransition(from, to)).toBe(false);
  });
});

describe('transitionSession', () => {
  it('returns a new session object on a valid transition', () => {
    const next = transitionSession(BASE_SESSION, 'provisioning');
    expect(next.state).toBe('provisioning');
    expect(next.id).toBe('s1');
  });

  it('does not mutate the original session', () => {
    transitionSession(BASE_SESSION, 'provisioning');
    expect(BASE_SESSION.state).toBe('requested');
  });

  it('preserves all other fields on transition', () => {
    const next = transitionSession(BASE_SESSION, 'provisioning');
    expect(next.ravnId).toBe(BASE_SESSION.ravnId);
    expect(next.templateId).toBe(BASE_SESSION.templateId);
    expect(next.resources).toEqual(BASE_SESSION.resources);
  });

  it('throws on an illegal transition with a descriptive message', () => {
    expect(() => transitionSession(BASE_SESSION, 'running')).toThrow(
      'Invalid session state transition: requested → running',
    );
  });

  it('throws when transitioning from terminated', () => {
    const terminated = { ...BASE_SESSION, state: 'terminated' as SessionState };
    expect(() => transitionSession(terminated, 'running')).toThrow();
  });

  it('walks through the full happy-path lifecycle', () => {
    let s = BASE_SESSION;
    s = transitionSession(s, 'provisioning');
    expect(s.state).toBe('provisioning');
    s = transitionSession(s, 'ready');
    expect(s.state).toBe('ready');
    s = transitionSession(s, 'running');
    expect(s.state).toBe('running');
    s = transitionSession(s, 'idle');
    expect(s.state).toBe('idle');
    s = transitionSession(s, 'running');
    expect(s.state).toBe('running');
    s = transitionSession(s, 'terminating');
    expect(s.state).toBe('terminating');
    s = transitionSession(s, 'terminated');
    expect(s.state).toBe('terminated');
  });

  it('allows failure from every pre-terminal state', () => {
    const preTerminal: SessionState[] = [
      'requested',
      'provisioning',
      'ready',
      'running',
      'idle',
      'terminating',
    ];
    for (const state of preTerminal) {
      const s = { ...BASE_SESSION, state };
      expect(transitionSession(s, 'failed').state).toBe('failed');
    }
  });

  it('allows failed → terminated (clean-up complete)', () => {
    const failed = { ...BASE_SESSION, state: 'failed' as SessionState };
    expect(transitionSession(failed, 'terminated').state).toBe('terminated');
  });
});

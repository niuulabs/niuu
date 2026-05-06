import { describe, it, expect } from 'vitest';
import { groupByState, countByState, SESSION_STATES } from './groupByState';
import type { Session } from '../../domain/session';

function makeSession(id: string, state: Session['state']): Session {
  return {
    id,
    ravnId: `r-${id}`,
    personaName: 'test',
    templateId: 'tpl-default',
    clusterId: 'cl-1',
    state,
    startedAt: new Date().toISOString(),
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.5,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 256,
      gpuCount: 0,
    },
    env: {},
    events: [],
  };
}

describe('groupByState', () => {
  it('returns an object with all SessionState keys', () => {
    const result = groupByState([]);
    const keys = Object.keys(result);
    expect(keys).toContain('running');
    expect(keys).toContain('idle');
    expect(keys).toContain('provisioning');
    expect(keys).toContain('failed');
    expect(keys).toContain('terminated');
    expect(keys).toContain('requested');
    expect(keys).toContain('ready');
    expect(keys).toContain('terminating');
  });

  it('places each session into the correct bucket', () => {
    const sessions = [
      makeSession('s1', 'running'),
      makeSession('s2', 'idle'),
      makeSession('s3', 'running'),
      makeSession('s4', 'provisioning'),
      makeSession('s5', 'failed'),
      makeSession('s6', 'terminated'),
    ];

    const result = groupByState(sessions);

    expect(result.running).toHaveLength(2);
    expect(result.idle).toHaveLength(1);
    expect(result.provisioning).toHaveLength(1);
    expect(result.failed).toHaveLength(1);
    expect(result.terminated).toHaveLength(1);
    expect(result.requested).toHaveLength(0);
    expect(result.ready).toHaveLength(0);
    expect(result.terminating).toHaveLength(0);
  });

  it('places sessions by exact state', () => {
    const s = makeSession('x', 'idle');
    const result = groupByState([s]);
    expect(result.idle[0]).toEqual(s);
  });

  it('handles an empty list — all buckets empty', () => {
    const result = groupByState([]);
    for (const key of Object.keys(result) as Array<Session['state']>) {
      expect(result[key]).toHaveLength(0);
    }
  });
});

describe('countByState', () => {
  it('returns zero counts for empty groups', () => {
    const groups = groupByState([]);
    const counts = countByState(groups);
    for (const key of Object.keys(counts) as Array<Session['state']>) {
      expect(counts[key]).toBe(0);
    }
  });

  it('counts sessions correctly', () => {
    const sessions = [
      makeSession('a', 'running'),
      makeSession('b', 'running'),
      makeSession('c', 'failed'),
    ];
    const counts = countByState(groupByState(sessions));
    expect(counts.running).toBe(2);
    expect(counts.failed).toBe(1);
    expect(counts.idle).toBe(0);
  });
});

describe('SESSION_STATES', () => {
  it('contains the five subnav states in display order', () => {
    expect(SESSION_STATES).toEqual(['running', 'idle', 'provisioning', 'failed', 'terminated']);
  });
});

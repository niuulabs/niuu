import { describe, expect, it } from 'vitest';
import type { Session, SessionState } from '../../domain/session';
import { DONE_TODAY_WINDOW_MS, filterSessionsByQuery, groupForSimpleMode } from './sessionLabels';

const NOW = new Date('2026-05-02T12:00:00.000Z').getTime();

function makeSession(id: string, state: SessionState, activityMsAgo = 0): Session {
  return {
    id,
    ravnId: `ravn-${id}`,
    name: id,
    personaName: 'builder',
    templateId: 'tpl',
    clusterId: 'cluster-a',
    state,
    startedAt: new Date(NOW - activityMsAgo).toISOString(),
    lastActivityAt: new Date(NOW - activityMsAgo).toISOString(),
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.5,
      memRequestMi: 512,
      memLimitMi: 1024,
      memUsedMi: 256,
      gpuCount: 0,
    },
    env: {},
    events: [],
  };
}

describe('groupForSimpleMode', () => {
  it('sorts sessions into needs-you, running, done today, older and archived', () => {
    const groups = groupForSimpleMode(
      [
        makeSession('a', 'awaiting_input'),
        makeSession('b', 'running'),
        makeSession('c', 'provisioning'),
        makeSession('d', 'idle'),
        makeSession('e', 'terminated', 60_000),
        makeSession('f', 'failed', DONE_TODAY_WINDOW_MS + 60_000),
        makeSession('g', 'archived'),
      ],
      NOW,
    );

    expect(groups.needsYou.map((s) => s.id)).toEqual(['a']);
    expect(groups.running.map((s) => s.id)).toEqual(['b', 'c', 'd']);
    expect(groups.doneToday.map((s) => s.id)).toEqual(['e']);
    expect(groups.older.map((s) => s.id)).toEqual(['f']);
    expect(groups.archived.map((s) => s.id)).toEqual(['g']);
  });

  it('orders each group by most recent activity first', () => {
    const groups = groupForSimpleMode(
      [
        makeSession('old', 'running', 10 * 60_000),
        makeSession('fresh', 'running', 60_000),
        makeSession('middle', 'running', 5 * 60_000),
      ],
      NOW,
    );

    expect(groups.running.map((s) => s.id)).toEqual(['fresh', 'middle', 'old']);
  });

  it('keeps a finished session in done today right up to the 24h boundary', () => {
    const groups = groupForSimpleMode(
      [
        makeSession('inside', 'terminated', DONE_TODAY_WINDOW_MS - 1),
        makeSession('outside', 'terminated', DONE_TODAY_WINDOW_MS),
      ],
      NOW,
    );

    expect(groups.doneToday.map((s) => s.id)).toEqual(['inside']);
    expect(groups.older.map((s) => s.id)).toEqual(['outside']);
  });
});

describe('filterSessionsByQuery', () => {
  const sessions = [
    { ...makeSession('sess-alpha', 'running'), name: 'auth-fix', preview: 'github.com/n/volundr' },
    { ...makeSession('sess-beta', 'running'), name: 'docs', clusterName: 'gpu-forge' },
  ];

  it('returns every session for a blank query', () => {
    expect(filterSessionsByQuery(sessions, '   ')).toHaveLength(2);
  });

  it('matches on name, id, repo preview and forge name', () => {
    expect(filterSessionsByQuery(sessions, 'auth').map((s) => s.id)).toEqual(['sess-alpha']);
    expect(filterSessionsByQuery(sessions, 'BETA').map((s) => s.id)).toEqual(['sess-beta']);
    expect(filterSessionsByQuery(sessions, 'volundr').map((s) => s.id)).toEqual(['sess-alpha']);
    expect(filterSessionsByQuery(sessions, 'gpu-forge').map((s) => s.id)).toEqual(['sess-beta']);
    expect(filterSessionsByQuery(sessions, 'nothing')).toEqual([]);
  });
});

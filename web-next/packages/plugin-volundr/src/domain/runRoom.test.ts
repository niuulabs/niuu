import { describe, expect, it } from 'vitest';
import type { RoomParticipant } from '@niuulabs/ui';
import type { Session } from './session';
import { withRunMembers } from './runRoom';

function member(overrides: Partial<Session> & Pick<Session, 'id' | 'state'>): Session {
  return {
    name: overrides.id,
    ravnId: 'ravn',
    personaName: 'Worker',
    templateId: 'tpl',
    clusterId: 'valhalla',
    startedAt: '2026-11-08T23:53:00Z',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 1,
      memLimitMi: 2,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    runId: 'v588',
    coordination: {
      projectId: 'probe',
      role: 'reviewer',
      parent: { instanceId: 'valhalla', sessionId: 'coder' },
    },
    ...overrides,
  };
}

const HOST = { id: 'coder', runId: 'v588' };
const EMPTY = new Map<string, RoomParticipant>();

describe('withRunMembers', () => {
  it("puts a finished run's members in its room from Forge's record, stopped", () => {
    const room = withRunMembers(EMPTY, HOST, [
      member({ id: 'reviewer', name: 'wf-v588-reviewer', state: 'terminated' }),
    ]);

    expect(room.get('reviewer')).toEqual({
      peerId: 'reviewer',
      persona: 'reviewer',
      participantType: 'skuld',
      displayName: 'wf-v588-reviewer',
      status: 'stopped',
    });
  });

  it('keeps what the live room knows and its live status while the member runs', () => {
    const live = new Map<string, RoomParticipant>([
      [
        'reviewer',
        {
          peerId: 'reviewer',
          persona: 'reviewer',
          participantType: 'skuld',
          displayName: 'wf-v588-reviewer',
          status: 'thinking',
          subscribesTo: ['code.changed'],
          emits: ['review.passed'],
        },
      ],
    ]);

    const room = withRunMembers(live, HOST, [member({ id: 'reviewer', state: 'running' })]);

    expect(room.get('reviewer')).toMatchObject({
      status: 'thinking',
      subscribesTo: ['code.changed'],
      emits: ['review.passed'],
    });
  });

  it('says stopped once the member stopped, whatever the room last heard', () => {
    const live = new Map<string, RoomParticipant>([
      ['reviewer', { peerId: 'reviewer', persona: 'r', participantType: 'skuld', status: 'idle' }],
    ]);

    const room = withRunMembers(live, HOST, [member({ id: 'reviewer', state: 'archived' })]);

    expect(room.get('reviewer')?.status).toBe('stopped');
  });

  it('reads a member without a live room as running or idle from its session', () => {
    const room = withRunMembers(EMPTY, HOST, [
      member({ id: 'a', state: 'running' }),
      member({ id: 'b', state: 'idle' }),
      member({ id: 'c', state: 'failed' }),
    ]);

    expect([...room.values()].map((p) => [p.peerId, p.status])).toEqual([
      ['a', 'running'],
      ['b', 'idle'],
      ['c', 'error'],
    ]);
  });

  it('adds nobody for a session that is no run, or a nested session of another run', () => {
    expect(withRunMembers(EMPTY, { id: 'coder' }, [member({ id: 'r', state: 'idle' })])).toBe(
      EMPTY,
    );
    expect(
      withRunMembers(EMPTY, HOST, [member({ id: 'r', state: 'idle', runId: 'other-run' })]),
    ).toBe(EMPTY);
    expect(withRunMembers(EMPTY, null, [])).toBe(EMPTY);
  });
});

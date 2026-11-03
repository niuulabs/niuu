import { describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '@niuulabs/query';
import { buildRunLogHttpAdapter } from './runLogHttp';

function client(get: ApiClient['get'], post: ApiClient['post'] = vi.fn()): ApiClient {
  return { get, post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() } as unknown as ApiClient;
}

const entry = (seq: number, kind: string, payload: Record<string, unknown> = {}) => ({
  seq,
  kind,
  ts: `2026-11-02T23:0${seq}:00Z`,
  payload,
});

describe('buildRunLogHttpAdapter', () => {
  it("reads a run's outcomes from every page of its session log", async () => {
    const get = vi
      .fn()
      .mockResolvedValueOnce({
        entries: [
          entry(1, 'user'),
          entry(2, 'room_outcome', {
            eventType: 'plan.revised',
            participantId: 'flock-planner',
            participant: { participant_type: 'ravn' },
            fields: { plan: 'a\nb' },
          }),
        ],
        scanned_through: 2,
        has_more: true,
      })
      .mockResolvedValueOnce({
        entries: [
          entry(3, 'room_outcome', {
            eventType: 'plan.approved',
            participant: { peer_id: 'workflow-runtime', participant_type: 'workflow' },
          }),
        ],
        scanned_through: 3,
        has_more: false,
      });

    const outcomes = await buildRunLogHttpAdapter(client(get)).outcomes('s 1', 'valhalla');

    expect(get).toHaveBeenNthCalledWith(
      1,
      '/sessions/s%201/log/page?after=0&show_internal=true&instance_id=valhalla',
      { signal: undefined },
    );
    expect(get).toHaveBeenNthCalledWith(
      2,
      '/sessions/s%201/log/page?after=2&show_internal=true&instance_id=valhalla',
      { signal: undefined },
    );
    expect(outcomes).toEqual([
      {
        seq: 2,
        at: '2026-11-02T23:02:00Z',
        eventType: 'plan.revised',
        participantId: 'flock-planner',
        participantType: 'ravn',
        fields: { plan: 'a\nb' },
      },
      {
        seq: 3,
        at: '2026-11-02T23:03:00Z',
        eventType: 'plan.approved',
        participantId: 'workflow-runtime',
        participantType: 'workflow',
        fields: {},
      },
    ]);
  });

  it('reads a single-host log without naming an instance', async () => {
    const get = vi.fn().mockResolvedValue({ entries: [], scanned_through: 0, has_more: false });
    await buildRunLogHttpAdapter(client(get)).outcomes('s1', null);
    expect(get).toHaveBeenCalledWith('/sessions/s1/log/page?after=0&show_internal=true', {
      signal: undefined,
    });
  });

  it("lists each project's sessions on the host the project lives on", async () => {
    const get = vi.fn(async (path: string) => {
      if (path === '/projects') {
        return [
          { id: 'p1', name: 'Yggdrasil', instance_id: 'valhalla' },
          { id: 'p2', name: 'Local' },
        ];
      }
      if (path.includes('p1')) return [{ id: 's1' }, { id: 's2' }];
      return [];
    }) as unknown as ApiClient['get'];

    expect(await buildRunLogHttpAdapter(client(get)).projectSessions()).toEqual([
      { projectId: 'p1', name: 'Yggdrasil', sessionIds: ['s1', 's2'] },
      { projectId: 'p2', name: 'Local', sessionIds: [] },
    ]);
    expect(get).toHaveBeenCalledWith('/sessions?project_id=p1&instance_id=valhalla');
    expect(get).toHaveBeenCalledWith('/sessions?project_id=p2');
  });

  it("sends a message into a run's session", async () => {
    const post = vi.fn().mockResolvedValue({ status: 'delivered' });
    await buildRunLogHttpAdapter(client(vi.fn(), post)).sendMessage('s1', 'valhalla', 'Notes');
    expect(post).toHaveBeenCalledWith('/sessions/s1/messages?instance_id=valhalla', {
      content: 'Notes',
    });
  });
});

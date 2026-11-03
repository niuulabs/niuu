import { describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '@niuulabs/query';
import { buildRunLogHttpAdapter } from './runLogHttp';

function client(get: ApiClient['get'], post: ApiClient['post'] = vi.fn()): ApiClient {
  return { get, post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() } as unknown as ApiClient;
}

const row = (seq: number, overrides: Record<string, unknown> = {}) => ({
  seq,
  session_id: 's1',
  provider_key: 's1:plan',
  revision_id: `pf_${seq}`,
  title: 'plan.md',
  url: `/api/v1/forge/sessions/s1/files/presented/pf_${seq}`,
  at: `2026-11-02T23:0${seq}:00Z`,
  mime: 'text/markdown',
  version_label: `v${seq}`,
  source: 'present_file',
  ...overrides,
});

describe('buildRunLogHttpAdapter', () => {
  it('reads every page of the presented-file publications on one host', async () => {
    const get = vi
      .fn()
      .mockResolvedValueOnce({ items: [row(1)], next_after: 7, has_more: true })
      .mockResolvedValueOnce({ items: [row(2)], next_after: 9, has_more: false });

    const publications = await buildRunLogHttpAdapter(client(get)).publications('valhalla');

    expect(get).toHaveBeenNthCalledWith(
      1,
      '/artifact-publications?source=present_file&instance_id=valhalla&after=0',
      { signal: undefined },
    );
    expect(get).toHaveBeenNthCalledWith(
      2,
      '/artifact-publications?source=present_file&instance_id=valhalla&after=7',
      { signal: undefined },
    );
    expect(publications).toEqual([
      {
        seq: 1,
        sessionId: 's1',
        providerKey: 's1:plan',
        revisionId: 'pf_1',
        title: 'plan.md',
        url: '/api/v1/forge/sessions/s1/files/presented/pf_1',
        at: '2026-11-02T23:01:00Z',
        mime: 'text/markdown',
        versionLabel: 'v1',
      },
      expect.objectContaining({ seq: 2, revisionId: 'pf_2', versionLabel: 'v2' }),
    ]);
  });

  it("narrows to one session's publications on the default host", async () => {
    const get = vi.fn().mockResolvedValue({ items: [], next_after: 0, has_more: false });
    const signal = new AbortController().signal;
    await buildRunLogHttpAdapter(client(get)).publications(null, { sessionId: 's 1', signal });
    expect(get).toHaveBeenCalledWith(
      '/artifact-publications?source=present_file&session_id=s+1&after=0',
      { signal },
    );
  });

  it('reads the text of a presented file from its session', async () => {
    const get = vi.fn().mockResolvedValue('# Plan');
    const text = await buildRunLogHttpAdapter(client(get)).presentedText('s 1', 'valhalla', 'pf_1');
    expect(text).toBe('# Plan');
    expect(get).toHaveBeenCalledWith('/sessions/s%201/files/presented/pf_1?instance_id=valhalla', {
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

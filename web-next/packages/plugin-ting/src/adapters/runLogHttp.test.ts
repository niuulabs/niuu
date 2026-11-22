import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it, vi } from 'vitest';
import { ApiClientError, type ApiClient } from '@niuulabs/query';
import { buildRunLogHttpAdapter } from './runLogHttp';

function client(get: ApiClient['get'], post: ApiClient['post'] = vi.fn()): ApiClient {
  return { get, post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() } as unknown as ApiClient;
}

const row = (seq: number, overrides: Record<string, unknown> = {}) => ({
  seq,
  session_id: 's1',
  provider_key: 's1:plan',
  revision_id: `pf_${seq}`,
  tool_use_id: `pf_${seq}`,
  title: 'plan.md',
  url: `/api/v1/forge/sessions/s1/files/presented/pf_${seq}`,
  at: `2026-11-02T23:0${seq}:00Z`,
  mime: 'text/markdown',
  version_label: `v${seq}`,
  source: 'present_file',
  ...overrides,
});

/** Read by the Forge route tests too: a node must take exactly what is sent here. */
const NODE_REQUESTS = JSON.parse(
  readFileSync(
    resolve(__dirname, '../../../../../tests/fixtures/forge_node_requests.json'),
    'utf-8',
  ),
);

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
        fileId: 'pf_1',
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

  it('lists the Forge hosts from the registry, or the one Forge without it', async () => {
    const registryGet = vi.fn().mockResolvedValue([{ id: 'valhalla' }, { id: 'ymir' }]);
    const adapter = buildRunLogHttpAdapter(client(vi.fn()), client(registryGet));
    expect(await adapter.hosts()).toEqual(['valhalla', 'ymir']);
    expect(registryGet).toHaveBeenCalledWith('/instances?kind=volundr&enabledOnly=true');
    expect(await buildRunLogHttpAdapter(client(vi.fn())).hosts()).toEqual([null]);
  });

  it('covers only the node it talks to in node scope, under the name that node gives', async () => {
    const request = NODE_REQUESTS.read_artifacts;
    const get = vi.fn(async (endpoint: string) =>
      endpoint === '/guild'
        ? { instance_id: NODE_REQUESTS.instance.id }
        : { items: [row(1)], next_after: 1, has_more: false },
    );
    const registryGet = vi.fn();
    const adapter = buildRunLogHttpAdapter(
      client(get as unknown as ApiClient['get']),
      client(registryGet),
      { forgeScope: 'node' },
    );

    expect(adapter.scope).toBe('node');
    const hosts = await adapter.hosts();
    expect(hosts).toEqual([NODE_REQUESTS.instance.id]);
    expect(registryGet).not.toHaveBeenCalled();

    await adapter.publications(hosts[0]!);
    const sent = new URLSearchParams(String(get.mock.lastCall![0]).split('?')[1]);
    expect(Object.fromEntries(sent)).toEqual(request);
    expect(buildRunLogHttpAdapter(client(vi.fn())).scope).toBe('guild');
  });

  it('fails when the node cannot say which Forge it is', async () => {
    const get = vi.fn().mockRejectedValue(new ApiClientError('not joined to a Guild', 404));
    const adapter = buildRunLogHttpAdapter(client(get), null, { forgeScope: 'node' });
    await expect(adapter.hosts()).rejects.toThrow('not joined to a Guild');
  });

  it('reads the session that presented files, and knows when Forge no longer has it', async () => {
    const get = vi
      .fn()
      .mockResolvedValueOnce({ id: 's1', name: 'Artifact test', status: 'running' })
      .mockResolvedValueOnce({ id: 's1', name: 'Artifact test', status: 'stopped' })
      .mockRejectedValueOnce(new ApiClientError('API request failed: 404', 404))
      .mockRejectedValueOnce(new ApiClientError('API request failed: 502', 502));
    const adapter = buildRunLogHttpAdapter(client(get));

    expect(await adapter.session('s1', 'valhalla')).toEqual({
      sessionId: 's1',
      instanceId: 'valhalla',
      title: 'Artifact test',
      running: true,
    });
    expect(get).toHaveBeenCalledWith('/sessions/s1?instance_id=valhalla');
    expect((await adapter.session('s1', null))?.running).toBe(false);
    expect(await adapter.session('s1', null)).toBeNull();
    await expect(adapter.session('s1', null)).rejects.toThrow('502');
  });

  it('names the workflow run a member session works for, and its role', async () => {
    const get = vi.fn().mockResolvedValue({
      id: 's2',
      name: 'wf-n587-reviewer',
      status: 'running',
      tracker_issue_id: 'workflow:n587',
      coordination: { role: 'reviewer' },
    });

    expect(await buildRunLogHttpAdapter(client(get)).session('s2', 'noatun')).toEqual({
      sessionId: 's2',
      instanceId: 'noatun',
      title: 'wf-n587-reviewer',
      running: true,
      runSlug: 'n587',
      member: 'reviewer',
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

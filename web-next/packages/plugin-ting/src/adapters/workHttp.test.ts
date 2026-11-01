import { describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '@niuulabs/query';
import { buildWorkHttpAdapter } from './workHttp';

function client() {
  return { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
}

describe('Work HTTP read adapter', () => {
  it('preserves independent source coverage and the execution continuation', async () => {
    const http = client();
    const collection = {
      projects: [],
      campaigns: [],
      executions: [],
      executionNextCursor: 'next+/=',
      coverage: [
        {
          source: 'tracker',
          connectionId: 'private',
          status: 'unavailable',
          error: { code: 'source_unavailable', message: 'Reconnect this tracker.' },
        },
      ],
    };
    http.get.mockResolvedValue(collection);
    const service = buildWorkHttpAdapter(http);
    expect(await service.list()).toEqual(collection);
    expect(http.get).toHaveBeenCalledWith('/work');
    await service.list({ executionLimit: 50, executionCursor: collection.executionNextCursor });
    expect(http.get).toHaveBeenLastCalledWith(
      '/work?executionLimit=50&executionCursor=next%2B%2F%3D',
    );
  });

  it('qualifies resource identity and preserves absent operational runs', async () => {
    const http = client();
    const detail = {
      item: { id: 'project:uuid' },
      tasks: [{ operationalRun: null }],
      coverage: [],
    };
    http.get.mockResolvedValue(detail);
    expect(await buildWorkHttpAdapter(http).get('project', 'a/b')).toBe(detail);
    expect(http.get).toHaveBeenCalledWith('/work/project/a%2Fb');
  });

  it('propagates permission errors rather than presenting an empty collection', async () => {
    const http = client();
    const denied = new ApiClientError('Forbidden', 403);
    http.get.mockRejectedValue(denied);
    await expect(buildWorkHttpAdapter(http).list({ executionLimit: 25 })).rejects.toBe(denied);
    await expect(buildWorkHttpAdapter(http).get('campaign', 'id')).rejects.toBe(denied);
  });
});

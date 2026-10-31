import { describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '@niuulabs/query';
import { buildWorkHttpAdapter } from './workHttp';

function client() {
  return { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
}

describe('Work HTTP read adapter', () => {
  it('reads reviews across projects and for one ticket', async () => {
    const http = client();
    http.get.mockResolvedValue({ items: [] });
    const adapter = buildWorkHttpAdapter(http as never);

    await adapter.reviews();
    await adapter.ticketReviews('project/1', 'issue 1');

    expect(http.get).toHaveBeenCalledWith('/work/reviews');
    expect(http.get).toHaveBeenCalledWith('/work/project/project%2F1/tasks/issue%201/reviews');
  });

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

  it('posts a gate decision to the ticket it answers', async () => {
    const http = client();
    const decision = {
      gateId: 'gate-1',
      activationId: 'activation-1',
      decision: 'approve' as const,
      notes: '',
    };

    await buildWorkHttpAdapter(http).decideGate('saga/1', 'issue 1', decision);

    expect(http.post).toHaveBeenCalledWith(
      '/work/project/saga%2F1/tasks/issue%201/gate-decision',
      decision,
    );
  });

  it('starts a ticket through its coordinator', async () => {
    const http = client();

    await buildWorkHttpAdapter(http).startTicket('saga/1', 'issue 1');

    expect(http.post).toHaveBeenCalledWith('/work/project/saga%2F1/tasks/issue%201/start', {});
  });

  it('names the column a ticket was dropped on', async () => {
    const http = client();

    await buildWorkHttpAdapter(http).startTicket('saga-1', 'issue-1', 'planning');

    expect(http.post).toHaveBeenCalledWith('/work/project/saga-1/tasks/issue-1/start', {
      columnId: 'planning',
    });
  });

  it('propagates permission errors rather than presenting an empty collection', async () => {
    const http = client();
    const denied = new ApiClientError('Forbidden', 403);
    http.get.mockRejectedValue(denied);
    await expect(buildWorkHttpAdapter(http).list({ executionLimit: 25 })).rejects.toBe(denied);
    await expect(buildWorkHttpAdapter(http).get('campaign', 'id')).rejects.toBe(denied);
  });
});

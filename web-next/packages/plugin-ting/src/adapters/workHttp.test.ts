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
    await adapter.reviewWidget('acme-print', 'quality');
    expect(http.get).toHaveBeenCalledWith('/work/review-widgets/acme-print/quality');
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

    await buildWorkHttpAdapter(http).decideRunGate('a2a/1', decision);
    await buildWorkHttpAdapter(http).decideGate('saga/1', 'issue 1', decision);

    expect(http.post).toHaveBeenCalledWith(
      '/work/project/saga%2F1/tasks/issue%201/gate-decision',
      decision,
    );
    expect(http.post).toHaveBeenCalledWith('/work/runs/a2a%2F1/gate-decision', decision);
  });

  it('reads a run that has no ticket', async () => {
    const http = client();

    await buildWorkHttpAdapter(http).runReviews('a2a/1');

    expect(http.get).toHaveBeenCalledWith('/work/runs/a2a%2F1/reviews');
  });

  it('reads a run by its slug', async () => {
    const http = client();

    await buildWorkHttpAdapter(http).run('a2a/1');

    expect(http.get).toHaveBeenCalledWith('/work/runs/a2a%2F1');
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

  it("reads a run's graph and places it as the builder places a workflow", async () => {
    const http = client();
    http.get.mockResolvedValue({
      workflowName: 'Code, Review and Merge',
      graph: {
        nodes: [
          { id: 'go', kind: 'trigger', label: 'Requested', dispatchEvent: 'code.requested' },
          { id: 'code', kind: 'stage', label: 'Implement', position: { x: 300, y: 80 } },
        ],
        edges: [{ id: 'e1', source: 'go', target: 'code', label: 'code.requested' }],
      },
      nodes: [
        { nodeId: 'go', state: 'done', members: [] },
        {
          nodeId: 'code',
          state: 'active',
          members: [{ member: 'coder', state: 'active', verdict: null, sessionId: 's-1' }],
        },
      ],
    });
    const adapter = buildWorkHttpAdapter(http as never);

    const graph = await adapter.runGraph('run 1');

    expect(http.get).toHaveBeenCalledWith('/work/runs/run%201/graph');
    expect(graph.workflowName).toBe('Code, Review and Merge');
    // A node without a position is laid out; one with a position keeps it.
    expect(graph.nodes[0]!.position).toBeDefined();
    expect(graph.nodes[1]!.position).toEqual({ x: 300, y: 80 });
    expect(graph.edges[0]!.cp1).toBeDefined();
    expect(graph.states.get('code')?.members[0]?.sessionId).toBe('s-1');
  });
});

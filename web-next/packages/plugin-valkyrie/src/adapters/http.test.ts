import { describe, expect, it, vi } from 'vitest';
import { buildOdinReviewHttpAdapter, buildValkyrieHttpAdapter } from './http';

function makeClient() {
  return {
    basePath: '/api/v1/ravn/odin',
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
}

function rawItem(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    item_id: 'review:evolution_build:abc',
    kind: 'evolution_build',
    requested_action: 'install',
    environment_id: 'cluster-a',
    valkyrie_id: 'valkyrie:k8s-a',
    title: 'probe',
    summary: 'a probe',
    status: 'pending',
    risk_class: 'low',
    urgency: 0.6,
    evidence: { artifact: { content: 'skill', tool_code: 'def run(s): ...' } },
    requested_at: '2026-06-03T13:00:00Z',
    ...overrides,
  };
}

describe('buildValkyrieHttpAdapter', () => {
  it('fetches the dashboard and posts autonomy changes', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ valkyries: [] });
    client.post.mockResolvedValue({ valkyries: [] });
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.getDashboard();
    await adapter.updateAutonomy({
      valkyrieId: 'valkyrie:k8s-a',
      mode: 'yolo',
      reason: 'ship it',
      participantId: 'human:jozef',
    });

    expect(client.get).toHaveBeenCalledWith('/dashboard');
    expect(client.post).toHaveBeenCalledWith('/autonomy', {
      valkyrieId: 'valkyrie:k8s-a',
      mode: 'yolo',
      reason: 'ship it',
      participantId: 'human:jozef',
    });
  });
});

describe('buildOdinReviewHttpAdapter', () => {
  it('lists reviews with filters and normalizes the payload', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawItem()]);
    const adapter = buildOdinReviewHttpAdapter(client);

    const items = await adapter.listReviews({ status: 'pending', kind: 'evolution_build' });

    expect(client.get).toHaveBeenCalledWith('/reviews?status=pending&kind=evolution_build');
    expect(items[0]?.itemId).toBe('review:evolution_build:abc');
    expect(items[0]?.kind).toBe('evolution_build');
    expect(items[0]?.evidence.artifact).toEqual({
      content: 'skill',
      tool_code: 'def run(s): ...',
    });
  });

  it('lists without a query string when no filters are set', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);
    const adapter = buildOdinReviewHttpAdapter(client);

    await adapter.listReviews();

    expect(client.get).toHaveBeenCalledWith('/reviews');
  });

  it('fetches one review and returns null on failure', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce(rawItem());
    const adapter = buildOdinReviewHttpAdapter(client);

    const item = await adapter.getReview('review:evolution_build:abc');
    expect(client.get).toHaveBeenCalledWith('/reviews/review%3Aevolution_build%3Aabc');
    expect(item?.title).toBe('probe');

    client.get.mockRejectedValueOnce(new Error('404'));
    expect(await adapter.getReview('review:none')).toBeNull();
  });

  it('posts decisions and unwraps the decided item', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ item: rawItem({ status: 'approved' }) });
    const adapter = buildOdinReviewHttpAdapter(client);

    const decided = await adapter.decideReview({
      itemId: 'review:evolution_build:abc',
      decision: 'approved',
      reason: 'looks safe',
      participantId: 'human:jozef',
    });

    expect(client.post).toHaveBeenCalledWith('/reviews/review%3Aevolution_build%3Aabc/decide', {
      decision: 'approved',
      reason: 'looks safe',
      participantId: 'human:jozef',
    });
    expect(decided.status).toBe('approved');
  });

  it('fetches the summary', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ pendingTotal: 2 });
    const adapter = buildOdinReviewHttpAdapter(client);

    await adapter.getSummary();
    expect(client.get).toHaveBeenCalledWith('/reviews/summary');
  });
});

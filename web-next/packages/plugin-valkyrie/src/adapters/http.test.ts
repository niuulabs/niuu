import { describe, expect, it, vi } from 'vitest';
import { buildValkyrieHttpAdapter, buildValkyrieSignalSseStream } from './http';

function makeClient() {
  return {
    basePath: '/api/v1/valkyrie',
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
}

function mockSseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

describe('buildValkyrieHttpAdapter', () => {
  it('calls dashboard and huddle endpoints', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ environments: [] });
    client.post.mockResolvedValue({ id: 'huddle-1', joined: true });
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.getDashboard();
    await adapter.joinHuddle('huddle-1');

    expect(client.get).toHaveBeenCalledWith('/dashboard');
    expect(client.post).toHaveBeenCalledWith('/huddles/huddle-1/join', {});
  });

  it('URL encodes learning and huddle ids', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({});
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.adoptLearning({ learningId: 'learn a/b', reason: 'test' });
    await adapter.promoteLearning({
      learningId: 'learn a/b',
      reason: 'test',
      targetScope: 'flock',
    });
    await adapter.demoteLearning({
      learningId: 'learn a/b',
      reason: 'test',
      targetScope: 'domain',
    });
    await adapter.rollbackLearning({ learningId: 'learn a/b', reason: 'test' });
    await adapter.sendHuddleMessage({ huddleId: 'huddle a/b', body: 'hi' });

    expect(client.post).toHaveBeenCalledWith('/learnings/learn%20a%2Fb/adopt', {
      learningId: 'learn a/b',
      reason: 'test',
    });
    expect(client.post).toHaveBeenCalledWith('/learnings/learn%20a%2Fb/promote', {
      learningId: 'learn a/b',
      reason: 'test',
      targetScope: 'flock',
    });
    expect(client.post).toHaveBeenCalledWith('/learnings/learn%20a%2Fb/demote', {
      learningId: 'learn a/b',
      reason: 'test',
      targetScope: 'domain',
    });
    expect(client.post).toHaveBeenCalledWith('/learnings/learn%20a%2Fb/rollback', {
      learningId: 'learn a/b',
      reason: 'test',
    });
    expect(client.post).toHaveBeenCalledWith('/huddles/huddle%20a%2Fb/messages', {
      huddleId: 'huddle a/b',
      body: 'hi',
    });
  });
});

describe('buildValkyrieSignalSseStream', () => {
  it('normalizes SSE messages through openEventStream', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        mockSseResponse([
          'data: {"id":"event-1","summary":"OOM canary","severity":"critical"}\n\n',
        ]),
      );
    vi.stubGlobal('fetch', fetchMock);
    const stream = buildValkyrieSignalSseStream('/signals');
    const received: unknown[] = [];

    const unsubscribe = stream.subscribe((event) => received.push(event));
    await new Promise((resolve) => setTimeout(resolve, 0));
    unsubscribe();

    expect(fetchMock).toHaveBeenCalledWith(
      '/signals',
      expect.objectContaining({
        headers: expect.any(Object),
      }),
    );
    expect(received).toEqual([
      expect.objectContaining({
        id: 'event-1',
        summary: 'OOM canary',
        severity: 'critical',
      }),
    ]);
  });
});

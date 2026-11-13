import { describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '@niuulabs/query';
import { buildMemoryGatewayHttpAdapter, createMemoryGatewayMockAdapter } from './memoryGateway';

function client(answers: Record<string, unknown>): ApiClient & { calls: unknown[][] } {
  const calls: unknown[][] = [];
  return {
    calls,
    get: vi.fn(async (endpoint: string) => {
      calls.push(['GET', endpoint]);
      return answers[endpoint];
    }),
    post: vi.fn(async (endpoint: string, body?: unknown) => {
      calls.push(['POST', endpoint, body]);
      return answers[endpoint];
    }),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  } as unknown as ApiClient & { calls: unknown[][] };
}

describe('buildMemoryGatewayHttpAdapter', () => {
  it('asks over the people route and maps the answer', async () => {
    const api = client({
      '/memory/ask': {
        answer: 'a',
        gaps: [],
        sources: [
          { layer: 'org', layer_name: 'the org', ref: 'r', kind: 'page', title: 't', url: null },
        ],
      },
    });
    const gateway = buildMemoryGatewayHttpAdapter(api);
    const answer = await gateway.ask('q', 'p-1');
    await gateway.ask('q');
    expect(answer.sources[0]?.layerName).toBe('the org');
    expect(api.calls).toEqual([
      ['POST', '/memory/ask', { question: 'q', project_id: 'p-1' }],
      ['POST', '/memory/ask', { question: 'q' }],
    ]);
  });

  it('reviews, keeps and discards', async () => {
    const raw = {
      id: 'p-1',
      layer: 'project:x',
      layer_name: 'X',
      title: 't',
      now: null,
      would: 'w',
      why: 'y',
      by: 'agent',
      created_at: '2026-11-11T09:00:00Z',
      sources: [],
    };
    const api = client({
      '/memory/review': { proposals: [raw], more: [] },
      '/memory/review?layer=project%3Ax': { proposals: [], more: ['project:x'] },
    });
    const gateway = buildMemoryGatewayHttpAdapter(api);
    const all = await gateway.review();
    const one = await gateway.review('project:x');
    expect(all.proposals[0]).toMatchObject({ layerName: 'X', createdAt: raw.created_at });
    expect(one.more).toEqual(['project:x']);
    await gateway.keep({ id: 'p-1', layer: 'project:x' });
    await gateway.discard({ id: 'p-1', layer: 'project:x' }, 'no');
    expect(api.calls.slice(2)).toEqual([
      ['POST', '/memory/proposals/p-1/approve', { layer: 'project:x' }],
      ['POST', '/memory/proposals/p-1/reject', { layer: 'project:x', reason: 'no' }],
    ]);
  });
});

describe('createMemoryGatewayMockAdapter', () => {
  it('answers, filters by layer and forgets what was decided', async () => {
    const gateway = createMemoryGatewayMockAdapter();
    await expect(gateway.ask(' ')).rejects.toThrow('question is required');
    expect((await gateway.review('org')).proposals).toEqual([]);
    await gateway.keep({ id: 'p-1', layer: 'project:booking' });
    await gateway.discard({ id: 'p-2', layer: 'project:booking' }, 'r');
    expect((await gateway.review()).proposals).toEqual([]);
  });
});

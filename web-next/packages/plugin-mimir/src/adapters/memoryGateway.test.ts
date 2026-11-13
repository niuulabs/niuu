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
  it('searches over the people route and maps every hit', async () => {
    const raw = {
      query: 'cut-over',
      hits: [
        {
          layer: 'project:p-1',
          layer_name: 'Booking platform',
          kind: 'page',
          ref: 'projects/booking-cutover',
          title: 'Booking cut-over',
          snippet: 'The cut-over is on 12 December.',
        },
        {
          layer: 'org',
          layer_name: 'the org',
          kind: 'source',
          ref: 'source:src_1',
          title: 'Thread',
          snippet: 'Ines decides.',
        },
      ],
    };
    const api = client({
      '/memory/search?q=cut-over': raw,
      '/memory/search?q=cut+over&project_id=p-1': { ...raw, hits: [] },
    });
    const gateway = buildMemoryGatewayHttpAdapter(api);
    const all = await gateway.search('cut-over');
    const one = await gateway.search('cut over', { projectId: 'p-1' });
    expect(one.hits).toEqual([]);
    expect(all.query).toBe('cut-over');
    expect(all.hits[0]).toEqual({
      layer: 'project:p-1',
      layerName: 'Booking platform',
      kind: 'page',
      ref: 'projects/booking-cutover',
      title: 'Booking cut-over',
      snippet: 'The cut-over is on 12 December.',
    });
    expect(all.hits[1]?.layerName).toBe('the org');
    expect(api.calls).toEqual([
      ['GET', '/memory/search?q=cut-over'],
      ['GET', '/memory/search?q=cut+over&project_id=p-1'],
    ]);
  });

  it('lets a failed search reach the caller', async () => {
    const api = client({});
    (api.get as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('502 bad gateway'));
    const gateway = buildMemoryGatewayHttpAdapter(api);
    await expect(gateway.search('x')).rejects.toThrow('502 bad gateway');
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
  it('searches fixed records, narrows to a project, and refuses an empty query', async () => {
    const gateway = createMemoryGatewayMockAdapter();
    await expect(gateway.search(' ')).rejects.toThrow('query is required');
    const all = await gateway.search('CUT-OVER');
    expect(all.hits.map((hit) => hit.kind)).toEqual(['page', 'source']);
    const one = await gateway.search('cut-over', { projectId: 'booking' });
    expect(one.hits.map((hit) => hit.layer)).toEqual(['project:booking']);
    expect((await gateway.search('zebra')).hits).toEqual([]);
  });

  it('filters reviews by layer and forgets what was decided', async () => {
    const gateway = createMemoryGatewayMockAdapter();
    expect((await gateway.review('org')).proposals).toEqual([]);
    await gateway.keep({ id: 'p-1', layer: 'project:booking' });
    await gateway.discard({ id: 'p-2', layer: 'project:booking' }, 'r');
    expect((await gateway.review()).proposals).toEqual([]);
  });
});

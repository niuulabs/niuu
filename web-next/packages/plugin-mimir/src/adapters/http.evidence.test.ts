import { describe, it, expect, vi } from 'vitest';
import { buildMimirHttpAdapter } from './http';

function makeClient(overrides: Record<string, ReturnType<typeof vi.fn>> = {}) {
  return {
    get: vi.fn().mockResolvedValue([]),
    post: vi.fn().mockResolvedValue({}),
    put: vi.fn().mockResolvedValue(undefined),
    patch: vi.fn(),
    delete: vi.fn(),
    ...overrides,
  };
}

function httpError(status: number) {
  return Object.assign(new Error(`http ${status}`), { status });
}

const RAW_EVIDENCE = {
  fact: 'Raw SQL with asyncpg — no ORM',
  proof_count: 4,
  trend: 'stable',
  latest_support: '2026-03-20',
  supporting_dates: ['2026-01-10', '2026-03-20'],
  source_proof_count: 2,
};

describe('pages.getEvidence', () => {
  it('calls GET /evidence with the encoded path', async () => {
    const client = makeClient();
    await buildMimirHttpAdapter(client).pages.getEvidence('technical/session store.md');
    expect(client.get).toHaveBeenCalledWith('/evidence?path=technical%2Fsession%20store.md');
  });

  it('maps snake_case proof rows to the domain shape', async () => {
    const client = makeClient({ get: vi.fn().mockResolvedValue([RAW_EVIDENCE]) });
    const rows = await buildMimirHttpAdapter(client).pages.getEvidence('api/overview.md');
    expect(rows[0]).toEqual({
      fact: 'Raw SQL with asyncpg — no ORM',
      proofCount: 4,
      trend: 'stable',
      latestSupport: '2026-03-20',
      supportingDates: ['2026-01-10', '2026-03-20'],
      sourceProofCount: 2,
    });
  });

  it('defaults the optional proof fields', async () => {
    const client = makeClient({
      get: vi.fn().mockResolvedValue([{ fact: 'a', proof_count: 0, trend: 'new' }]),
    });
    const rows = await buildMimirHttpAdapter(client).pages.getEvidence('a.md');
    expect(rows[0]).toMatchObject({
      latestSupport: null,
      supportingDates: [],
      sourceProofCount: 0,
    });
  });

  it.each([404, 501])(
    'treats %i as "this instance keeps no evidence for that page"',
    async (status) => {
      const client = makeClient({ get: vi.fn().mockRejectedValue(httpError(status)) });
      const rows = await buildMimirHttpAdapter(client).pages.getEvidence('elsewhere.md');
      expect(rows).toEqual([]);
    },
  );

  it('raises on a real failure', async () => {
    const client = makeClient({ get: vi.fn().mockRejectedValue(httpError(503)) });
    await expect(buildMimirHttpAdapter(client).pages.getEvidence('a.md')).rejects.toThrow(
      'http 503',
    );
  });
});

describe('pages.getRelated', () => {
  it('defaults to one hop and sends no rel filter', async () => {
    const client = makeClient();
    await buildMimirHttpAdapter(client).pages.getRelated('arch/overview.md');
    expect(client.get).toHaveBeenCalledWith('/related?path=arch%2Foverview.md&depth=1');
  });

  it('passes depth and rel when given', async () => {
    const client = makeClient();
    await buildMimirHttpAdapter(client).pages.getRelated('arch/overview.md', 3, 'documents');
    expect(client.get).toHaveBeenCalledWith(
      '/related?path=arch%2Foverview.md&depth=3&rel=documents',
    );
  });

  it('maps rows and normalises the direction', async () => {
    const client = makeClient({
      get: vi.fn().mockResolvedValue([
        { path: 'api/overview.md', hop: 1, rel: 'documents', direction: 'out' },
        { path: 'infra/k8s.md', hop: 2, direction: 'in' },
      ]),
    });
    const rows = await buildMimirHttpAdapter(client).pages.getRelated('arch/overview.md', 2);
    expect(rows).toEqual([
      { path: 'api/overview.md', hop: 1, rel: 'documents', direction: 'out' },
      { path: 'infra/k8s.md', hop: 2, rel: null, direction: 'in' },
    ]);
  });

  it('treats a store without a link graph as an empty neighbourhood', async () => {
    const client = makeClient({ get: vi.fn().mockRejectedValue(httpError(501)) });
    const rows = await buildMimirHttpAdapter(client).pages.getRelated('a.md');
    expect(rows).toEqual([]);
  });

  it('raises on a real failure', async () => {
    const client = makeClient({ get: vi.fn().mockRejectedValue(httpError(500)) });
    await expect(buildMimirHttpAdapter(client).pages.getRelated('a.md')).rejects.toThrow(
      'http 500',
    );
  });
});

describe('pages.revisePage', () => {
  const rawPage = {
    path: 'api/overview.md',
    title: 'API Design Guidelines',
    summary: 'Standards.',
    category: 'api',
    updated_at: '2026-09-14T10:00:00Z',
    source_ids: [],
    content: '',
    related: [],
    zones: [{ kind: 'key-facts', items: ['New wording'] }],
  };

  it('posts the snake_case revision body', async () => {
    const client = makeClient({ post: vi.fn().mockResolvedValue(rawPage) });
    await buildMimirHttpAdapter(client).pages.revisePage({
      path: 'api/overview.md',
      oldFact: 'Old wording',
      newFact: 'New wording',
      attribution: 'you',
    });
    expect(client.post).toHaveBeenCalledWith('/page/revise', {
      path: 'api/overview.md',
      old_fact: 'Old wording',
      new_fact: 'New wording',
      attribution: 'you',
    });
  });

  it('returns the revised page', async () => {
    const client = makeClient({ post: vi.fn().mockResolvedValue(rawPage) });
    const page = await buildMimirHttpAdapter(client).pages.revisePage({
      path: 'api/overview.md',
      oldFact: 'Old wording',
      newFact: 'New wording',
      attribution: 'you',
    });
    expect(page.zones).toEqual([{ kind: 'key-facts', items: ['New wording'] }]);
  });

  it('raises when the write is refused', async () => {
    const client = makeClient({ post: vi.fn().mockRejectedValue(httpError(403)) });
    await expect(
      buildMimirHttpAdapter(client).pages.revisePage({
        path: 'api/overview.md',
        oldFact: 'a',
        newFact: 'b',
        attribution: 'you',
      }),
    ).rejects.toThrow('http 403');
  });
});

import { describe, it, expect } from 'vitest';
import { createMimirMockAdapter } from './mock';
import { getZoneByKind } from '../domain/page';

const ARCH = '/arch/overview';
const WEAK_FACT = 'Six cognitive regions (Sköll, Hati, Sága, Móði, Váli, Víðarr)';

describe('mock pages.getEvidence', () => {
  it('returns one row per Key Fact of a seeded page', async () => {
    const svc = createMimirMockAdapter();
    const page = await svc.pages.getPage(ARCH);
    const facts = getZoneByKind(page?.zones ?? [], 'key-facts')?.items ?? [];
    const rows = await svc.pages.getEvidence(ARCH);
    expect(rows.map((row) => row.fact)).toEqual(facts);
  });

  it('carries a weakening fact so the "worth a look" list has content', async () => {
    const svc = createMimirMockAdapter();
    const rows = await svc.pages.getEvidence(ARCH);
    expect(rows.find((row) => row.fact === WEAK_FACT)?.trend).toBe('weakening');
  });

  it('returns nothing for a page the instance does not keep', async () => {
    const svc = createMimirMockAdapter();
    expect(await svc.pages.getEvidence('/nope')).toEqual([]);
  });
});

describe('mock pages.getRelated', () => {
  it('walks one hop in both directions', async () => {
    const svc = createMimirMockAdapter();
    const rows = await svc.pages.getRelated(ARCH);
    expect(rows.map((row) => row.path)).toContain('/api/overview');
    expect(rows.every((row) => row.hop === 1)).toBe(true);
    expect(rows.some((row) => row.direction === 'in')).toBe(true);
  });

  it('labels typed edges', async () => {
    const svc = createMimirMockAdapter();
    const rows = await svc.pages.getRelated(ARCH);
    expect(rows.find((row) => row.path === '/api/overview')?.rel).toBe('documents');
  });

  it('reaches further with a larger depth', async () => {
    const svc = createMimirMockAdapter();
    const one = await svc.pages.getRelated(ARCH, 1);
    const two = await svc.pages.getRelated(ARCH, 2);
    expect(two.length).toBeGreaterThan(one.length);
    expect(two.some((row) => row.hop === 2)).toBe(true);
  });

  it('filters to one relationship', async () => {
    const svc = createMimirMockAdapter();
    const rows = await svc.pages.getRelated(ARCH, 1, 'documents');
    expect(rows.map((row) => row.path)).toEqual(['/api/overview']);
  });
});

describe('mock pages.revisePage', () => {
  it('rewrites the fact and appends the transition to the timeline', async () => {
    const svc = createMimirMockAdapter();
    const revised = await svc.pages.revisePage({
      path: ARCH,
      oldFact: WEAK_FACT,
      newFact: 'Six regions, and Víðarr calibrates the rest',
      attribution: 'you',
    });
    const facts = getZoneByKind(revised.zones ?? [], 'key-facts')?.items ?? [];
    expect(facts).toContain('Six regions, and Víðarr calibrates the rest');
    expect(facts).not.toContain(WEAK_FACT);
    const timeline = getZoneByKind(revised.zones ?? [], 'timeline')?.items ?? [];
    expect(timeline.at(-1)?.note).toContain('belief revised');
    expect(timeline.at(-1)?.source).toBe('you');
  });

  it('keeps the revision on later reads', async () => {
    const svc = createMimirMockAdapter();
    await svc.pages.revisePage({
      path: ARCH,
      oldFact: WEAK_FACT,
      newFact: 'Six regions, one of them meta-cognitive',
      attribution: 'you',
    });
    const page = await svc.pages.getPage(ARCH);
    expect(getZoneByKind(page?.zones ?? [], 'key-facts')?.items).toContain(
      'Six regions, one of them meta-cognitive',
    );
  });

  it('moves the evidence row off the weak trend', async () => {
    const svc = createMimirMockAdapter();
    await svc.pages.revisePage({
      path: ARCH,
      oldFact: WEAK_FACT,
      newFact: 'Six regions, checked again today',
      attribution: 'you',
    });
    const rows = await svc.pages.getEvidence(ARCH);
    const row = rows.find((entry) => entry.fact === 'Six regions, checked again today');
    expect(row?.trend).toBe('stable');
    expect(row?.proofCount).toBe(3);
  });

  it('raises when the page has no such fact', async () => {
    const svc = createMimirMockAdapter();
    await expect(
      svc.pages.revisePage({
        path: ARCH,
        oldFact: 'never written',
        newFact: 'x',
        attribution: 'you',
      }),
    ).rejects.toThrow(/Fact not found/);
  });

  it('raises for an unknown page', async () => {
    const svc = createMimirMockAdapter();
    await expect(
      svc.pages.revisePage({ path: '/nope', oldFact: 'a', newFact: 'b', attribution: 'you' }),
    ).rejects.toThrow(/Page not found/);
  });
});

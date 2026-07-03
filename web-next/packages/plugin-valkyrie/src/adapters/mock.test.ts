import { describe, expect, it } from 'vitest';
import {
  createMockOdinReviewService,
  createMockRealmGovernanceService,
  createMockValkyrieService,
  createSeedRealms,
  createSeedReviewItems,
  createSeedToolWorkflows,
  createSeedTrustGrants,
  createSeedValkyrieDashboard,
} from './mock';

describe('createMockValkyrieService', () => {
  it('returns the seeded dashboard', async () => {
    const service = createMockValkyrieService();
    const dashboard = await service.getDashboard();
    expect(dashboard.valkyries.length).toBeGreaterThan(0);
    expect(dashboard.environments.length).toBeGreaterThan(0);
  });

  it('applies autonomy updates to the targeted valkyrie', async () => {
    const seed = createSeedValkyrieDashboard();
    const service = createMockValkyrieService(seed);
    const target = seed.valkyries[0]!;

    const dashboard = await service.updateAutonomy({
      valkyrieId: target.id,
      mode: 'yolo',
      reason: 'test',
    });

    const updated = dashboard.valkyries.find((entry) => entry.id === target.id);
    expect(updated?.autonomyMode).toBe('yolo');
  });
});

describe('createMockOdinReviewService', () => {
  it('lists newest-first with status and kind filters', async () => {
    const service = createMockOdinReviewService();
    const all = await service.listReviews();
    expect(all.length).toBe(createSeedReviewItems().length);
    const sorted = [...all].map((item) => item.requestedAt);
    expect(sorted).toEqual([...sorted].sort((a, b) => b.localeCompare(a)));

    const pending = await service.listReviews({ status: 'pending' });
    expect(pending.every((item) => item.status === 'pending')).toBe(true);

    const builds = await service.listReviews({ kind: 'evolution_build' });
    expect(builds.every((item) => item.kind === 'evolution_build')).toBe(true);

    const limited = await service.listReviews({ limit: 1 });
    expect(limited).toHaveLength(1);
  });

  it('returns one item by id and null for unknown ids', async () => {
    const service = createMockOdinReviewService();
    const seed = createSeedReviewItems()[0]!;
    expect((await service.getReview(seed.itemId))?.itemId).toBe(seed.itemId);
    expect(await service.getReview('review:none')).toBeNull();
  });

  it('approves pending items and settles them', async () => {
    const service = createMockOdinReviewService();
    const pending = (await service.listReviews({ status: 'pending' }))[0]!;

    const decided = await service.decideReview({
      itemId: pending.itemId,
      decision: 'approved',
      reason: 'fine',
      participantId: 'human:jozef',
    });

    expect(decided.status).toBe('applied');
    expect(decided.decidedBy).toBe('human:jozef');
    await expect(
      service.decideReview({ itemId: pending.itemId, decision: 'approved' }),
    ).rejects.toThrow(/already/);
  });

  it('requires a reason to reject', async () => {
    const service = createMockOdinReviewService();
    const pending = (await service.listReviews({ status: 'pending' }))[0]!;

    await expect(
      service.decideReview({ itemId: pending.itemId, decision: 'rejected' }),
    ).rejects.toThrow(/reason/i);

    const rejected = await service.decideReview({
      itemId: pending.itemId,
      decision: 'rejected',
      reason: 'not needed',
    });
    expect(rejected.status).toBe('rejected');
  });

  it('summarises pending items by kind and risk', async () => {
    const service = createMockOdinReviewService();
    const summary = await service.getSummary();
    expect(summary.pendingTotal).toBeGreaterThan(0);
    expect(Object.values(summary.pendingByKind).reduce((a, b) => a + b, 0)).toBe(
      summary.pendingTotal,
    );
    expect(summary.countsByStatus.pending).toBe(summary.pendingTotal);
  });
});

describe('createMockRealmGovernanceService', () => {
  it('serves the seeded realms, grants, and workflows', async () => {
    const service = createMockRealmGovernanceService();

    const realms = await service.listRealms();
    expect(realms).toEqual(createSeedRealms());

    const grants = await service.listTrustGrants('asgard');
    expect(grants).toEqual(createSeedTrustGrants()['asgard']);

    const workflows = await service.listWorkflows();
    expect(workflows).toEqual(createSeedToolWorkflows());
  });

  it('creates a trust grant that later listings include', async () => {
    const service = createMockRealmGovernanceService();

    const created = await service.createTrustGrant('midgard', {
      action_class: 'build',
      target: '*',
      level: 4,
      limits: { workflow: 'valkyrie-tool-forge-fast' },
      granted_by: 'human:operator',
    });

    expect(created).toMatchObject({
      realm_id: 'realm-midgard',
      action_class: 'build',
      level: 4,
      limits: { workflow: 'valkyrie-tool-forge-fast' },
      granted_by: 'human:operator',
    });

    const grants = await service.listTrustGrants('midgard');
    expect(grants).toHaveLength(1);
    expect(grants[0]?.id).toBe(created.id);
  });

  it('defaults granted_by to null and rejects unknown realms', async () => {
    const service = createMockRealmGovernanceService();

    const created = await service.createTrustGrant('midgard', {
      action_class: 'build',
      target: '*',
      level: 1,
      limits: {},
    });
    expect(created.granted_by).toBeNull();

    await expect(service.listTrustGrants('nowhere')).rejects.toThrow('Realm not found: nowhere');
    await expect(
      service.createTrustGrant('nowhere', {
        action_class: 'build',
        target: '*',
        level: 0,
        limits: {},
      }),
    ).rejects.toThrow('Realm not found: nowhere');
  });
});

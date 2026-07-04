import { describe, expect, it } from 'vitest';
import {
  createMockOdinReviewService,
  createMockRealmGovernanceService,
  createMockValkyrieService,
  createMockValkyrieSkillsService,
  createSeedLearnedSkills,
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

describe('createMockValkyrieService learnings', () => {
  it('serves one learning by id and null for unknown ids', async () => {
    const service = createMockValkyrieService();
    const learning = await service.getLearning('learn-k8s-oom-canary');
    expect(learning?.title).toBe('OOMKilled with rising queue depth');
    expect(learning?.repetition).toBe(3);
    expect(await service.getLearning('learn-ghost')).toBeNull();
  });

  it('records feedback on the learning and surfaces it on the dashboard', async () => {
    const service = createMockValkyrieService();

    const updated = await service.sendLearningFeedback({
      learningId: 'learn-email-vendor-escalation',
      verdict: 'useful',
      reason: 'Caught the contract email',
      operatorId: 'human:operator',
    });

    expect(updated.feedback).toMatchObject({
      verdict: 'useful',
      reason: 'Caught the contract email',
      operatorId: 'human:operator',
    });
    const dashboard = await service.getDashboard();
    const onDashboard = dashboard.learnings.find(
      (entry) => entry.id === 'learn-email-vendor-escalation',
    );
    expect(onDashboard?.feedback?.verdict).toBe('useful');
  });

  it('rejects unknown verdicts with the backend detail string', async () => {
    const service = createMockValkyrieService();
    await expect(
      service.sendLearningFeedback({
        learningId: 'learn-email-vendor-escalation',
        verdict: 'amazing' as never,
        operatorId: 'human:operator',
      }),
    ).rejects.toThrow('Unsupported feedback verdict');
  });

  it('requires a targetScope for wrong_tier and applies it when given', async () => {
    const service = createMockValkyrieService();
    await expect(
      service.sendLearningFeedback({
        learningId: 'learn-k8s-oom-canary',
        verdict: 'wrong_tier',
        operatorId: 'human:operator',
      }),
    ).rejects.toThrow('targetScope is required for wrong_tier feedback');

    const updated = await service.sendLearningFeedback({
      learningId: 'learn-k8s-oom-canary',
      verdict: 'wrong_tier',
      targetScope: 'environment',
      operatorId: 'human:operator',
    });
    expect(updated.feedback?.verdict).toBe('wrong_tier');
    expect(updated.targetScope).toBe('environment');
  });

  it('rejects feedback and revisions for unknown learnings', async () => {
    const service = createMockValkyrieService();
    await expect(
      service.sendLearningFeedback({
        learningId: 'learn-ghost',
        verdict: 'useful',
        operatorId: 'human:operator',
      }),
    ).rejects.toThrow('Learning learn-ghost not found');
    await expect(
      service.reviseLearning({
        learningId: 'learn-ghost',
        summary: 'x',
        reason: 'y',
        operatorId: 'human:operator',
      }),
    ).rejects.toThrow('Learning learn-ghost not found');
  });

  it('revises a candidate in place with an empty supersededId', async () => {
    const service = createMockValkyrieService();

    const result = await service.reviseLearning({
      learningId: 'learn-email-vendor-escalation',
      summary: 'Flag contract-deadline emails from unknown senders for review.',
      reason: 'Tightened the sender condition',
      operatorId: 'human:operator',
    });

    expect(result.supersededId).toBe('');
    expect(result.learning.id).toBe('learn-email-vendor-escalation');
    expect(result.learning.summary).toContain('unknown senders');
    expect(result.learning.status).toBe('candidate');
    expect(result.learning.history?.at(-1)).toMatchObject({
      eventType: 'learning.revised',
      operatorId: 'human:operator',
      reason: 'Tightened the sender condition',
    });
    const dashboard = await service.getDashboard();
    expect(
      dashboard.learnings.find((entry) => entry.id === 'learn-email-vendor-escalation')?.summary,
    ).toContain('unknown senders');
  });

  it('revising an installed learning creates a superseding candidate', async () => {
    const service = createMockValkyrieService();

    const result = await service.reviseLearning({
      learningId: 'learn-k8s-oom-canary',
      title: 'OOMKilled with rising queue depth (tightened)',
      reason: 'Narrow the queue-depth window',
      operatorId: 'human:operator',
    });

    expect(result.supersededId).toBe('learn-k8s-oom-canary');
    expect(result.learning.id).toBe('learn-k8s-oom-canary:rev1');
    expect(result.learning.status).toBe('candidate');
    expect(result.learning.supersedes).toBe('learn-k8s-oom-canary');
    expect(result.learning.feedback).toBeNull();

    // The original stays installed; the candidate joins the dashboard.
    const dashboard = await service.getDashboard();
    const original = dashboard.learnings.find((entry) => entry.id === 'learn-k8s-oom-canary');
    expect(original?.status).toBe('canary');
    expect(
      dashboard.learnings.find((entry) => entry.id === 'learn-k8s-oom-canary:rev1'),
    ).toBeDefined();

    // A second revision counts up, never reusing rev1.
    const again = await service.reviseLearning({
      learningId: 'learn-k8s-oom-canary',
      summary: 'Second pass',
      reason: 'More evidence',
      operatorId: 'human:operator',
    });
    expect(again.learning.id).toBe('learn-k8s-oom-canary:rev2');
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

    const grants = await service.listTrustGrants('valhalla');
    expect(grants).toEqual(createSeedTrustGrants()['valhalla']);

    const workflows = await service.listWorkflows();
    expect(workflows).toEqual(createSeedToolWorkflows());
  });

  it('creates a trust grant that later listings include', async () => {
    const service = createMockRealmGovernanceService();

    const created = await service.createTrustGrant('host-jozef', {
      action_class: 'build',
      target: '*',
      level: 4,
      limits: { workflow: 'valkyrie-tool-forge-fast' },
      granted_by: 'human:operator',
    });

    expect(created).toMatchObject({
      realm_id: 'realm-host-jozef',
      action_class: 'build',
      level: 4,
      limits: { workflow: 'valkyrie-tool-forge-fast' },
      granted_by: 'human:operator',
    });

    const grants = await service.listTrustGrants('host-jozef');
    expect(grants).toHaveLength(1);
    expect(grants[0]?.id).toBe(created.id);
  });

  it('defaults granted_by to null and rejects unknown realms', async () => {
    const service = createMockRealmGovernanceService();

    const created = await service.createTrustGrant('host-jozef', {
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

describe('createMockValkyrieSkillsService', () => {
  it('lists newest-adopted-first summaries without the code payload', async () => {
    const service = createMockValkyrieSkillsService();

    const skills = await service.listSkills('env-k8s-valhalla');

    expect(skills.map((skill) => skill.skillName)).toEqual([
      'k8s_memory_pressure_probe',
      'registry_token_refresh_check',
    ]);
    expect(skills[0]).not.toHaveProperty('toolCode');
    expect(skills[0]?.hasCode).toBe(true);
  });

  it('filters the list by environment', async () => {
    const service = createMockValkyrieSkillsService();
    expect(await service.listSkills('env-elsewhere')).toHaveLength(0);
    expect(await service.listSkills('')).toHaveLength(createSeedLearnedSkills().length);
  });

  it('serves the full record for one skill and null for unknown names', async () => {
    const service = createMockValkyrieSkillsService();

    const skill = await service.getSkill('env-k8s-valhalla', 'k8s_memory_pressure_probe');
    expect(skill?.toolCode).toContain('def run(signal: dict)');
    expect(skill?.testCode).toContain('def test_matches_oomkilled_pod');
    expect(skill?.requirements).toEqual(['kubernetes>=29.0.0']);
    expect(skill?.manifest).toMatchObject({ safety_class: 'read_only' });

    expect(await service.getSkill('env-k8s-valhalla', 'ghost_probe')).toBeNull();
    expect(await service.getSkill('env-elsewhere', 'k8s_memory_pressure_probe')).toBeNull();
  });
});

import { describe, expect, it, vi } from 'vitest';
import {
  buildOdinReviewHttpAdapter,
  buildRealmGovernanceHttpAdapter,
  buildValkyrieHttpAdapter,
  buildValkyrieSkillsHttpAdapter,
} from './http';

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

  it('lists decisions with filters', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ items: [], total: 0, limit: 8, offset: 0 });
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.listDecisions({ environmentId: 'env-a', valkyrieId: 'v-1', limit: 8 });
    expect(client.get).toHaveBeenCalledWith(
      '/decisions?environment_id=env-a&valkyrie_id=v-1&limit=8',
    );

    await adapter.listDecisions();
    expect(client.get).toHaveBeenCalledWith('/decisions');
  });

  it('fetches one decision and returns null on failure', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({ decision: { decisionId: 'd-1' }, lineage: {} });
    const adapter = buildValkyrieHttpAdapter(client);

    const detail = await adapter.getDecision('d-1');
    expect(client.get).toHaveBeenCalledWith('/decisions/d-1');
    expect(detail?.decision.decisionId).toBe('d-1');

    client.get.mockRejectedValueOnce(new Error('404'));
    expect(await adapter.getDecision('missing')).toBeNull();
  });

  it('lists signal history and skill stats', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({ items: [], total: 0, limit: 10, offset: 0 });
    client.get.mockResolvedValueOnce({ skills: [{ skillName: 'probe' }] });
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.listSignalHistory({ environmentId: 'env-a', severity: 'warning', offset: 10 });
    expect(client.get).toHaveBeenCalledWith(
      '/signals/history?environment_id=env-a&severity=warning&offset=10',
    );

    const skills = await adapter.getSkillStats('env-a');
    expect(client.get).toHaveBeenCalledWith('/learnings/stats/skills?environment_id=env-a');
    expect(skills).toEqual([{ skillName: 'probe' }]);
  });

  it('fetches one learning and returns null on failure (404)', async () => {
    const client = makeClient();
    client.get.mockResolvedValueOnce({ id: 'learn-1', title: 'probe pattern' });
    const adapter = buildValkyrieHttpAdapter(client);

    const learning = await adapter.getLearning('learn-1');
    expect(client.get).toHaveBeenCalledWith('/learnings/learn-1');
    expect(learning?.title).toBe('probe pattern');

    client.get.mockRejectedValueOnce(new Error('404'));
    expect(await adapter.getLearning('learn:missing')).toBeNull();
  });

  it('posts feedback without a targetScope for plain verdicts', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ id: 'learn-1', feedback: { verdict: 'useful' } });
    const adapter = buildValkyrieHttpAdapter(client);

    const updated = await adapter.sendLearningFeedback({
      learningId: 'learn-1',
      verdict: 'useful',
      operatorId: 'human:operator',
    });

    expect(client.post).toHaveBeenCalledWith('/learnings/learn-1/feedback', {
      verdict: 'useful',
      reason: '',
      operatorId: 'human:operator',
    });
    expect(updated.feedback?.verdict).toBe('useful');
  });

  it('posts wrong_tier feedback with the target scope and encodes the id', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ id: 'learn:a b' });
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.sendLearningFeedback({
      learningId: 'learn:a b',
      verdict: 'wrong_tier',
      reason: 'belongs on the environment tier',
      targetScope: 'environment',
      operatorId: 'human:operator',
    });

    expect(client.post).toHaveBeenCalledWith('/learnings/learn%3Aa%20b/feedback', {
      verdict: 'wrong_tier',
      reason: 'belongs on the environment tier',
      operatorId: 'human:operator',
      targetScope: 'environment',
    });
  });

  it('posts revisions with only the provided content fields', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      learning: { id: 'learn-1:rev1', supersedes: 'learn-1' },
      supersededId: 'learn-1',
    });
    const adapter = buildValkyrieHttpAdapter(client);

    const result = await adapter.reviseLearning({
      learningId: 'learn-1',
      summary: 'tightened summary',
      reason: 'narrowed the trigger',
      operatorId: 'human:operator',
    });

    expect(client.post).toHaveBeenCalledWith('/learnings/learn-1/revise', {
      summary: 'tightened summary',
      reason: 'narrowed the trigger',
      operatorId: 'human:operator',
    });
    expect(result.supersededId).toBe('learn-1');
    expect(result.learning.id).toBe('learn-1:rev1');
  });

  it('posts all three content fields when a full edit is submitted', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ learning: { id: 'learn-1' }, supersededId: '' });
    const adapter = buildValkyrieHttpAdapter(client);

    await adapter.reviseLearning({
      learningId: 'learn-1',
      title: 't',
      summary: 's',
      content: 'c',
      reason: 'r',
      operatorId: 'human:operator',
    });

    expect(client.post).toHaveBeenCalledWith('/learnings/learn-1/revise', {
      title: 't',
      summary: 's',
      content: 'c',
      reason: 'r',
      operatorId: 'human:operator',
    });
  });
});

describe('buildValkyrieSkillsHttpAdapter', () => {
  it('lists learned skills for an environment and unwraps items', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      items: [{ skillName: 'oom_probe', environmentId: 'env-a', hasCode: true }],
      total: 1,
    });
    const adapter = buildValkyrieSkillsHttpAdapter(client);

    const skills = await adapter.listSkills('env-a');

    expect(client.get).toHaveBeenCalledWith('/skills?environment_id=env-a');
    expect(skills).toEqual([{ skillName: 'oom_probe', environmentId: 'env-a', hasCode: true }]);
  });

  it('fetches one skill with the environment pinned in the query', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      skillName: 'oom probe',
      toolCode: 'def run(signal): ...',
      requirements: ['kubernetes>=29.0.0'],
    });
    const adapter = buildValkyrieSkillsHttpAdapter(client);

    const skill = await adapter.getSkill('env-a', 'oom probe');

    expect(client.get).toHaveBeenCalledWith('/skills/oom%20probe?environment_id=env-a');
    expect(skill?.toolCode).toBe('def run(signal): ...');
  });

  it('returns null when the skill is unknown (404)', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('404 Not Found'));
    const adapter = buildValkyrieSkillsHttpAdapter(client);

    expect(await adapter.getSkill('env-a', 'ghost')).toBeNull();
  });
});

describe('buildOdinReviewHttpAdapter', () => {
  it('lists reviews with filters and normalizes the payload', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawItem()]);
    const adapter = buildOdinReviewHttpAdapter(client);

    const items = await adapter.listReviews({
      status: 'pending',
      kind: 'evolution_build',
      environmentId: 'env-a',
      riskClass: 'high',
      query: 'restart',
      limit: 21,
      offset: 20,
    });

    expect(client.get).toHaveBeenCalledWith(
      '/reviews?status=pending&kind=evolution_build&environment_id=env-a&risk_class=high&q=restart&limit=21&offset=20',
    );
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

    await adapter.getSummary({ environmentId: 'env-a', riskClass: 'high', query: 'restart' });
    expect(client.get).toHaveBeenCalledWith(
      '/reviews/summary?environment_id=env-a&risk_class=high&q=restart',
    );
  });
});

describe('buildRealmGovernanceHttpAdapter', () => {
  it('lists realms and trust grants from the shared realms API', async () => {
    const realmsClient = makeClient();
    const workflowsClient = makeClient();
    realmsClient.get.mockResolvedValue([]);
    const adapter = buildRealmGovernanceHttpAdapter(realmsClient, workflowsClient);

    await adapter.listRealms();
    expect(realmsClient.get).toHaveBeenCalledWith('/realms');

    await adapter.listTrustGrants('asgard');
    expect(realmsClient.get).toHaveBeenCalledWith('/realms/asgard/trust-grants');
    expect(workflowsClient.get).not.toHaveBeenCalled();
  });

  it('posts the trust grant body untouched', async () => {
    const realmsClient = makeClient();
    const workflowsClient = makeClient();
    realmsClient.post.mockResolvedValue({ id: 'grant-1' });
    const adapter = buildRealmGovernanceHttpAdapter(realmsClient, workflowsClient);

    await adapter.createTrustGrant('realm/with slash', {
      action_class: 'build',
      target: '*',
      level: 3,
      limits: { workflow: 'valkyrie-tool-forge' },
      granted_by: 'human:operator',
    });

    expect(realmsClient.post).toHaveBeenCalledWith('/realms/realm%2Fwith%20slash/trust-grants', {
      action_class: 'build',
      target: '*',
      level: 3,
      limits: { workflow: 'valkyrie-tool-forge' },
      granted_by: 'human:operator',
    });
  });

  it('lists workflows from the Ting client', async () => {
    const realmsClient = makeClient();
    const workflowsClient = makeClient();
    workflowsClient.get.mockResolvedValue([{ id: 'wf-1', tags: ['tool-builder'] }]);
    const adapter = buildRealmGovernanceHttpAdapter(realmsClient, workflowsClient);

    const workflows = await adapter.listWorkflows();
    expect(workflowsClient.get).toHaveBeenCalledWith('/workflows');
    expect(realmsClient.get).not.toHaveBeenCalled();
    expect(workflows).toEqual([{ id: 'wf-1', tags: ['tool-builder'] }]);
  });
});

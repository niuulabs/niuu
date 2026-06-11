import { describe, expect, it } from 'vitest';
import {
  normalizeReviewItem,
  normalizeValkyrieSignalEvent,
  reviewArtifactEvidence,
  reviewEffectStatement,
  reviewPolicyFindings,
} from './domain';

describe('normalizeValkyrieSignalEvent', () => {
  it('normalizes a signal event from wire data', () => {
    expect(
      normalizeValkyrieSignalEvent({
        id: 'event-1',
        type: 'learning',
        environmentId: 'env-k8s-valhalla',
        flockId: 'flock-k8s',
        message: 'Peer learning entered canary',
        severity: 'warning',
        timestamp: '2026-06-03T14:00:00Z',
      }),
    ).toEqual({
      id: 'event-1',
      type: 'learning',
      environmentId: 'env-k8s-valhalla',
      flockId: 'flock-k8s',
      summary: 'Peer learning entered canary',
      severity: 'warning',
      timestamp: '2026-06-03T14:00:00Z',
    });
  });

  it('rejects empty payloads without a summary', () => {
    expect(normalizeValkyrieSignalEvent(null)).toBeNull();
    expect(normalizeValkyrieSignalEvent({ id: 'event-2' })).toBeNull();
  });

  it('defaults unknown type and severity safely', () => {
    expect(
      normalizeValkyrieSignalEvent({
        type: 'unknown',
        severity: 'loud',
        summary: 'fallback event',
      }),
    ).toMatchObject({
      type: 'signal',
      severity: 'info',
      summary: 'fallback event',
    });
  });

  it.each(['judgment', 'action', 'learning', 'huddle'] as const)(
    'preserves %s event type',
    (type) => {
      expect(
        normalizeValkyrieSignalEvent({
          type,
          summary: `${type} event`,
          receivedAt: '2026-06-03T14:01:00Z',
        }),
      ).toMatchObject({
        id: `2026-06-03T14:01:00Z:${type} event`,
        type,
        timestamp: '2026-06-03T14:01:00Z',
      });
    },
  );

  it.each(['notice', 'critical'] as const)('preserves %s severity', (severity) => {
    expect(
      normalizeValkyrieSignalEvent({
        severity,
        summary: `${severity} event`,
      }),
    ).toMatchObject({
      severity,
      timestamp: new Date(0).toISOString(),
    });
  });
});

describe('normalizeReviewItem', () => {
  it('round-trips a backend payload to camelCase', () => {
    const item = normalizeReviewItem({
      item_id: 'review:evolution_build:abc',
      kind: 'evolution_build',
      requested_action: 'install',
      environment_id: 'cluster-a',
      valkyrie_id: 'valkyrie:k8s-a',
      title: 'probe',
      summary: 'a probe',
      audience: 'valkyrie',
      risk_class: 'high',
      safety_class: 'read_only',
      urgency: 0.7,
      requested_capability: 'approve',
      evidence: { artifact: { content: 'skill md', tool_code: 'code' } },
      status: 'pending',
      requested_at: '2026-06-03T13:00:00Z',
    });
    expect(item.itemId).toBe('review:evolution_build:abc');
    expect(item.riskClass).toBe('high');
    expect(item.urgency).toBe(0.7);
    expect(item.status).toBe('pending');
  });

  it('defaults unknown enums and missing fields safely', () => {
    const item = normalizeReviewItem({ item_id: 'x', kind: 'mystery', status: 'odd' });
    expect(item.kind).toBe('flock_learning');
    expect(item.status).toBe('pending');
    expect(item.riskClass).toBe('low');
    expect(item.title).toBe('x');
    expect(item.evidence).toEqual({});
  });
});

describe('review evidence helpers', () => {
  const base = normalizeReviewItem({
    item_id: 'review:x',
    kind: 'evolution_build',
    requested_action: 'install',
    environment_id: 'cluster-a',
    valkyrie_id: 'valkyrie:k8s-a',
    title: 'probe',
    flock_id: 'flock:k8s',
    evidence: {
      artifact: { content: 'md', tool_code: 'py', canary_sample: { kind: 'Pod' } },
      review: { findings: ['policy: hold', 42] },
    },
  });

  it('extracts artifact content, tool code, and canary sample', () => {
    const artifact = reviewArtifactEvidence(base);
    expect(artifact.skillContent).toBe('md');
    expect(artifact.toolCode).toBe('py');
    expect(artifact.canarySample).toEqual({ kind: 'Pod' });
  });

  it('keeps only string findings', () => {
    expect(reviewPolicyFindings(base)).toEqual(['policy: hold']);
  });

  it('states the exact effect of approving per kind', () => {
    expect(reviewEffectStatement(base)).toContain('canary the tool in a sandbox');
    expect(reviewEffectStatement(base)).toContain('propose it to flock:k8s');
    expect(
      reviewEffectStatement(normalizeReviewItem({ ...basePayload(), kind: 'skill_promotion' })),
    ).toContain('promote');
    expect(
      reviewEffectStatement(normalizeReviewItem({ ...basePayload(), kind: 'court_escalation' })),
    ).toContain('operator authority');
    expect(
      reviewEffectStatement(normalizeReviewItem({ ...basePayload(), kind: 'autonomy_change' })),
    ).toContain('autonomy');
    expect(
      reviewEffectStatement(normalizeReviewItem({ ...basePayload(), kind: 'flock_learning' })),
    ).toContain('every relevant resident');
  });
});

function basePayload(): Record<string, unknown> {
  return {
    item_id: 'review:x',
    kind: 'evolution_build',
    environment_id: 'cluster-a',
    valkyrie_id: 'valkyrie:k8s-a',
    title: 'probe',
  };
}

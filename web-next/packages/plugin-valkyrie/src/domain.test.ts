import { describe, expect, it } from 'vitest';
import {
  autonomyModeForLevel,
  decisionSkillName,
  grantWorkflowName,
  isToolBuilderWorkflow,
  latestBuildGrant,
  normalizeReviewItem,
  normalizeValkyrieSignalEvent,
  realmSlugForEnvironment,
  referencedSkillName,
  reviewArtifactEvidence,
  reviewEffectStatement,
  reviewPolicyFindings,
  type RealmTrustGrant,
  type TingWorkflowSummary,
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

describe('realmSlugForEnvironment', () => {
  it.each([
    ['env-k8s-valhalla', 'valhalla'],
    ['env-host-jozef', 'host-jozef'],
    ['env-printer-forge', 'printer-forge'],
  ] as const)('strips the canonical prefix: %s → %s', (environmentId, slug) => {
    expect(realmSlugForEnvironment(environmentId)).toBe(slug);
  });

  it('prefers the longer env-k8s- prefix over the bare env- prefix', () => {
    expect(realmSlugForEnvironment('env-k8s-ymir')).not.toBe('k8s-ymir');
    expect(realmSlugForEnvironment('env-k8s-ymir')).toBe('ymir');
  });

  it('returns ids without a canonical prefix unchanged', () => {
    expect(realmSlugForEnvironment('valhalla')).toBe('valhalla');
    expect(realmSlugForEnvironment('')).toBe('');
  });
});

describe('autonomyModeForLevel', () => {
  it.each([
    [0, 'guarded'],
    [1, 'guarded'],
    [2, 'autonomous'],
    [3, 'autonomous'],
    [4, 'yolo'],
    [5, 'yolo'],
  ] as const)('maps level %d to %s', (level, mode) => {
    expect(autonomyModeForLevel(level)).toBe(mode);
  });
});

function grant(overrides: Partial<RealmTrustGrant> = {}): RealmTrustGrant {
  return {
    id: 'grant-1',
    realm_id: 'realm-asgard',
    action_class: 'build',
    target: '*',
    level: 2,
    limits: { workflow: 'valkyrie-tool-forge' },
    granted_by: 'human:operator',
    granted_at: '2026-06-02T10:00:00Z',
    ...overrides,
  };
}

describe('latestBuildGrant', () => {
  it('returns the most recent build grant and ignores other action classes', () => {
    const older = grant({ id: 'grant-old', granted_at: '2026-05-01T00:00:00Z', level: 1 });
    const newer = grant({ id: 'grant-new', granted_at: '2026-06-01T00:00:00Z', level: 3 });
    const deploy = grant({
      id: 'grant-deploy',
      action_class: 'deploy',
      granted_at: '2026-06-30T00:00:00Z',
    });

    expect(latestBuildGrant([older, deploy, newer])?.id).toBe('grant-new');
  });

  it('returns null when no build grant exists', () => {
    expect(latestBuildGrant([])).toBeNull();
    expect(latestBuildGrant([grant({ action_class: 'deploy' })])).toBeNull();
  });
});

describe('grantWorkflowName', () => {
  it('reads limits.workflow when it is a string', () => {
    expect(grantWorkflowName(grant())).toBe('valkyrie-tool-forge');
  });

  it('returns empty for missing grants or non-string limits', () => {
    expect(grantWorkflowName(null)).toBe('');
    expect(grantWorkflowName(grant({ limits: {} }))).toBe('');
    expect(grantWorkflowName(grant({ limits: { workflow: 7 } }))).toBe('');
  });
});

describe('isToolBuilderWorkflow', () => {
  it('detects the tool-builder tag', () => {
    const workflow: TingWorkflowSummary = {
      id: 'wf-1',
      name: 'forge',
      description: '',
      version: '1',
      tags: ['tool-builder'],
    };
    expect(isToolBuilderWorkflow(workflow)).toBe(true);
    expect(isToolBuilderWorkflow({ ...workflow, tags: ['release'] })).toBe(false);
  });
});

describe('decisionSkillName', () => {
  it('reads the first string skill_name from the evidence entries', () => {
    expect(
      decisionSkillName({
        evidence: [{ capability_name: 'inspect' }, { skill_name: 'oom_probe' }],
      }),
    ).toBe('oom_probe');
  });

  it('ignores empty or non-string skill_name values', () => {
    expect(decisionSkillName({ evidence: [] })).toBe('');
    expect(decisionSkillName({ evidence: [{ skill_name: '' }, { skill_name: 7 }] })).toBe('');
  });
});

describe('referencedSkillName', () => {
  it('prefers the explicit evidence skill_name even when unknown to the list', () => {
    expect(
      referencedSkillName(
        { evidence: [{ skill_name: 'ghost_probe' }], summary: '', rationale: '' },
        ['oom_probe'],
      ),
    ).toBe('ghost_probe');
  });

  it('falls back to a known skill name mentioned in the summary or rationale', () => {
    const decision = {
      evidence: [],
      summary: "handled a signal with learned skill 'oom_probe' — no action needed",
      rationale: '',
    };
    expect(referencedSkillName(decision, ['disk_probe', 'oom_probe'])).toBe('oom_probe');
    expect(
      referencedSkillName({ evidence: [], summary: '', rationale: 'ran disk_probe on the node' }, [
        'disk_probe',
      ]),
    ).toBe('disk_probe');
  });

  it('returns empty when nothing references a skill', () => {
    expect(
      referencedSkillName({ evidence: [], summary: 'routine window', rationale: 'all quiet' }, [
        'oom_probe',
        '',
      ]),
    ).toBe('');
  });
});

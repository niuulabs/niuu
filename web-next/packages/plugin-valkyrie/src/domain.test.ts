import { describe, expect, it } from 'vitest';
import {
  autonomyModeForLevel,
  collapseDecisionsByCorrelation,
  decisionHasRealAction,
  decisionHeadline,
  decisionNeedsApproval,
  decisionSkillName,
  decisionSubject,
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
  ACTIVITY_STORY_LIMIT,
  LIST_LIMIT,
  type DecisionRecord,
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

function makeDecision(overrides: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    decisionId: 'decision-1',
    environmentId: 'env-k8s-valhalla',
    valkyrieId: 'valkyrie-valhalla-sigrun',
    operationalState: 'watching',
    tier: 'ambient',
    confidence: 0.7,
    rationale: 'routine',
    recommendedAction: 'none',
    actionAuthority: 'autonomous',
    signalRefs: [],
    evidence: [],
    correlationId: 'corr-1',
    summary: 'routine check',
    outcome: '',
    decidedAt: '2026-06-03T14:00:00Z',
    ...overrides,
  };
}

describe('list caps', () => {
  it('pins the shared list cap at 20 and the story cap at 30', () => {
    expect(LIST_LIMIT).toBe(20);
    expect(ACTIVITY_STORY_LIMIT).toBe(30);
  });
});

describe('decisionHasRealAction', () => {
  it.each(['', 'none', 'n/a', 'na', 'watch', 'observe', 'noop', ' None ', 'WATCH'])(
    'treats %j as not a real action',
    (action) => {
      expect(decisionHasRealAction(makeDecision({ recommendedAction: action }))).toBe(false);
    },
  );

  it('accepts a real capability as an action', () => {
    expect(decisionHasRealAction(makeDecision({ recommendedAction: 'refresh_pull_secret' }))).toBe(
      true,
    );
  });
});

describe('decisionNeedsApproval', () => {
  const approvable = {
    actionAuthority: 'human_review_required',
    tier: 'present',
    recommendedAction: 'restart_deployment',
  };

  it('is true only when authority, tier, and a real action all align', () => {
    expect(decisionNeedsApproval(makeDecision(approvable))).toBe(true);
    expect(decisionNeedsApproval(makeDecision({ ...approvable, tier: 'urgent' }))).toBe(true);
    expect(decisionNeedsApproval(makeDecision({ ...approvable, tier: 'URGENT ' }))).toBe(true);
  });

  it('rejects autonomous and court authorities regardless of tier', () => {
    expect(
      decisionNeedsApproval(makeDecision({ ...approvable, actionAuthority: 'autonomous' })),
    ).toBe(false);
    expect(
      decisionNeedsApproval(makeDecision({ ...approvable, actionAuthority: 'court_required' })),
    ).toBe(false);
  });

  it('rejects ambient and observational tiers — they never reach the inbox', () => {
    expect(decisionNeedsApproval(makeDecision({ ...approvable, tier: 'ambient' }))).toBe(false);
    expect(decisionNeedsApproval(makeDecision({ ...approvable, tier: 'observational' }))).toBe(
      false,
    );
    expect(decisionNeedsApproval(makeDecision({ ...approvable, tier: '' }))).toBe(false);
  });

  it.each(['', 'none', 'watch', 'observe', 'n/a'])(
    'rejects non-action verdict %j even at an operator tier',
    (action) => {
      expect(
        decisionNeedsApproval(makeDecision({ ...approvable, recommendedAction: action })),
      ).toBe(false);
    },
  );
});

describe('decisionHeadline', () => {
  it('uses the record summary and strips the redundant valkyrie/env prefix', () => {
    expect(
      decisionHeadline(
        makeDecision({
          summary:
            'Valkyrie valkyrie-valhalla-sigrun in env-k8s-valhalla judged the rollout healthy',
        }),
        'Watching',
      ),
    ).toBe('judged the rollout healthy');
    expect(
      decisionHeadline(makeDecision({ summary: 'valkyrie: sigrun in prod noticed drift' }), 'X'),
    ).toBe('noticed drift');
  });

  it('keeps summaries without the prefix untouched', () => {
    expect(
      decisionHeadline(makeDecision({ summary: 'Registry token rollover broke pulls' }), 'X'),
    ).toBe('Registry token rollover broke pulls');
  });

  it('falls back to the label for empty or prefix-only summaries', () => {
    expect(decisionHeadline(makeDecision({ summary: '' }), 'Watching')).toBe('Watching');
    expect(decisionHeadline(makeDecision({ summary: '   ' }), 'Watching')).toBe('Watching');
  });
});

describe('decisionSubject', () => {
  it('prefers a resource name from the evidence', () => {
    expect(
      decisionSubject(makeDecision({ evidence: [{ other: 1 }, { subject: 'pv/media-primary' }] })),
    ).toBe('pv/media-primary');
    expect(decisionSubject(makeDecision({ evidence: [{ pod: 'ravn-worker-77' }] }))).toBe(
      'ravn-worker-77',
    );
  });

  it('derives a readable subject from the correlation id otherwise', () => {
    expect(decisionSubject(makeDecision({ correlationId: 'corr-imagepull' }))).toBe('imagepull');
    expect(decisionSubject(makeDecision({ correlationId: 'idle-triage:env-k8s-valhalla' }))).toBe(
      'env-k8s-valhalla',
    );
    expect(decisionSubject(makeDecision({ correlationId: '' }))).toBe('');
  });
});

describe('collapseDecisionsByCorrelation', () => {
  it('collapses consecutive decisions sharing a correlation into one row, newest wins', () => {
    const rows = collapseDecisionsByCorrelation([
      makeDecision({ decisionId: 'd3', correlationId: 'corr-pv', decidedAt: '14:10' }),
      makeDecision({ decisionId: 'd2', correlationId: 'corr-pv', decidedAt: '14:05' }),
      makeDecision({ decisionId: 'd1', correlationId: 'corr-pv', decidedAt: '14:00' }),
      makeDecision({ decisionId: 'x1', correlationId: 'corr-other', decidedAt: '13:00' }),
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ count: 3 });
    // Newest-first input → the newest decision of the run is the one rendered.
    expect(rows[0]?.decision.decisionId).toBe('d3');
    expect(rows[0]?.decision.decidedAt).toBe('14:10');
    expect(rows[1]).toMatchObject({ count: 1 });
  });

  it('starts a fresh group when the correlation recurs after another decision', () => {
    const rows = collapseDecisionsByCorrelation([
      makeDecision({ decisionId: 'a1', correlationId: 'corr-a' }),
      makeDecision({ decisionId: 'b1', correlationId: 'corr-b' }),
      makeDecision({ decisionId: 'a2', correlationId: 'corr-a' }),
    ]);
    expect(rows.map((row) => row.decision.decisionId)).toEqual(['a1', 'b1', 'a2']);
    expect(rows.every((row) => row.count === 1)).toBe(true);
  });

  it('never groups decisions without a correlation id', () => {
    const rows = collapseDecisionsByCorrelation([
      makeDecision({ decisionId: 'n1', correlationId: '' }),
      makeDecision({ decisionId: 'n2', correlationId: '' }),
    ]);
    expect(rows).toHaveLength(2);
  });

  it('returns an empty list for no decisions', () => {
    expect(collapseDecisionsByCorrelation([])).toEqual([]);
  });
});

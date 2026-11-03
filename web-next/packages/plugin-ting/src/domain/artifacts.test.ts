import { describe, expect, it } from 'vitest';
import {
  answersPendingGate,
  artifactKeysShown,
  artifactReview,
  artifactsFromPublications,
  artifactStatus,
  gatesWithArtifacts,
  isReadable,
  newestFirst,
  revisionPrompt,
  runRef,
  versionDocument,
  type Publication,
  type RunRef,
} from './artifacts';
import type { ReviewRevision, RunReview } from './review';
import type { WorkSummary } from './work';

const RUN: RunRef = {
  slug: 'a2a-1',
  title: 'Developer Planning',
  status: 'running',
  condition: 'active',
  attention: null,
  sessionId: 'session-1',
  instanceId: 'valhalla',
  workflowId: 'wf-1',
  workflowVersion: '1.0.1',
  updatedAt: '2026-11-02T23:44:00Z',
};

const NONE = new Set<string>();

function publication(seq: number, overrides: Partial<Publication> = {}): Publication {
  return {
    seq,
    sessionId: 'session-1',
    providerKey: 'session-1:plan',
    revisionId: `pf_${seq}`,
    title: 'plan.md',
    url: `/api/v1/forge/sessions/session-1/files/presented/pf_${seq}`,
    at: `2026-11-02T23:${String(seq).padStart(2, '0')}:00Z`,
    mime: 'text/markdown',
    versionLabel: `v${seq}`,
    ...overrides,
  };
}

const PRESENTED: Publication[] = [
  publication(3, { revisionId: 'pf_3', versionLabel: 'v2' }),
  publication(1, { revisionId: 'pf_1', versionLabel: 'v1' }),
  // A republish of v2 is the same version.
  publication(4, { revisionId: 'pf_3', versionLabel: 'v2' }),
  publication(2, {
    providerKey: 'pf_2',
    revisionId: 'pf_2',
    title: 'diagram.png',
    mime: 'image/png',
    versionLabel: null,
  }),
  publication(5, { sessionId: 'other-session', providerKey: 'other-session:plan' }),
];

function gateRevision(overrides: Partial<ReviewRevision>): ReviewRevision {
  return {
    number: 1,
    gateId: 'g1',
    activationId: 'act-1',
    requestedAt: '2026-11-02T23:10:00Z',
    status: 'pending',
    summary: '',
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: { plan: 'the plan copied into the outcome' },
    ...overrides,
  };
}

function planGate(revisions: ReviewRevision[]): RunReview {
  return {
    columnId: 'plan-review',
    title: 'Plan review',
    review: 'plan',
    workflowName: 'Developer Planning',
    taskId: 'a2a-1',
    sessionId: 'session-1',
    panels: [{ widget: 'document', title: 'Plan', source: { kind: 'outcome', value: 'plan' } }],
    revisions,
  };
}

const SHOWN_V1 = {
  artifactKey: 'plan',
  providerKey: 'session-1:plan',
  revisionId: 'pf_1',
  name: 'plan.md',
  versionLabel: 'v1',
  mime: 'text/markdown',
  url: null,
};

describe('artifacts from publications', () => {
  it("makes one artifact per key of the run's own session, each file a version", () => {
    const [plan, diagram] = artifactsFromPublications(RUN, PRESENTED, NONE);
    expect(plan).toMatchObject({ id: 'session-1:plan', key: 'plan', title: 'Plan' });
    expect(plan!.versions.map((version) => [version.revisionId, version.label])).toEqual([
      ['pf_1', 'v1'],
      ['pf_3', 'v2'],
    ]);
    expect(plan!.updatedAt).toBe('2026-11-02T23:03:00Z');
    // A file presented without a key stands alone, named after itself.
    expect(diagram).toMatchObject({ key: 'pf_2', title: 'diagram.png' });
    expect(diagram!.versions[0]!.label).toBe('v1');
    expect(artifactsFromPublications(RUN, [], NONE)).toEqual([]);
  });

  it('reads text documents on the page and links everything else', () => {
    const [plan, diagram] = artifactsFromPublications(RUN, PRESENTED, NONE);
    const v1 = plan!.versions[0]!;
    const image = diagram!.versions[0]!;
    expect(isReadable(v1)).toBe(true);
    expect(isReadable({ ...v1, mime: null })).toBe(true);
    expect(isReadable(image)).toBe(false);
    expect(versionDocument(v1, '# Plan')).toBe('# Plan');
    expect(versionDocument(image, undefined)).toBe(
      '[Open diagram.png](/api/v1/forge/sessions/session-1/files/presented/pf_2)',
    );
    expect(versionDocument({ ...image, url: null }, undefined)).toBe('diagram.png');
  });
});

describe('artifact status', () => {
  it('waits on a person only while the run is held at a gate', () => {
    expect(artifactStatus({ ...RUN, status: 'blocked' }, NONE)).toBe('waiting');
    expect(artifactStatus(RUN, new Set(['a2a-1']))).toBe('waiting');
    // A finished run keeps its last attention note; it no longer waits.
    expect(
      artifactStatus(
        {
          ...RUN,
          status: 'completed',
          condition: 'completed',
          attention: { kind: 'decision', message: '' },
        },
        NONE,
      ),
    ).toBe('done');
    expect(artifactStatus({ ...RUN, status: 'failed', condition: 'failed' }, NONE)).toBe('failed');
    expect(artifactStatus(RUN, NONE)).toBe('inProgress');
  });
});

describe('reviewing artifacts', () => {
  it('carries the decision of the gate that showed each version', () => {
    const [plan] = artifactsFromPublications(RUN, PRESENTED, NONE);
    const gates = [
      planGate([
        gateRevision({
          status: 'changes_requested',
          notes: 'tighten it',
          decidedBy: 'jozef',
          artifacts: [SHOWN_V1],
        }),
        gateRevision({
          number: 2,
          gateId: 'g2',
          activationId: 'act-2',
          artifacts: [{ ...SHOWN_V1, revisionId: 'pf_3', versionLabel: 'v2' }],
        }),
      ]),
    ];
    const texts = new Map([['pf_1', '# Plan v1']]);
    const review = artifactReview(plan!, gates, texts);
    const [first, second] = review.revisions;
    expect(first).toMatchObject({
      gateId: 'g1',
      status: 'changes_requested',
      notes: 'tighten it',
      decidedBy: 'jozef',
      summary: 'v1',
      fields: { plan: '# Plan v1' },
    });
    expect(second).toMatchObject({ gateId: 'g2', activationId: 'act-2', status: 'pending' });
    expect(answersPendingGate(review)).toBe(true);
    expect(review.panels[0]!.source).toEqual({ kind: 'outcome', value: 'plan' });
  });

  it('is only presented when no gate showed it', () => {
    const [plan] = artifactsFromPublications(RUN, PRESENTED, NONE);
    const review = artifactReview(plan!, [], new Map());
    expect(review.revisions.map((revision) => [revision.gateId, revision.status])).toEqual([
      ['session-1:plan#1', 'Presented'],
      ['session-1:plan#2', 'Presented'],
    ]);
    expect(answersPendingGate(review)).toBe(false);
  });

  it("reads a gate's panel from the version it showed, keeping the outcome otherwise", () => {
    const presented = artifactsFromPublications(RUN, PRESENTED, NONE);
    const gates = [
      planGate([
        gateRevision({ artifacts: [SHOWN_V1] }),
        gateRevision({ number: 2, gateId: 'g2' }),
        gateRevision({ number: 3, artifacts: [{ ...SHOWN_V1, revisionId: 'pf_gone' }] }),
      ]),
    ];
    const [gate] = gatesWithArtifacts(gates, presented, new Map([['pf_1', '# Plan v1']]));
    expect(gate!.revisions.map((revision) => revision.fields.plan)).toEqual([
      '# Plan v1',
      'the plan copied into the outcome',
      'the plan copied into the outcome',
    ]);
    expect(artifactKeysShown(gates)).toEqual(new Set(['plan']));
    expect(artifactKeysShown([planGate([gateRevision({})])])).toEqual(new Set());
  });

  it('asks a new run to revise a version against the notes, under the same key', () => {
    const [plan] = artifactsFromPublications(RUN, PRESENTED, NONE);
    const prompt = revisionPrompt(
      plan!,
      plan!.versions[1]!,
      '# Plan v2',
      'Plan NIU-296',
      'Fix P-2',
    );
    expect(prompt).toContain('Plan NIU-296');
    expect(prompt).toContain('v2 from run a2a-1');
    expect(prompt).toContain('artifact key plan');
    expect(prompt).toContain("Reviewer's notes:\nFix P-2");
    expect(prompt).toContain('Previous plan:\n# Plan v2');
  });
});

describe('helpers', () => {
  it('orders artifacts newest first', () => {
    const [plan, diagram] = artifactsFromPublications(RUN, PRESENTED, NONE);
    expect(newestFirst([diagram!, plan!]).map((item) => item.id)).toEqual([plan!.id, diagram!.id]);
  });

  it('turns a Work run into a run reference, or nothing without a session', () => {
    const summary = {
      id: 'campaign:1',
      kind: 'workflowLaunch',
      title: 'Developer Planning',
      campaignSlug: 'a2a-1',
      condition: 'active',
      rawState: { source: 'campaign', status: 'running' },
      currentStep: null,
      attention: null,
      createdAt: null,
      updatedAt: null,
      source: null,
      workflow: { id: 'wf-1', version: '1.0.1', documentRevision: null },
      session: { id: 'session-1', connectionId: 'valhalla' },
      links: { self: '', specialist: '' },
      actions: [],
    } satisfies WorkSummary;
    expect(runRef(summary)).toMatchObject({
      slug: 'a2a-1',
      sessionId: 'session-1',
      workflowId: 'wf-1',
    });
    expect(runRef({ ...summary, session: null })).toBeNull();
    expect(runRef({ ...summary, workflow: null })).toMatchObject({ workflowId: null });
  });
});

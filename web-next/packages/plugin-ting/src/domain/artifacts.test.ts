import { describe, expect, it } from 'vitest';
import {
  artifactReview,
  artifactsFromOutcomes,
  artifactStatus,
  documentFieldsShown,
  feedbackFor,
  launchPrompt,
  newestFirst,
  producerName,
  revisionPrompt,
  runBrief,
  runRef,
  type RunOutcome,
  type RunRef,
} from './artifacts';
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

function outcome(seq: number, overrides: Partial<RunOutcome>): RunOutcome {
  return {
    seq,
    at: `2026-11-02T23:${String(seq).padStart(2, '0')}:00Z`,
    eventType: 'developer.plan.revised',
    participantId: 'flock-developer-analyst',
    participantType: 'ravn',
    fields: {},
    ...overrides,
  };
}

const NONE = new Set<string>();

const LOG: RunOutcome[] = [
  outcome(1, {
    participantId: 'skuld-1',
    participantType: 'skuld',
    eventType: 'delivery.requested',
    fields: {
      prompt:
        '# Workflow Launch\n\nWorkflow: Developer Planning\n\n## Prompt\nPlan NIU-296\nRead the ticket.\n\n## Expectations\nFollow the graph.',
    },
  }),
  // A single-line plan: its longest text is the document, not the digest or the summary.
  outcome(2, {
    fields: {
      plan: 'P-1: add the adapter. P-2: validate the realm id before polling it.',
      ticket_digest: 'Plan NIU-296 only',
      summary: 'First draft of the plan for review',
      verdict: 'ready_for_review',
      base_sha: '4671b57',
    },
  }),
  outcome(3, {
    participantId: 'flock-developer-architecture-reviewer',
    eventType: 'plan.review.completed',
    fields: { summary: 'Signal parsing is underspecified.', verdict: 'changes_required' },
  }),
  outcome(4, {
    fields: {
      plan: 'P-1: add the adapter.\nP-2: parse signals strictly.',
      verdict: 'ready_for_review',
    },
  }),
  outcome(5, {
    participantId: 'workflow-runtime',
    participantType: 'workflow',
    eventType: 'developer.plan.approved',
    fields: { summary: 'Approved after review', verdict: 'approved' },
  }),
];

describe('artifacts from a run log', () => {
  it('makes one artifact per agent field, each report of it a version', () => {
    const [plan, ...others] = artifactsFromOutcomes(RUN, LOG, NONE);
    expect(others).toEqual([]);
    expect(plan).toMatchObject({
      id: 'a2a-1/flock-developer-analyst/plan',
      title: 'Plan',
      producer: 'Developer analyst',
      subject: 'Plan NIU-296',
      status: 'inProgress',
      updatedAt: '2026-11-02T23:04:00Z',
    });
    expect(plan!.versions.map((version) => [version.number, version.seq, version.verdict])).toEqual(
      [
        [1, 2, 'ready_for_review'],
        [2, 4, 'ready_for_review'],
      ],
    );
  });

  it('keeps what the others said, between the versions it answers', () => {
    const [plan] = artifactsFromOutcomes(RUN, LOG, NONE);
    expect(plan!.feedback.map((item) => [item.from, item.verdict])).toEqual([
      ['Developer architecture reviewer', 'changes_required'],
      ['Workflow', 'approved'],
    ]);
    expect(feedbackFor(plan!, plan!.versions[0]!).map((item) => item.seq)).toEqual([3]);
    expect(feedbackFor(plan!, plan!.versions[1]!).map((item) => item.seq)).toEqual([5]);
  });

  it('takes every multi-line field of an outcome as its own document', () => {
    const artifacts = artifactsFromOutcomes(
      RUN,
      [
        outcome(1, {
          participantId: 'flock-reviewer',
          fields: { comments: '## Major\n- one', findings: '## High\n- two', summary: 'x y' },
        }),
      ],
      NONE,
    );
    expect(artifacts.map((artifact) => artifact.title).sort()).toEqual(['Comments', 'Findings']);
  });

  it('finds nothing in identifiers, verdicts or a lone summary', () => {
    expect(
      artifactsFromOutcomes(
        RUN,
        [
          outcome(1, {
            fields: { verdict: 'approved', base_sha: 'abc', summary: 'All good here' },
          }),
        ],
        NONE,
      ),
    ).toEqual([]);
  });

  it('subjects a run with no brief by its title', () => {
    const [plan] = artifactsFromOutcomes(RUN, LOG.slice(1), NONE);
    expect(plan!.subject).toBe('Developer Planning');
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

describe('run briefs', () => {
  it("reads the person's prompt out of Ting's launch brief", () => {
    expect(runBrief(LOG)).toBe('Plan NIU-296\nRead the ticket.');
    expect(launchPrompt('Just do it')).toBe('Just do it');
    expect(launchPrompt('# Workflow Launch\n\n## Prompt\nLast section')).toBe('Last section');
    expect(runBrief([])).toBe('');
  });

  it('asks a new run to revise a version against the notes', () => {
    const [plan] = artifactsFromOutcomes(RUN, LOG, NONE);
    const prompt = revisionPrompt(plan!, plan!.versions[1]!, 'Plan NIU-296', 'Name the setting.');
    expect(prompt).toContain('Plan NIU-296');
    expect(prompt).toContain('version 2 from run a2a-1');
    expect(prompt).toContain("Reviewer's notes:\nName the setting.");
    expect(prompt).toContain('Previous plan:\nP-1: add the adapter.\nP-2: parse signals strictly.');
  });
});

describe('artifacts in the review workspace', () => {
  it('reads as a review, its latest version answering a waiting gate', () => {
    const [plan] = artifactsFromOutcomes(RUN, LOG, NONE);
    const review = artifactReview(plan!, { id: 'gate-7', activationId: 'act-7' });
    expect(review.panels).toEqual([
      { widget: 'document', title: 'Plan', source: { kind: 'outcome', value: 'plan' } },
    ]);
    const [first, latest] = review.revisions;
    expect(first).toMatchObject({ gateId: `${plan!.id}#1`, status: 'Ready for review' });
    expect(first!.notes).toBe(
      'Developer architecture reviewer · Changes required: Signal parsing is underspecified.',
    );
    expect(latest).toMatchObject({ gateId: 'gate-7', activationId: 'act-7', status: 'pending' });
    expect(latest!.fields.plan).toContain('parse signals strictly');
  });

  it('has no gate to answer when the run waits on none', () => {
    const [plan] = artifactsFromOutcomes(RUN, LOG, NONE);
    expect(artifactReview(plan!, null).revisions.every((item) => item.status !== 'pending')).toBe(
      true,
    );
  });

  it("knows which fields a gate's own review already shows", () => {
    expect(
      documentFieldsShown([
        {
          columnId: 'c',
          title: 'Review',
          review: null,
          workflowName: 'w',
          taskId: 't',
          sessionId: 's',
          panels: [
            { widget: 'document', title: 'Plan', source: { kind: 'outcome', value: 'plan' } },
            { widget: 'diff', title: 'Diff' },
          ],
          revisions: [],
        },
      ]),
    ).toEqual(new Set(['plan']));
  });
});

describe('helpers', () => {
  it('names producers and orders artifacts newest first', () => {
    expect(producerName('flock-developer-analyst')).toBe('Developer analyst');
    const [plan] = artifactsFromOutcomes(RUN, LOG, NONE);
    const older = { ...plan!, id: 'older', updatedAt: '2026-01-01T00:00:00Z' };
    expect(newestFirst([older, plan!]).map((item) => item.id)).toEqual([plan!.id, 'older']);
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

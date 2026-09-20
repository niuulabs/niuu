import { describe, expect, it } from 'vitest';
import type { WorkflowWait, WorkflowExecution } from '../domain/workflowExecution';
import { buildWorkflowExecutionResultsMarkdown } from './workflowExecutionResults';

const execution: WorkflowExecution = {
  executionId: 'execution-1',
  name: 'Portable delivery',
  prompt: 'Ship it',
  repo: 'https://git.example/team/repo',
  baseBranch: 'main',
  baseSha: 'a'.repeat(40),
  workflowId: 'workflow-1',
  state: 'running',
  suspensionReason: 'children_contract_valid',
  currentGeneration: 1,
  planRevision: 'plan-1',
  budget: { totalUnits: 100, reservedUnits: 60, spentUnits: 10, availableUnits: 30 },
  join: { ready: true },
  children: [
    {
      childId: 'attempt-1',
      childKey: 'parser',
      attempt: 1,
      generation: 1,
      state: 'completed',
      dependencies: [],
      requirementIds: ['REQ-PARSER'],
      taskHandle: { agentId: 'agent', taskId: 'task' },
      workspace: null,
      error: null,
      evidenceValidation: { accepted: true, manifest_digest: 'manifest' },
      candidate: {
        attemptId: 'attempt-1',
        candidateSha: 'b'.repeat(40),
        candidateTree: 'c'.repeat(40),
        verificationReceipts: [
          {
            receipt_id: 'check-1',
            contract_id: 'unit-tests',
            exit_code: 0,
            provenance: { signature: 'signed-value' },
          },
        ],
        reviewReceipts: [
          {
            receipt_id: 'review-1',
            role: 'security',
            verdict: 'pass',
            reviewer_id: 'reviewer-1',
            provenance: { signature: 'review-signature' },
          },
        ],
        requirementEvidence: [
          {
            requirement_id: 'REQ-PARSER',
            implementation_paths: ['parser.py'],
            verification_contract_ids: ['unit-tests'],
          },
        ],
      },
    },
  ],
  integrationReceipts: [{ receipt_id: 'integration-1' }],
  integrationCandidate: { candidate_sha: 'd'.repeat(40), candidate_tree: 'e'.repeat(40) },
  integrationReviewReceipt: {
    role: 'integration',
    verdict: 'pass',
    provenance: { signature: 'integration-signature' },
  },
  mergeReceipt: null,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T01:00:00Z',
};

/**
 * A `forge.checks` wait. `status: 'stale'` models a wait whose observation
 * reports a candidate that no longer matches the persisted request identity
 * (the generic equivalent of what a distinct "stale_candidate" status used to
 * mean) rather than a distinct backend status.
 */
function currentChecksWait(
  status: 'pending' | 'satisfied' | 'failed' | 'stale',
  changes: Partial<WorkflowWait> = {},
): WorkflowWait {
  const headSha = 'd'.repeat(40);
  const baseSha = 'a'.repeat(40);
  const observedSha = status === 'stale' ? 'f'.repeat(40) : headSha;
  return {
    waitId: `wait-${status}`,
    executionId: execution.executionId,
    nodeId: 'delivery-publication-wait',
    conditionType: 'forge.checks',
    state: status === 'pending' ? 'pending' : 'notified',
    requestDigest: `digest-${status}`,
    generation: 1,
    executionRevision: 8,
    request: {
      repository: execution.repo,
      reviewNumber: 42,
      expectedHeadSha: headSha,
      expectedBaseSha: baseSha,
      expectedTargetBranch: 'main',
      policyId: 'delivery-policy',
    },
    nextPollAt: '2026-01-01T03:00:00Z',
    attemptCount: 2,
    lastError: '',
    observation: {
      status: status === 'stale' ? 'failed' : status,
      observedAt: '2026-01-01T02:00:00Z',
      reason: status,
      detail: {
        candidate: {
          provider: 'forge-provider',
          repository: execution.repo,
          review_number: 42,
          source_branch: 'feature',
          target_branch: 'main',
          candidate_sha: observedSha,
          tested_base_sha: baseSha,
          current_target_sha: baseSha,
          mergeable: status !== 'stale',
          checks: [],
          serialized_publication: true,
        },
        checks:
          status === 'stale'
            ? null
            : {
                receipt_id: `receipt-${status}`,
                provider: 'forge-provider',
                repository: execution.repo,
                review_number: 42,
                candidate_sha: headSha,
                tested_base_sha: baseSha,
                observed_at: '2026-01-01T02:00:00Z',
                provenance: { signature: 'present' },
                checks: [
                  {
                    name: 'required-ci',
                    conclusion:
                      status === 'satisfied'
                        ? 'passing'
                        : status === 'failed'
                          ? 'failing'
                          : 'pending',
                  },
                ],
              },
        mergeReceipt: null,
      },
    },
    ...changes,
  };
}

describe('buildWorkflowExecutionResultsMarkdown', () => {
  it('reports accepted work, integration, and pending publication without overstating trust', () => {
    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {
      workflowDigest: 'sha256:digest',
      verification: { status: 'accepted', blockingReasons: [] },
    });

    expect(markdown).toContain('# Portable delivery');
    expect(markdown).toContain('[Open repository](<https://git.example/team/repo>)');
    expect(markdown).toContain('| parser | 1 | 1 | completed |');
    expect(markdown).toContain('| unit-tests | 0 | `check-1` | Present |');
    expect(markdown).toContain('| security | pass | reviewer-1 | Present |');
    expect(markdown).toContain('**REQ-PARSER** — paths: `parser.py`; contracts: `unit-tests`');
    expect(markdown).toContain('Integration receipts recorded: **1**');
    expect(markdown).toContain('No merge receipt has been recorded.');
    expect(markdown).toContain('No remote delivery wait has been recorded.');
    expect(markdown).toContain('this browser view does not independently verify its cryptography');
    expect(markdown).toContain(
      'Remote review/publication has not been observed for the current integration candidate.',
    );
    expect(markdown).toContain(
      'Remote CI success is not established for the current integration candidate.',
    );
    expect(markdown).toContain('Merge is not established for the current integration candidate.');
    expect(markdown).not.toContain('cryptographically verified');
  });

  it('renders incomplete work without inventing candidates, checks, or integration', () => {
    const markdown = buildWorkflowExecutionResultsMarkdown(
      {
        ...execution,
        state: 'blocked',
        children: [{ ...execution.children[0]!, state: 'blocked', candidate: null }],
        integrationCandidate: null,
        integrationReceipts: [],
        integrationReviewReceipt: null,
      },
      {},
    );

    expect(markdown).toContain(
      'Current child evidence status reported by the server: **not reported**',
    );
    expect(markdown).toContain('No candidate has been recorded for this attempt.');
    expect(markdown).toContain('No integrated candidate has been recorded.');
    expect(markdown).toContain('The workflow is **blocked**; this is an interim report.');
  });

  it('escapes table delimiters from provider-neutral labels', () => {
    const markdown = buildWorkflowExecutionResultsMarkdown(
      {
        ...execution,
        children: [{ ...execution.children[0]!, childKey: 'api|worker' }],
      },
      { verification: { status: 'accepted|with notes' } },
    );

    expect(markdown).toContain('| api\\|worker |');
    expect(markdown).toContain('**accepted\\|with notes**');
  });

  it('separates historical attempts from current evidence status', () => {
    const previous = {
      ...execution.children[0]!,
      childId: 'attempt-previous',
      attempt: 1,
      state: 'superseded',
      error: { kind: 'verification_failed', detail: 'Old attempt failed.' },
    };
    const current = {
      ...execution.children[0]!,
      childId: 'attempt-current',
      attempt: 2,
      candidate: { ...execution.children[0]!.candidate, attemptId: 'attempt-current' },
    };
    const markdown = buildWorkflowExecutionResultsMarkdown(
      { ...execution, children: [previous, current] },
      { verification: { status: 'accepted' } },
    );

    expect(markdown).toContain('## Historical attempts');
    expect(markdown).toContain('excluded from the current child evidence status');
    expect(markdown).toContain('| parser | 1 | 1 | superseded |');
    expect(markdown).not.toContain('Old attempt failed.');
    expect(markdown).toContain(
      'No blocking reasons, child errors, questions, gates, or join blockers were included',
    );
  });

  it('includes current verification, child, input, gate, and join blockers', () => {
    const child = {
      ...execution.children[0]!,
      state: 'blocked',
      evidenceValidation: { accepted: false, blocking_reasons: ['Receipt rejected'] },
      error: { kind: 'remote_failed', detail: 'Worker stopped' },
      pendingQuestions: [
        {
          requestId: 'question-1',
          persona: 'coder',
          question: 'Choose a format?',
          reason: '',
          recommendation: '',
          attempted: [],
        },
      ],
      pendingGates: [
        {
          gateId: 'gate-1',
          nodeId: 'review',
          label: 'Approval',
          condition: '',
          instructions: '',
          summary: 'Waiting for a reviewer',
        },
      ],
    };
    const markdown = buildWorkflowExecutionResultsMarkdown(
      {
        ...execution,
        state: 'blocked',
        children: [child],
        join: { pending: ['parser'], blocked: ['review'], failed: ['tests'] },
      },
      { verification: { status: 'rejected', blockingReasons: ['Current envelope rejected'] } },
    );

    expect(markdown).toContain('Current child evidence: Current envelope rejected');
    expect(markdown).toContain('**parser** evidence: Receipt rejected');
    expect(markdown).toContain('**parser** error: Worker stopped');
    expect(markdown).toContain('question `question-1`: Choose a format?');
    expect(markdown).toContain('gate `gate-1`: Approval — Waiting for a reviewer');
    expect(markdown).toContain('Join pending: `parser`');
    expect(markdown).toContain('Join blocked: `review`');
    expect(markdown).toContain('Join failed: `tests`');
  });

  it('contains hostile provider text without allowing it to create report structure', () => {
    const hostileCandidate = {
      ...execution.children[0]!.candidate,
      requirementEvidence: [
        {
          requirement_id: 'REQ|FORGED\n## Injected',
          implementation_paths: ['file```\n# forged-heading'],
          verification_contract_ids: ['check`value'],
        },
      ],
    };
    const markdown = buildWorkflowExecutionResultsMarkdown(
      {
        ...execution,
        name: '# Forged\n## Heading <script>',
        repo: 'https://example.com/repo)\n# forged-link',
        children: [
          {
            ...execution.children[0]!,
            childKey: 'api|worker\n## forged-workstream',
            candidate: hostileCandidate,
          },
        ],
      },
      { verification: { status: 'accepted\n# forged-status' } },
    );
    const headings = markdown.split('\n').filter((line) => /^#{1,6} /.test(line));

    expect(headings).not.toContain('# forged-link');
    expect(headings).not.toContain('# forged-heading');
    expect(headings).not.toContain('## Injected');
    expect(headings).not.toContain('## forged-workstream');
    expect(markdown).not.toContain('](javascript:');
    expect(markdown).toContain('````file``` # forged-heading````');
    expect(markdown).toContain('``check`value``');
  });

  it('renders durable remote verification and merge observations from exact API fields', () => {
    const waits: WorkflowWait[] = [
      {
        waitId: 'checks-wait',
        executionId: execution.executionId,
        nodeId: 'delivery-publication-wait',
        conditionType: 'forge.checks',
        state: 'notified',
        requestDigest: 'request-digest',
        generation: 1,
        executionRevision: 8,
        request: {
          repository: execution.repo,
          reviewNumber: 42,
          expectedHeadSha: 'd'.repeat(40),
          expectedBaseSha: 'a'.repeat(40),
          expectedTargetBranch: 'main',
          policyId: 'delivery-policy',
        },
        nextPollAt: '2026-01-01T03:00:00Z',
        attemptCount: 2,
        lastError: '',
        observation: {
          status: 'satisfied',
          observedAt: '2026-01-01T02:00:00Z',
          reason: 'All required checks passed',
          detail: {
            candidate: {
              provider: 'forge-provider',
              repository: execution.repo,
              review_number: 42,
              source_branch: 'feature',
              target_branch: 'main',
              candidate_sha: 'd'.repeat(40),
              tested_base_sha: 'a'.repeat(40),
              current_target_sha: 'a'.repeat(40),
              mergeable: true,
              checks: [],
              serialized_publication: true,
            },
            checks: {
              receipt_id: 'remote-check-receipt',
              provider: 'forge-provider',
              repository: execution.repo,
              review_number: 42,
              candidate_sha: 'd'.repeat(40),
              tested_base_sha: 'a'.repeat(40),
              observed_at: '2026-01-01T02:00:00Z',
              provenance: { signature: 'present' },
              checks: [
                {
                  name: 'required-ci',
                  conclusion: 'passing',
                  details_url: 'https://ci.example/jobs/1',
                },
              ],
            },
            mergeReceipt: null,
          },
        },
      },
      {
        waitId: 'merge-wait',
        executionId: execution.executionId,
        nodeId: 'delivery-publication-wait',
        conditionType: 'forge.merge',
        state: 'ready',
        requestDigest: 'merge-request-digest',
        generation: 1,
        executionRevision: 9,
        request: {
          repository: execution.repo,
          reviewNumber: 42,
          expectedHeadSha: 'd'.repeat(40),
          expectedBaseSha: 'a'.repeat(40),
          expectedTargetBranch: 'main',
          policyId: 'delivery-policy',
          method: 'squash',
          providerOperationId: 'operation-7',
        },
        nextPollAt: '2026-01-01T04:00:00Z',
        attemptCount: 3,
        lastError: '',
        observation: {
          status: 'satisfied',
          observedAt: '2026-01-01T03:00:00Z',
          reason: '',
          detail: {
            candidate: null,
            checks: null,
            mergeReceipt: {
              receipt_id: 'merge-receipt',
              provider: 'forge-provider',
              repository: execution.repo,
              review_number: 42,
              source_sha: 'd'.repeat(40),
              base_sha: 'a'.repeat(40),
              target_branch: 'main',
              result_sha: 'e'.repeat(40),
              canonical_target_sha: 'e'.repeat(40),
              method: 'squash',
              state: 'merged',
              provider_operation_id: 'operation-7',
              verified_at: '2026-01-01T03:00:00Z',
              provenance: { signature: 'present' },
            },
          },
        },
      },
    ];

    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {}, waits);

    expect(markdown).toContain('### Remote verification · review #42');
    expect(markdown).toContain('| Condition type | `forge.checks` |');
    expect(markdown).toContain('| Provider | `forge-provider` |');
    expect(markdown).toContain('| Observation status | `satisfied` |');
    expect(markdown).toContain('| required-ci | `passing` |');
    expect(markdown).toContain('[Open authoritative details](<https://ci.example/jobs/1>)');
    expect(markdown).toContain('Observed candidate `dddddddddddd…`');
    expect(markdown).toContain('### Merge operation · review #42');
    expect(markdown).toContain('| Provider operation | `operation-7` |');
    expect(markdown).toContain('Merge receipt state: **merged**');
    expect(markdown).toContain('Merge receipt provider operation: `operation-7`');
    expect(markdown).toContain('Last observation | 2026-01-01T03:00:00Z');
    expect(markdown).toContain(
      'Remote CI success is established by a matching current-candidate `satisfied` forge.checks observation.',
    );
    expect(markdown).toContain(
      'Merge is established by an authoritative receipt matching the current candidate.',
    );
  });

  it('establishes current remote publication and CI without claiming an unobserved merge', () => {
    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {}, [
      currentChecksWait('satisfied'),
    ]);

    expect(markdown).toContain(
      'Remote review/publication was observed for the current integration candidate.',
    );
    expect(markdown).toContain(
      'Remote CI success is established by a matching current-candidate `satisfied` forge.checks observation.',
    );
    expect(markdown).toContain('Merge is not established for the current integration candidate.');
    expect(markdown).not.toContain(
      'Remote publication, CI success, and merge are not established.',
    );
  });

  it.each([
    ['pending', 'Remote CI is pending for the current delivery request'],
    ['failed', 'Remote CI failed for the current delivery request'],
  ] as const)('reports a current %s observation precisely', (status, expected) => {
    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {}, [
      currentChecksWait(status),
    ]);

    expect(markdown).toContain(
      'Remote review/publication was observed for the current integration candidate.',
    );
    expect(markdown).toContain(expected);
    expect(markdown).toContain('Merge is not established');
    expect(markdown).not.toContain('Remote CI success is established');
  });

  it('does not establish current publication or CI from an observation whose detail does not match', () => {
    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {}, [
      currentChecksWait('stale'),
    ]);

    expect(markdown).toContain('· Non-current identity');
    expect(markdown).toContain(
      'This observation is retained as history and does not establish the current generation’s delivery state.',
    );
    expect(markdown).toContain(
      'The provider reported an observation that does not match the current candidate; remote review/publication is not established',
    );
    expect(markdown).toContain(
      'Remote CI success is not established because the reported observation does not match the current candidate.',
    );
  });

  it('labels prior-generation checks as historical without clearing current limitations', () => {
    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {}, [
      currentChecksWait('satisfied', { generation: 0 }),
    ]);

    expect(markdown).toContain('· Historical generation');
    expect(markdown).toContain(
      'Remote review/publication has not been observed for the current integration candidate.',
    );
    expect(markdown).toContain(
      'Remote CI success is not established for the current integration candidate.',
    );
    expect(markdown).not.toContain('Remote CI success is established');
  });

  it('renders a non-forge condition generically and surfaces its detail fields', () => {
    const timerWait: WorkflowWait = {
      waitId: 'timer-wait',
      executionId: execution.executionId,
      nodeId: 'delivery-publication-wait',
      conditionType: 'timer',
      state: 'notified',
      requestDigest: 'timer-request-digest',
      generation: 1,
      executionRevision: 5,
      request: { after_seconds: 3600 },
      nextPollAt: '2026-01-01T04:00:00Z',
      attemptCount: 1,
      lastError: '',
      observation: {
        status: 'satisfied',
        observedAt: '2026-01-01T02:00:00Z',
        reason: 'The configured deadline has passed',
        detail: { until: '2026-01-01T01:00:00+00:00' },
      },
    };

    const markdown = buildWorkflowExecutionResultsMarkdown(execution, {}, [timerWait]);

    expect(markdown).toContain('### timer');
    expect(markdown).toContain('| Condition type | `timer` |');
    expect(markdown).toContain('| Observation status | `satisfied` |');
    expect(markdown).toContain('Additional observation detail:');
    expect(markdown).toContain('- until: "2026-01-01T01:00:00\\+00:00"');
  });
});

import type {
  DeliveryCandidate,
  WorkflowChildExecution,
  WorkflowWait,
  WorkflowExecution,
  AttestedReviewReceipt,
  DeliveryVerificationReceipt,
} from '../domain/workflowExecution';

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function normalizedText(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

function markdownText(value: string): string {
  return normalizedText(value).replace(/([\\`*_[\]{}()#+.!|>])/g, '\\$1');
}

function escapeCell(value: string): string {
  return markdownText(value);
}

function code(value: string | null | undefined): string {
  if (!value) return '—';
  const content = normalizedText(value);
  if (!content) return '—';
  const longestRun = Math.max(0, ...(content.match(/`+/g)?.map((run) => run.length) ?? []));
  const delimiter = '`'.repeat(longestRun + 1);
  const padded = content.startsWith('`') || content.endsWith('`') ? ` ${content} ` : content;
  return `${delimiter}${padded}${delimiter}`;
}

function shortCode(value: string | null | undefined): string {
  if (!value) return '—';
  return code(value.length > 16 ? `${value.slice(0, 12)}…` : value);
}

function link(label: string, value: string): string {
  if (
    [...value].some((character) => {
      const point = character.codePointAt(0) ?? 0;
      return point <= 31 || point === 127;
    })
  )
    return code(value);
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return code(value);
    const destination = url.href.replace(/[<>\s]/g, (character) => encodeURIComponent(character));
    return `[${markdownText(label)}](<${destination}>)`;
  } catch {
    return code(value);
  }
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function signatureState(receipt: { provenance?: { signature?: string } | null }): string {
  return receipt.provenance?.signature ? 'Present' : 'Absent';
}

function candidateFor(child: WorkflowChildExecution): DeliveryCandidate | null {
  return child.candidate ?? null;
}

function verificationRows(receipts: DeliveryVerificationReceipt[]): string[] {
  return receipts.map((receipt) => {
    const exitCode = numberValue(receipt.exit_code);
    return `| ${escapeCell(receipt.contract_id ?? 'Unlabelled contract')} | ${exitCode ?? '—'} | ${shortCode(receipt.receipt_id)} | ${signatureState(receipt)} |`;
  });
}

function reviewRows(receipts: AttestedReviewReceipt[]): string[] {
  return receipts.map(
    (receipt) =>
      `| ${escapeCell(receipt.role ?? 'Unlabelled review')} | ${escapeCell(receipt.verdict ?? 'Recorded')} | ${escapeCell(receipt.reviewer_id ?? '—')} | ${signatureState(receipt)} |`,
  );
}

function workstreamSummary(child: WorkflowChildExecution): string {
  const candidate = candidateFor(child);
  const verificationReceipts = candidate?.verificationReceipts?.length ?? 0;
  const reviews = candidate?.reviewReceipts?.length ?? 0;
  const validation = child.evidenceValidation
    ? child.evidenceValidation.accepted
      ? 'Accepted by server'
      : 'Rejected by server'
    : 'Not reported';
  return `| ${escapeCell(child.childKey)} | ${child.generation ?? '—'} | ${child.attempt} | ${escapeCell(child.state)} | ${shortCode(candidate?.candidateSha)} | ${verificationReceipts} | ${reviews} | ${validation} |`;
}

function currentAndHistoricalChildren(execution: WorkflowExecution): {
  current: WorkflowChildExecution[];
  historical: WorkflowChildExecution[];
} {
  const eligible = execution.children.filter(
    (child) => child.generation === undefined || child.generation === execution.currentGeneration,
  );
  const latest = new Map<string, WorkflowChildExecution>();
  for (const child of eligible) {
    const previous = latest.get(child.childKey);
    if (!previous || child.attempt > previous.attempt) latest.set(child.childKey, child);
  }
  const current = [...latest.values()];
  const currentIds = new Set(current.map((child) => child.childId));
  return {
    current,
    historical: execution.children.filter((child) => !currentIds.has(child.childId)),
  };
}

function requirementLines(candidate: DeliveryCandidate): string[] {
  return (candidate.requirementEvidence ?? []).map((item) => {
    const paths = item.implementation_paths?.map(code).join(', ') || '—';
    const contracts = item.verification_contract_ids?.map(code).join(', ') || '—';
    return `- **${escapeCell(item.requirement_id ?? 'Unlabelled requirement')}** — paths: ${paths}; contracts: ${contracts}`;
  });
}

function workstreamDetails(child: WorkflowChildExecution): string[] {
  const candidate = candidateFor(child);
  const lines = [`### ${markdownText(child.childKey)}`, ''];
  if (!candidate) {
    lines.push('No candidate has been recorded for this attempt.', '');
    return lines;
  }
  lines.push(
    `Candidate ${shortCode(candidate.candidateSha)} · tree ${shortCode(candidate.candidateTree)} · attempt ${code(candidate.attemptId ?? child.childId)}`,
    '',
  );
  const verificationReceipts = candidate.verificationReceipts ?? [];
  if (verificationReceipts.length) {
    lines.push(
      '#### Verification and contract receipts',
      '',
      '| Contract | Exit | Receipt | Signature |',
      '| --- | ---: | --- | --- |',
      ...verificationRows(verificationReceipts),
      '',
    );
  } else {
    lines.push('No verification receipts have been recorded.', '');
  }
  const reviews = candidate.reviewReceipts ?? [];
  if (reviews.length) {
    lines.push(
      '#### Recorded reviews',
      '',
      '| Role | Verdict | Reviewer | Signature |',
      '| --- | --- | --- | --- |',
      ...reviewRows(reviews),
      '',
    );
  } else {
    lines.push('No review receipts have been recorded.', '');
  }
  const requirements = requirementLines(candidate);
  if (requirements.length) lines.push('#### Requirement evidence', '', ...requirements, '');
  return lines;
}

function blockerLines(
  execution: WorkflowExecution,
  currentChildren: WorkflowChildExecution[],
  verification: Record<string, unknown> | null,
): string[] {
  const blockers = [
    ...strings(verification?.blockingReasons).map(
      (reason) => `- Current child evidence: ${markdownText(reason)}`,
    ),
    ...strings(verification?.blocking_reasons).map(
      (reason) => `- Current child evidence: ${markdownText(reason)}`,
    ),
  ];
  for (const child of currentChildren) {
    blockers.push(
      ...(child.evidenceValidation?.blocking_reasons ?? []).map(
        (reason) => `- **${markdownText(child.childKey)}** evidence: ${markdownText(reason)}`,
      ),
    );
    if (child.error?.detail) {
      blockers.push(
        `- **${markdownText(child.childKey)}** error: ${markdownText(child.error.detail)}`,
      );
    }
    blockers.push(
      ...(child.pendingQuestions ?? []).map(
        (question) =>
          `- **${markdownText(child.childKey)}** question ${code(question.requestId)}: ${markdownText(question.question)}`,
      ),
      ...(child.pendingGates ?? []).map(
        (gate) =>
          `- **${markdownText(child.childKey)}** gate ${code(gate.gateId)}: ${markdownText(gate.label)}${gate.summary ? ` — ${markdownText(gate.summary)}` : ''}`,
      ),
    );
  }
  const join = record(execution.join);
  for (const field of ['pending', 'blocked', 'failed'] as const) {
    const keys = strings(join?.[field]);
    if (keys.length) {
      blockers.push(`- Join ${field}: ${keys.map(code).join(', ')}`);
    }
  }
  return blockers;
}

interface CurrentDeliveryIdentity {
  generation: number;
  headSha: string;
  baseSha: string;
  targetBranch: string;
}

function currentDeliveryIdentity(
  execution: WorkflowExecution,
  integrationCandidate: Record<string, unknown> | null,
): CurrentDeliveryIdentity | null {
  const headSha = text(integrationCandidate?.candidate_sha);
  const candidateBaseSha = text(integrationCandidate?.base_sha);
  const baseSha = execution.baseSha ?? candidateBaseSha;
  const targetBranch = execution.baseBranch;
  if (!headSha || !baseSha || !targetBranch) return null;
  if (candidateBaseSha && candidateBaseSha !== baseSha) return null;
  return { generation: execution.currentGeneration, headSha, baseSha, targetBranch };
}

function waitRequestMatchesCurrent(
  wait: WorkflowWait,
  identity: CurrentDeliveryIdentity | null,
): boolean {
  return Boolean(
    identity &&
    wait.generation === identity.generation &&
    wait.request.expectedHeadSha === identity.headSha &&
    wait.request.expectedBaseSha === identity.baseSha &&
    wait.request.expectedTargetBranch === identity.targetBranch,
  );
}

function waitObservationMatchesCurrent(
  wait: WorkflowWait,
  identity: CurrentDeliveryIdentity | null,
): boolean {
  if (!waitRequestMatchesCurrent(wait, identity) || !identity || !wait.observation) return false;
  const observation = wait.observation;
  if (
    observation.expectedHeadSha !== identity.headSha ||
    observation.expectedBaseSha !== identity.baseSha ||
    observation.expectedTargetBranch !== identity.targetBranch
  )
    return false;
  if (
    observation.candidate &&
    (observation.candidate.candidate_sha !== identity.headSha ||
      observation.candidate.tested_base_sha !== identity.baseSha ||
      observation.candidate.current_target_sha !== identity.baseSha ||
      observation.candidate.target_branch !== identity.targetBranch)
  )
    return false;
  if (
    observation.checks &&
    (observation.checks.candidate_sha !== identity.headSha ||
      observation.checks.tested_base_sha !== identity.baseSha)
  )
    return false;
  if (
    observation.mergeReceipt &&
    (observation.mergeReceipt.source_sha !== identity.headSha ||
      observation.mergeReceipt.base_sha !== identity.baseSha ||
      observation.mergeReceipt.target_branch !== identity.targetBranch)
  )
    return false;
  return true;
}

function executionMergeMatchesCurrent(
  receipt: Record<string, unknown> | null,
  identity: CurrentDeliveryIdentity | null,
): boolean {
  if (!receipt || !identity || text(receipt.state)?.toLowerCase() !== 'merged') return false;
  return (
    (text(receipt.source_sha) ?? text(receipt.sourceSha)) === identity.headSha &&
    (text(receipt.base_sha) ?? text(receipt.baseSha)) === identity.baseSha &&
    (text(receipt.target_branch) ?? text(receipt.targetBranch)) === identity.targetBranch
  );
}

function deliveryWaitLines(
  waits: readonly WorkflowWait[],
  identity: CurrentDeliveryIdentity | null,
  currentGeneration: number,
): string[] {
  const lines = ['## Remote delivery observations', ''];
  if (!waits.length) {
    lines.push('No remote delivery wait has been recorded.', '');
    return lines;
  }
  for (const wait of waits) {
    const requestIsCurrent = waitRequestMatchesCurrent(wait, identity);
    const observationIsCurrent = waitObservationMatchesCurrent(wait, identity);
    const scopeLabel =
      wait.generation !== currentGeneration
        ? ' · Historical generation'
        : !requestIsCurrent || (wait.observation && !observationIsCurrent)
          ? ' · Non-current identity'
          : '';
    const observation = wait.observation;
    const checkReceipt = observation?.checks;
    const mergeReceipt = observation?.mergeReceipt;
    const candidate = observation?.candidate;
    const provider = checkReceipt?.provider ?? mergeReceipt?.provider ?? candidate?.provider;
    const remoteChecks = checkReceipt?.checks ?? candidate?.checks ?? [];
    lines.push(
      `### ${wait.mode === 'checks' ? 'Remote verification' : 'Merge operation'} · review #${wait.request.reviewNumber}${scopeLabel}`,
      '',
      '| Field | Value |',
      '| --- | --- |',
      `| Provider | ${provider ? code(provider) : 'Not reported'} |`,
      `| Durable wait state | ${code(wait.state)} |`,
      `| Provider observation | ${observation ? code(observation.status) : 'Not observed'} |`,
      `| Generation | ${wait.generation} |`,
      `| Expected head | ${shortCode(wait.request.expectedHeadSha)} |`,
      `| Expected base | ${shortCode(wait.request.expectedBaseSha)} |`,
      `| Target branch | ${code(wait.request.expectedTargetBranch)} |`,
      `| Provider operation | ${code(wait.request.providerOperationId)} |`,
      `| Attempts | ${wait.attemptCount} |`,
      `| Last observation | ${observation ? escapeCell(observation.observedAt) : 'Not observed'} |`,
      '',
    );
    if (scopeLabel) {
      lines.push(
        'This observation is retained as history and does not establish the current generation’s delivery state.',
        '',
      );
    }
    if (candidate) {
      lines.push(
        `Observed candidate ${shortCode(candidate.candidate_sha)} against target ${shortCode(candidate.current_target_sha)}; provider reported mergeable: **${candidate.mergeable ? 'yes' : 'no'}**.`,
        '',
      );
    }
    if (remoteChecks.length) {
      lines.push(
        '| Remote verification | State | Details |',
        '| --- | --- | --- |',
        ...remoteChecks.map(
          (check) =>
            `| ${escapeCell(check.name)} | ${code(check.conclusion)} | ${check.details_url ? link('Open authoritative details', check.details_url) : '—'} |`,
        ),
        '',
      );
      if (checkReceipt) {
        lines.push(
          `Remote verification receipt signature: **${signatureState(checkReceipt)}**.`,
          '',
        );
      }
    }
    if (mergeReceipt) {
      lines.push(
        `Merge receipt state: **${markdownText(mergeReceipt.state)}** · method ${code(mergeReceipt.method)} · result ${shortCode(mergeReceipt.result_sha)} · canonical target ${shortCode(mergeReceipt.canonical_target_sha)}.`,
        `Merge receipt provider operation: ${code(mergeReceipt.provider_operation_id)} · signature: **${signatureState(mergeReceipt)}**.`,
        '',
      );
    }
    if (observation?.reason)
      lines.push(`Provider reason: ${markdownText(observation.reason)}.`, '');
    if (wait.lastError) lines.push(`Last observer error: ${markdownText(wait.lastError)}.`, '');
  }
  return lines;
}

function deliveryStatusLines(
  waits: readonly WorkflowWait[],
  identity: CurrentDeliveryIdentity | null,
  executionMergeReceipt: Record<string, unknown> | null,
): string[] {
  const currentRequests = waits.filter((wait) => waitRequestMatchesCurrent(wait, identity));
  const currentObservations = currentRequests.filter((wait) =>
    waitObservationMatchesCurrent(wait, identity),
  );
  const publicationObserved = currentObservations.some(
    (wait) =>
      wait.observation?.status !== 'stale_candidate' &&
      Boolean(
        wait.observation?.candidate || wait.observation?.checks || wait.observation?.mergeReceipt,
      ),
  );
  const checksPassed = currentObservations.some(
    (wait) => wait.observation?.status === 'checks_passed' && Boolean(wait.observation.checks),
  );
  const mergeObserved = currentObservations.some(
    (wait) =>
      wait.observation?.status === 'merged' &&
      wait.observation.mergeReceipt?.state.toLowerCase() === 'merged',
  );
  const mergeEstablished =
    mergeObserved || executionMergeMatchesCurrent(executionMergeReceipt, identity);
  const statuses = currentRequests
    .map((wait) => wait.observation?.status)
    .filter((status): status is NonNullable<typeof status> => Boolean(status));
  const has = (status: (typeof statuses)[number]) => statuses.includes(status);
  const lines = [
    publicationObserved
      ? 'Remote review/publication was observed for the current integration candidate.'
      : has('stale_candidate')
        ? 'The provider reported a stale candidate; remote review/publication is not established for the current integration candidate.'
        : 'Remote review/publication has not been observed for the current integration candidate.',
  ];
  if (checksPassed) {
    lines.push(
      'Remote CI success is established by a matching current-candidate `checks_passed` observation and verification receipt.',
    );
  } else if (has('checks_failed')) {
    lines.push('Remote CI failed for the current delivery request; CI success is not established.');
  } else if (has('checks_pending')) {
    lines.push(
      'Remote CI is pending for the current delivery request; CI success is not established.',
    );
  } else if (has('stale_candidate')) {
    lines.push('Remote CI success is not established because the provider candidate is stale.');
  } else {
    lines.push('Remote CI success is not established for the current integration candidate.');
  }
  if (mergeEstablished) {
    lines.push('Merge is established by an authoritative receipt matching the current candidate.');
  } else if (has('merge_failed')) {
    lines.push('The current merge operation failed; merge is not established.');
  } else if (has('merge_pending')) {
    lines.push('The current merge operation is pending; merge is not established.');
  } else if (has('stale_candidate')) {
    lines.push('Merge is not established because the provider candidate is stale.');
  } else {
    lines.push('Merge is not established for the current integration candidate.');
  }
  return lines;
}

/** Build a portable Markdown report from the public execution and evidence projections. */
export function buildWorkflowExecutionResultsMarkdown(
  execution: WorkflowExecution,
  evidence: Record<string, unknown>,
  deliveryWaits: readonly WorkflowWait[] = [],
): string {
  const verification = record(evidence.verification);
  const verificationStatus = text(verification?.status) ?? 'not reported';
  const workflowDigest = text(evidence.workflowDigest);
  const integrationReceipts = execution.integrationReceipts ?? [];
  const integrationCandidate = record(execution.integrationCandidate);
  const integrationReview = execution.integrationReviewReceipt;
  const mergeReceipt = record(execution.mergeReceipt);
  const deliveryIdentity = currentDeliveryIdentity(execution, integrationCandidate);
  const { current: currentChildren, historical: historicalChildren } =
    currentAndHistoricalChildren(execution);
  const blockers = blockerLines(execution, currentChildren, verification);
  const lines = [
    `# ${markdownText(execution.name)}`,
    '',
    `> **${markdownText(execution.state.toUpperCase())}** · Current child evidence status reported by the server: **${markdownText(verificationStatus)}**`,
    '',
    '## Run context',
    '',
    '| Field | Value |',
    '| --- | --- |',
    `| Execution | ${code(execution.executionId)} |`,
    `| Plan revision | ${code(execution.planRevision)} |`,
    `| Generation | ${execution.currentGeneration} |`,
    `| Repository | ${link('Open repository', execution.repo)} |`,
    `| Base branch | ${code(execution.baseBranch ?? text(evidence.baseRef))} |`,
    `| Base commit | ${code(execution.baseSha ?? text(evidence.baseSha))} |`,
    `| Workflow digest | ${code(workflowDigest)} |`,
    `| Updated | ${escapeCell(execution.updatedAt || '—')} |`,
    '',
    'This view reports the platform response as received. “Accepted by server” means the server accepted the evidence envelope. “Present” means a receipt contains a signature; this browser view does not independently verify its cryptography.',
    '',
    '## Current workstreams',
    '',
    '| Workstream | Generation | Attempt | State | Candidate | Verification receipts | Reviews | Evidence |',
    '| --- | ---: | ---: | --- | --- | ---: | ---: | --- |',
    ...(currentChildren.length
      ? currentChildren.map(workstreamSummary)
      : ['No current workstream attempts were reported.']),
    '',
    ...currentChildren.flatMap(workstreamDetails),
  ];

  if (historicalChildren.length) {
    lines.push(
      '## Historical attempts',
      '',
      'These attempts are retained for chronology and are excluded from the current child evidence status above.',
      '',
      '| Workstream | Generation | Attempt | State | Candidate |',
      '| --- | ---: | ---: | --- | --- |',
      ...historicalChildren.map(
        (child) =>
          `| ${escapeCell(child.childKey)} | ${child.generation ?? '—'} | ${child.attempt} | ${escapeCell(child.state)} | ${shortCode(child.candidate?.candidateSha)} |`,
      ),
      '',
    );
  }

  lines.push(
    '## Blocking and pending',
    '',
    ...(blockers.length
      ? blockers
      : [
          'No blocking reasons, child errors, questions, gates, or join blockers were included in the platform response.',
        ]),
    '',
    '## Integration',
    '',
  );

  if (integrationCandidate) {
    lines.push(
      `Integrated candidate: ${shortCode(text(integrationCandidate.candidate_sha))} · tree ${shortCode(text(integrationCandidate.candidate_tree))}`,
      '',
      `- Integration receipts recorded: **${integrationReceipts.length}**`,
      `- Integration review: **${markdownText(integrationReview?.verdict ?? 'not recorded')}**`,
      `- Integration review signature: **${integrationReview ? signatureState(integrationReview) : 'Absent'}**`,
      '',
    );
  } else {
    lines.push('No integrated candidate has been recorded.', '');
  }

  lines.push(...deliveryWaitLines(deliveryWaits, deliveryIdentity, execution.currentGeneration));
  lines.push('## Publication', '');
  if (mergeReceipt) {
    lines.push(
      `- Publication state: **${markdownText(text(mergeReceipt.state) ?? 'recorded')}**`,
      `- Result commit: ${shortCode(text(mergeReceipt.result_sha) ?? text(mergeReceipt.resultSha))}`,
      `- Receipt signature: **${record(mergeReceipt.provenance)?.signature ? 'Present' : 'Absent'}**`,
      '',
    );
  } else {
    lines.push('No merge receipt has been recorded.', '');
  }

  const limitations: string[] = [];
  if (execution.state !== 'completed') {
    limitations.push(
      `The workflow is **${markdownText(execution.state)}**; this is an interim report.`,
    );
  }
  if (!integrationCandidate) limitations.push('Integration has not produced a candidate.');
  limitations.push(...deliveryStatusLines(deliveryWaits, deliveryIdentity, mergeReceipt));
  limitations.push(
    `Budget reports ${execution.budget.availableUnits} available, ${execution.budget.reservedUnits} reserved, and ${execution.budget.spentUnits} spent units.`,
  );
  if (execution.suspensionReason) {
    limitations.push(`Latest workflow reason: ${markdownText(execution.suspensionReason)}.`);
  }
  lines.push('## Pending and limits', '', ...limitations.map((item) => `- ${item}`), '');
  return lines.join('\n');
}

/** Provider-neutral projections of durable developer workflow executions. */
export type DeveloperExecutionState =
  'pending' | 'running' | 'waiting' | 'blocked' | 'canceling' | 'canceled' | 'completed' | 'failed';

export interface DeveloperReceiptProvenance {
  producer_id?: string;
  key_id?: string;
  signature?: string;
}

export interface DeveloperVerificationReceipt extends Record<string, unknown> {
  receipt_id?: string;
  contract_id?: string;
  exit_code?: number;
  candidate_sha?: string;
  completed_at?: string;
  provenance?: DeveloperReceiptProvenance;
}

export interface DeveloperReviewReceipt extends Record<string, unknown> {
  receipt_id?: string;
  role?: string;
  verdict?: string;
  reviewer_id?: string;
  candidate_sha?: string;
  findings?: unknown[];
  provenance?: DeveloperReceiptProvenance;
}

export interface DeveloperCandidate extends Record<string, unknown> {
  attemptId?: string;
  candidateSha?: string;
  candidateTree?: string;
  verificationReceipts?: DeveloperVerificationReceipt[];
  reviewReceipts?: DeveloperReviewReceipt[];
  requirementEvidence?: {
    requirement_id?: string;
    implementation_paths?: string[];
    verification_contract_ids?: string[];
  }[];
}

export interface DeveloperEvidenceValidation {
  accepted: boolean;
  manifest_digest?: string;
  blocking_reasons?: string[];
}

export type DeveloperDeliveryWaitMode = 'checks' | 'merge';
export type DeveloperDeliveryWaitState = 'pending' | 'ready' | 'failed' | 'notified';
export type DeveloperDeliveryObservationStatus =
  | 'checks_pending'
  | 'checks_passed'
  | 'checks_failed'
  | 'merge_pending'
  | 'merged'
  | 'merge_failed'
  | 'stale_candidate';

export interface DeveloperRemoteCheck {
  name: string;
  conclusion: 'passing' | 'failing' | 'pending' | 'canceled' | 'skipped' | 'unknown';
  details_url?: string | null;
}

export interface DeveloperCheckReceipt {
  receipt_id: string;
  provider: string;
  repository: string;
  review_number: number;
  candidate_sha: string;
  tested_base_sha: string;
  checks: DeveloperRemoteCheck[];
  observed_at: string;
  provenance?: DeveloperReceiptProvenance | null;
}

export interface DeveloperReviewCandidate {
  provider: string;
  repository: string;
  review_number: number;
  source_branch: string;
  target_branch: string;
  candidate_sha: string;
  tested_base_sha: string;
  current_target_sha: string;
  mergeable: boolean;
  checks: DeveloperRemoteCheck[];
  serialized_publication: boolean;
}

export interface DeveloperMergeReceipt extends Record<string, unknown> {
  receipt_id: string;
  provider: string;
  repository: string;
  review_number: number;
  source_sha: string;
  base_sha: string;
  target_branch: string;
  result_sha?: string | null;
  canonical_target_sha?: string | null;
  method: string;
  state: string;
  provider_operation_id?: string | null;
  verified_at?: string | null;
  provenance?: DeveloperReceiptProvenance | null;
}

export interface DeveloperDeliveryWaitRequest {
  repository: string;
  reviewNumber: number;
  expectedHeadSha: string;
  expectedBaseSha: string;
  expectedTargetBranch: string;
  policyId: string;
  method?: string | null;
  providerOperationId?: string;
}

export interface DeveloperDeliveryObservation {
  status: DeveloperDeliveryObservationStatus;
  repository: string;
  reviewNumber: number;
  expectedHeadSha: string;
  expectedBaseSha: string;
  expectedTargetBranch: string;
  observedAt: string;
  reason: string;
  candidate?: DeveloperReviewCandidate | null;
  checks?: DeveloperCheckReceipt | null;
  mergeReceipt?: DeveloperMergeReceipt | null;
}

export interface DeveloperDeliveryWait {
  waitId: string;
  executionId: string;
  mode: DeveloperDeliveryWaitMode;
  state: DeveloperDeliveryWaitState;
  requestDigest: string;
  generation: number;
  executionRevision: number;
  candidateDigest: string;
  request: DeveloperDeliveryWaitRequest;
  nextPollAt: string;
  attemptCount: number;
  lastError: string;
  observation?: DeveloperDeliveryObservation | null;
}

export interface DeveloperChildExecution {
  childId: string;
  childKey: string;
  attempt: number;
  generation?: number;
  state: string;
  dependencies: string[];
  requirementIds?: string[];
  taskHandle: { agentId: string; taskId: string; contextId?: string } | null;
  workspace: Record<string, unknown> | null;
  candidate?: DeveloperCandidate | null;
  evidence?: Record<string, unknown>[];
  evidenceValidation?: DeveloperEvidenceValidation | null;
  error: { kind: string | null; detail: string } | null;
  pendingQuestions?: {
    requestId: string;
    persona: string;
    question: string;
    reason: string;
    recommendation: string;
    attempted: string[];
  }[];
  pendingGates?: {
    gateId: string;
    nodeId: string;
    label: string;
    condition: string;
    instructions: string;
    summary: string;
  }[];
}

export interface DeveloperExecution {
  executionId: string;
  name: string;
  prompt: string;
  repo: string;
  baseBranch?: string;
  baseSha?: string;
  workflowId: string;
  state: DeveloperExecutionState;
  suspensionReason: string;
  currentGeneration: number;
  planRevision?: string;
  budget: { totalUnits: number; reservedUnits: number; spentUnits: number; availableUnits: number };
  join: Record<string, unknown> | null;
  children: DeveloperChildExecution[];
  createdAt: string;
  updatedAt: string;
  deadline?: string;
  integrationAllocation?: Record<string, unknown> | null;
  integrationCandidate?: Record<string, unknown> | null;
  integrationReceipts?: Record<string, unknown>[];
  integrationReviewReceipt?: DeveloperReviewReceipt | null;
  mergeReceipt?: Record<string, unknown> | null;
  completedAt?: string | null;
}

export interface DeveloperExecutionLaunch {
  workflowId: string;
  prompt: string;
  repo: string;
  baseBranch: string;
  name?: string;
  model?: string;
  connectionId?: string;
  budgetUnits?: number;
  deadline?: string;
}

export function executionIsTerminal(state: DeveloperExecutionState): boolean {
  return state === 'completed' || state === 'canceled' || state === 'failed';
}

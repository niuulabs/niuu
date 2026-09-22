/** Provider-neutral projections of durable developer workflow executions. */
export type WorkflowExecutionState =
  'pending' | 'running' | 'waiting' | 'blocked' | 'canceling' | 'canceled' | 'completed' | 'failed';

export interface DeliveryReceiptProvenance {
  producer_id?: string;
  key_id?: string;
  signature?: string;
}

export interface DeliveryVerificationReceipt extends Record<string, unknown> {
  receipt_id?: string;
  contract_id?: string;
  exit_code?: number;
  candidate_sha?: string;
  completed_at?: string;
  provenance?: DeliveryReceiptProvenance;
}

export interface AttestedReviewReceipt extends Record<string, unknown> {
  receipt_id?: string;
  role?: string;
  verdict?: string;
  reviewer_id?: string;
  candidate_sha?: string;
  findings?: unknown[];
  provenance?: DeliveryReceiptProvenance;
}

export interface DeliveryCandidate extends Record<string, unknown> {
  attemptId?: string;
  candidateSha?: string;
  candidateTree?: string;
  verificationReceipts?: DeliveryVerificationReceipt[];
  reviewReceipts?: AttestedReviewReceipt[];
  requirementEvidence?: {
    requirement_id?: string;
    implementation_paths?: string[];
    verification_contract_ids?: string[];
  }[];
}

export interface ChildEvidenceValidation {
  accepted: boolean;
  manifest_digest?: string;
  blocking_reasons?: string[];
}

export type WorkflowWaitState = 'pending' | 'ready' | 'failed' | 'notified';
/** Generic wait outcome. What a status means is owned by the wait's own conditionType. */
export type WaitObservationStatus = 'pending' | 'satisfied' | 'failed';

export interface DeliveryRemoteCheck {
  name: string;
  conclusion: 'passing' | 'failing' | 'pending' | 'canceled' | 'skipped' | 'unknown';
  details_url?: string | null;
}

export interface DeliveryCheckReceipt {
  receipt_id: string;
  provider: string;
  repository: string;
  review_number: number;
  candidate_sha: string;
  tested_base_sha: string;
  checks: DeliveryRemoteCheck[];
  observed_at: string;
  provenance?: DeliveryReceiptProvenance | null;
}

export interface AttestedReviewCandidate {
  provider: string;
  repository: string;
  review_number: number;
  source_branch: string;
  target_branch: string;
  candidate_sha: string;
  tested_base_sha: string;
  current_target_sha: string;
  mergeable: boolean;
  checks: DeliveryRemoteCheck[];
  serialized_publication: boolean;
}

export interface DeliveryMergeReceipt extends Record<string, unknown> {
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
  provenance?: DeliveryReceiptProvenance | null;
}

/**
 * Condition-specific identity a wait was registered with. Opaque to the generic
 * engine; a `forge.checks` or `forge.merge` wait carries repository, reviewNumber,
 * expectedHeadSha, expectedBaseSha, expectedTargetBranch, policyId, and (for
 * forge.merge) method and providerOperationId. Other condition types (timer, ...)
 * carry whatever their own observer requires.
 */
export type WaitRequest = Record<string, unknown>;

/**
 * Condition-specific detail explaining an observation. A `forge.checks` wait's
 * detail carries `candidate` and `checks`; a `forge.merge` wait's carries
 * `mergeReceipt`. Other condition types carry whatever their own observer reports.
 */
export interface WaitObservation {
  status: WaitObservationStatus;
  observedAt: string;
  reason: string;
  detail: Record<string, unknown>;
}

export interface WorkflowWait {
  waitId: string;
  executionId: string;
  nodeId: string;
  conditionType: string;
  state: WorkflowWaitState;
  requestDigest: string;
  generation: number;
  executionRevision: number;
  request: WaitRequest;
  nextPollAt: string;
  attemptCount: number;
  lastError: string;
  observation?: WaitObservation | null;
}

/**
 * Pending question/gate shapes are common to every child, whichever pack
 * layers evidence on top of the durable parent/child engine.
 */
interface PendingChildQuestion {
  requestId: string;
  persona: string;
  question: string;
  reason: string;
  recommendation: string;
  attempted: string[];
}

interface PendingChildGate {
  gateId: string;
  nodeId: string;
  label: string;
  condition: string;
  instructions: string;
  summary: string;
}

/** A domain-neutral child of a durable workflow execution. */
export interface WorkflowChildExecution {
  childId: string;
  childKey: string;
  attempt: number;
  generation?: number;
  state: string;
  /** Name of the node's own `templates` entry this child was stamped from. */
  template?: string;
  dependencies: string[];
  input: Record<string, unknown>;
  taskHandle: { agentId: string; taskId: string; contextId?: string } | null;
  result: Record<string, unknown> | null;
  artifacts: Record<string, unknown>[];
  resultValidation?: ChildEvidenceValidation | null;
  resultValidatedAt?: string | null;
  error: { kind: string | null; detail: string } | null;
  pendingQuestions?: PendingChildQuestion[];
  pendingGates?: PendingChildGate[];
  deadline?: string;
  createdAt?: string;
  updatedAt?: string;
}

/**
 * The code-delivery specialization of a child execution (a workstream). Same
 * durable identity as `WorkflowChildExecution`, with its generic `result` /
 * `artifacts` / `resultValidation` replaced by the delivery pack's own
 * `candidate` / `evidence` / `evidenceValidation` vocabulary, plus the
 * workspace and requirement fields only the delivery pack tracks.
 */
export interface DeliveryChildExecution extends Omit<
  WorkflowChildExecution,
  'input' | 'result' | 'artifacts' | 'resultValidation' | 'resultValidatedAt'
> {
  requirementIds?: string[];
  workspace: Record<string, unknown> | null;
  candidate?: DeliveryCandidate | null;
  evidence?: Record<string, unknown>[];
  evidenceValidation?: ChildEvidenceValidation | null;
  evidenceValidatedAt?: string | null;
}

/**
 * A domain-neutral durable workflow execution — fan-out/join, durable waits,
 * and session continuation for any workflow that expands into a bounded
 * child DAG. No git or delivery vocabulary belongs here; a pack that needs
 * more (code delivery today) layers its own type over this one, the same way
 * its API layers its own router over the generic one.
 */
export interface WorkflowExecution {
  executionId: string;
  name: string;
  prompt: string;
  workflowId: string;
  input: Record<string, unknown>;
  state: WorkflowExecutionState;
  suspensionReason: string;
  parentStopRequestedAt?: string | null;
  parentStoppedAt?: string | null;
  currentGeneration: number;
  planRevision?: string | null;
  budget: { totalUnits: number; reservedUnits: number; spentUnits: number; availableUnits: number };
  join: Record<string, unknown> | null;
  children: WorkflowChildExecution[];
  createdAt: string;
  updatedAt: string;
  deadline?: string;
  completedAt?: string | null;
  /**
   * The expanding node's own named child-workflow templates, keyed by name,
   * each with its exact pin and the child workflow's own one-line
   * description — see `templates` on the `kind: subworkflow` node.
   */
  templates?: Record<
    string,
    { templateId: string; templateRevision: string; templateDigest: string; description: string }
  >;
}

/**
 * The code-delivery specialization of a workflow execution. Everything a
 * generic execution has, plus the git identity and integration/publication
 * state only the delivery pack's own API surface reports.
 */
export interface DeliveryExecution extends Omit<WorkflowExecution, 'children'> {
  children: DeliveryChildExecution[];
  repo: string;
  baseBranch: string;
  baseSha: string;
  integrationAllocation?: Record<string, unknown> | null;
  integrationCandidate?: Record<string, unknown> | null;
  integrationReceipts?: Record<string, unknown>[];
  integrationReviewReceipt?: AttestedReviewReceipt | null;
  mergeReceipt?: Record<string, unknown> | null;
}

/** Launch a generic durable workflow execution — no git identity involved. */
export interface WorkflowExecutionLaunch {
  workflowId: string;
  /** Exact immutable workflow version to expand; omission resolves the current head. */
  workflowVersion?: string;
  /** The `kind: subworkflow` node in the workflow graph this execution expands. */
  parentNodeId: string;
  prompt: string;
  input?: Record<string, unknown>;
  name?: string;
  model?: string;
  connectionId?: string;
  budgetUnits?: number;
  deadline?: string;
}

/** Launch a code-delivery execution — bound to a repository and base branch. */
export interface DeliveryExecutionLaunch {
  workflowId: string;
  /** Exact immutable workflow version to expand; omission resolves the current head. */
  workflowVersion?: string;
  /** The `kind: subworkflow` node in the workflow graph this execution expands. */
  parentNodeId: string;
  prompt: string;
  repo: string;
  baseBranch: string;
  name?: string;
  model?: string;
  connectionId?: string;
  budgetUnits?: number;
  deadline?: string;
}

export function executionIsTerminal(state: WorkflowExecutionState): boolean {
  return state === 'completed' || state === 'canceled' || state === 'failed';
}

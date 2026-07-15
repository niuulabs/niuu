import type {
  AutonomyMode,
  DecisionDetail,
  DecisionRecord,
  HistoryPage,
  HuddleMessage,
  HuddleSummary,
  LearnedSkillRecord,
  LearnedSkillSummary,
  LearningFeedbackVerdict,
  LearningRecord,
  LearningScope,
  RealmSummary,
  RealmTrustGrant,
  ReviewItem,
  ReviewKind,
  ReviewRiskClass,
  ReviewStatus,
  ReviewSummary,
  SignalHistoryEntry,
  SkillUsageStat,
  TingWorkflowSummary,
  ValkyrieDashboard,
} from './domain';

export interface AutonomyUpdateRequest {
  valkyrieId: string;
  mode: AutonomyMode;
  reason: string;
  participantId?: string;
}

export interface DecisionListFilters {
  environmentId?: string;
  valkyrieId?: string;
  operationalState?: string;
  limit?: number;
  offset?: number;
}

export interface SignalHistoryFilters {
  environmentId?: string;
  severity?: string;
  limit?: number;
  offset?: number;
}

/** Body for POST /huddles/{id}/join — mirrors the backend HuddleJoinRequest. */
export interface HuddleJoinInput {
  huddleId: string;
  participantId: string;
  displayName?: string;
  action?: string;
  targetFlockId?: string;
}

/**
 * Body for POST /huddles/{id}/messages. `directedTo` names peer participant
 * ids — the backend bridges those as direct messages via the Skuld room.
 */
export interface HuddleMessageInput {
  huddleId: string;
  body: string;
  directedTo?: string[];
  authorId: string;
}

/**
 * Body for POST /learnings/{id}/feedback. `targetScope` is required for the
 * `wrong_tier` verdict (adjacent scope only) and ignored for the others.
 */
export interface LearningFeedbackInput {
  learningId: string;
  verdict: LearningFeedbackVerdict;
  reason?: string;
  operatorId: string;
  targetScope?: LearningScope;
}

/**
 * Body for POST /learnings/{id}/revise — at least one of title / summary /
 * content must be present alongside the required reason.
 */
export interface LearningRevisionInput {
  learningId: string;
  title?: string;
  summary?: string;
  content?: string;
  reason: string;
  operatorId: string;
}

/**
 * Result of a revision. For adopted/canary learnings `learning` is a NEW
 * superseding candidate (`<old>:revN`, `supersedes` set) and `supersededId`
 * names the original, which stays installed until the candidate passes
 * review. For plain candidates the record is updated in place and
 * `supersededId` is ''.
 */
export interface LearningRevisionResult {
  learning: LearningRecord;
  supersededId: string;
}

export interface IValkyrieService {
  getDashboard(): Promise<ValkyrieDashboard>;
  updateAutonomy(request: AutonomyUpdateRequest): Promise<ValkyrieDashboard>;
  joinHuddle(request: HuddleJoinInput): Promise<HuddleSummary>;
  leaveHuddle(huddleId: string): Promise<HuddleSummary>;
  sendHuddleMessage(request: HuddleMessageInput): Promise<HuddleMessage>;
  listDecisions(filters?: DecisionListFilters): Promise<HistoryPage<DecisionRecord>>;
  getDecision(decisionId: string): Promise<DecisionDetail | null>;
  listSignalHistory(filters?: SignalHistoryFilters): Promise<HistoryPage<SignalHistoryEntry>>;
  getSkillStats(environmentId?: string): Promise<SkillUsageStat[]>;
  /** Full learning record; null when unknown (404). */
  getLearning(learningId: string): Promise<LearningRecord | null>;
  sendLearningFeedback(request: LearningFeedbackInput): Promise<LearningRecord>;
  reviseLearning(request: LearningRevisionInput): Promise<LearningRevisionResult>;
}

/**
 * Learned skills adopted on an environment — the artifact behind a
 * "handled with a learned skill" judgment. Served by
 * GET /skills and GET /skills/{name} on the Valkyrie API base.
 */
export interface IValkyrieSkillsService {
  listSkills(environmentId: string): Promise<LearnedSkillSummary[]>;
  /** Full record including markdown, code, and tests; null when unknown (404). */
  getSkill(environmentId: string, name: string): Promise<LearnedSkillRecord | null>;
}

export interface ReviewListFilters {
  status?: ReviewStatus | '';
  kind?: ReviewKind | '';
  environmentId?: string;
  riskClass?: ReviewRiskClass | '';
  query?: string;
  limit?: number;
  offset?: number;
}

export type ReviewSummaryFilters = Pick<
  ReviewListFilters,
  'kind' | 'environmentId' | 'riskClass' | 'query'
>;

export interface ReviewDecisionRequest {
  itemId: string;
  decision: 'approved' | 'rejected';
  reason?: string;
  participantId?: string;
}

export interface IOdinReviewService {
  listReviews(filters?: ReviewListFilters): Promise<ReviewItem[]>;
  getReview(itemId: string): Promise<ReviewItem | null>;
  decideReview(request: ReviewDecisionRequest): Promise<ReviewItem>;
  getSummary(filters?: ReviewSummaryFilters): Promise<ReviewSummary>;
}

/** Body for POST /api/v1/realms/{slug}/trust-grants (exact backend casing). */
export interface TrustGrantCreate {
  action_class: string;
  target: string;
  level: number;
  limits: Record<string, unknown>;
  granted_by?: string | null;
}

export interface IRealmGovernanceService {
  listRealms(): Promise<RealmSummary[]>;
  listTrustGrants(slug: string): Promise<RealmTrustGrant[]>;
  createTrustGrant(slug: string, request: TrustGrantCreate): Promise<RealmTrustGrant>;
  listWorkflows(): Promise<TingWorkflowSummary[]>;
}
